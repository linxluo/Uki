"""
Uki 本地 HTTP 服务器 (FastAPI)

为 Electron 桌面应用提供后端 API。
启动: uvicorn server:app --port 8765
"""

import sys
import io
import queue
import json
import re
from pathlib import Path
import threading
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from uki.agent import UkiAgent
from uki.config import Config
from uki.commands import set_agent_ref

app = FastAPI(title="Uki API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

agent = UkiAgent()
agent.set_mode("auto")  # Electron 模式默认自动执行
set_agent_ref(agent)


class ChatRequest(BaseModel):
    message: str


class _QueueStream:
    """将 stdout 内容重定向到队列，用于流式输出"""
    def __init__(self, q: queue.Queue):
        self.q = q
        self._buf = ""

    def write(self, text: str):
        self._buf += text
        if "\n" in self._buf:
            lines = self._buf.split("\n")
            self._buf = lines.pop()
            for line in lines:
                stripped = line.strip()
                if stripped:
                    self.q.put(stripped)

    def flush(self):
        if self._buf.strip():
            self.q.put(self._buf)
            self._buf = ""


@app.post("/chat")
async def chat(req: ChatRequest):
    """发送消息给 Uki，流式返回 Agent 的思考过程"""
    q: queue.Queue = queue.Queue()

    def _run():
        old = sys.stdout
        sys.stdout = _QueueStream(q)
        try:
            agent.run(req.message)
        except Exception as e:
            q.put(f"[错误] {e}")
        finally:
            sys.stdout = old
            q.put(None)

    threading.Thread(target=_run, daemon=True).start()

    async def generate():
        while True:
            line = q.get()
            if line is None:
                break
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
            yield f"data: {json.dumps(clean, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok", "model": Config.model}


@app.get("/mode")
async def get_mode():
    return {"mode": agent.permission_mode}


class ModeRequest(BaseModel):
    mode: str


@app.post("/mode")
async def set_mode(req: ModeRequest):
    if req.mode in ("default", "auto", "readonly"):
        agent.set_mode(req.mode)
        return {"mode": agent.permission_mode}
    return {"error": f"未知模式: {req.mode}"}


# ============================================================
# 配置读写
# ============================================================

ENV_PATH = Path(__file__).parent / ".env"

CONFIG_FIELDS = [
    {"key": "OPENAI_API_KEY",    "label": "API Key",         "type": "password"},
    {"key": "OPENAI_BASE_URL",   "label": "接口地址",         "type": "text"},
    {"key": "OPENAI_MODEL",      "label": "模型",            "type": "text"},
    {"key": "UKI_CONTEXT_WINDOW","label": "上下文窗口(token)", "type": "number"},
    {"key": "UKI_CONTEXT_MAX",   "label": "推荐上限(token)",  "type": "number"},
    {"key": "UKI_CONTEXT_TRIM",  "label": "裁剪阈值(token)",  "type": "number"},
    {"key": "UKI_SUMMARY_THRESHOLD", "label": "总结阈值(token)", "type": "number"},
]


def _mask_key(key: str) -> str:
    """遮蔽 API key 中间部分：sk-abc...xyz"""
    if not key or len(key) < 12:
        return key
    return key[:6] + "..." + key[-4:]


def _read_env() -> dict[str, str]:
    """读取 .env 文件，返回键值对"""
    result = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


def _write_env(data: dict[str, str]):
    """写入 .env 文件，保留原有注释结构"""
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in data:
                new_lines.append(f"{k}={data[k]}")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in data.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@app.get("/config")
async def get_config():
    """获取当前配置（API key 部分遮蔽）"""
    env_data = _read_env()
    fields = []
    for f in CONFIG_FIELDS:
        val = env_data.get(f["key"], "")
        display_val = _mask_key(val) if f["type"] == "password" and val else val
        fields.append({**f, "value": display_val, "has_value": bool(val)})
    return {"fields": fields}


class SaveConfigRequest(BaseModel):
    fields: list[dict]


@app.post("/config")
async def save_config(req: SaveConfigRequest):
    """保存配置。未修改的密码字段不会覆盖原有值。"""
    env_data = _read_env()
    updates = {}
    for f in req.fields:
        key = f.get("key", "")
        val = str(f.get("value", "")).strip()
        if not key:
            continue
        # 如果是密码字段且值已被遮蔽（含 ...），则保留原值
        field_def = next((x for x in CONFIG_FIELDS if x["key"] == key), None)
        if field_def and field_def["type"] == "password" and "..." in val and key in env_data:
            continue
        updates[key] = val
    _write_env(updates)
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    if not Config.is_ready():
        print("请先在 .env 中配置 API key")
        sys.exit(1)
    uvicorn.run(app, host="127.0.0.1", port=8765)
