"""
Uki 本地 HTTP 服务器 (FastAPI)

为 Electron 桌面应用提供后端 API。
启动: uvicorn server:app --port 8765
"""

import sys
import io

# Windows 下 Electron 子进程 stdout 默认 GBK，强制 UTF-8 防止 emoji/中文崩溃
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
import queue
import json
import re
import asyncio
from pathlib import Path
import threading
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from uki.agent import UkiAgent
from uki.config import Config
from uki.commands import set_agent_ref, create_builtin_registry

app = FastAPI(title="Uki API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

agent = UkiAgent()
set_agent_ref(agent)

# 命令注册表（供前端自动补全使用）
commands = create_builtin_registry()

# 【第十五课】加载插件
agent.load_plugins()
agent.plugin_manager.register_commands(commands)

# Electron 模式下的权限确认
_confirm_event = threading.Event()
_confirm_result = False
_confirm_queue: queue.Queue | None = None  # 当前 SSE 输出队列


def _electron_permission(tool_name: str) -> bool:
    """Electron 模式的权限确认：向 UI 发送确认请求并等待用户响应"""
    global _confirm_result
    if _confirm_queue is not None:
        _confirm_queue.put(("confirm", tool_name))
        _confirm_event.wait()
        _confirm_event.clear()
        return _confirm_result
    return False

agent.permission_callback = _electron_permission


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


@app.get("/commands")
async def get_commands():
    """返回所有可用命令列表，供前端自动补全。"""
    return {"commands": commands.list_commands_as_dict()}


@app.post("/chat")
async def chat(req: ChatRequest):
    """发送消息给 Uki，流式返回 Agent 的思考过程"""
    global _confirm_queue
    q: queue.Queue = queue.Queue()
    _confirm_queue = q  # 让 permission_callback 能发确认请求到 SSE 流

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
            try:
                item = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if item is None:
                break
            if isinstance(item, tuple) and item[0] == "confirm":
                yield f"data: {json.dumps({'type': 'confirm', 'tool': item[1]}, ensure_ascii=False)}\n\n"
            else:
                clean = re.sub(r"\x1b\[[0-9;]*m", "", item)
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


class ConfirmRequest(BaseModel):
    approved: bool


class TogglePluginRequest(BaseModel):
    name: str
    enabled: bool


@app.post("/mode")
async def set_mode(req: ModeRequest):
    if req.mode in ("default", "auto", "readonly"):
        agent.set_mode(req.mode)
        return {"mode": agent.permission_mode}
    return {"error": f"未知模式: {req.mode}"}


@app.post("/confirm")
async def confirm(req: ConfirmRequest):
    """用户对权限确认的响应"""
    global _confirm_result
    _confirm_result = req.approved
    _confirm_event.set()
    return {"status": "ok"}


# ============================================================
# MCP 管理
# ============================================================

@app.get("/mcp")
async def get_mcp_tools():
    """获取当前所有 MCP 工具"""
    servers_info = []
    for srv in agent.mcp.servers:
        tools = [t["function"]["name"] for t in srv.tools]
        servers_info.append({"name": srv.name, "tools": tools})
    return {"servers": servers_info}


@app.get("/tools")
async def get_all_tools():
    """获取所有可用工具（内置 + MCP）"""
    tools = []
    for t in agent.all_tools:
        func = t["function"]
        source = "内置"
        # 检查是否是 MCP 工具
        for srv in agent.mcp.servers:
            for st in srv.tools:
                if st["function"]["name"] == func["name"]:
                    source = f"MCP ({srv.name})"
        tools.append({
            "name": func["name"],
            "description": func.get("description", "")[:80],
            "source": source,
        })
    return {"tools": tools}


@app.get("/mcp-config")
async def get_mcp_config():
    """读取 MCP 配置文件内容"""
    config_path = Path(".uki_mcp.json")
    if config_path.exists():
        return {"content": config_path.read_text(encoding="utf-8")}
    return {"content": '{\n  "servers": []\n}'}


class MCPConfigRequest(BaseModel):
    content: str


@app.post("/mcp-config")
async def save_mcp_config(req: MCPConfigRequest):
    """保存 MCP 配置文件（需要重启生效）"""
    config_path = Path(".uki_mcp.json")
    config_path.write_text(req.content, encoding="utf-8")
    return {"status": "ok"}


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
    """保存配置并即时生效。"""
    env_data = _read_env()
    updates = {}
    for f in req.fields:
        key = f.get("key", "")
        val = str(f.get("value", "")).strip()
        if not key:
            continue
        field_def = next((x for x in CONFIG_FIELDS if x["key"] == key), None)
        if field_def and field_def["type"] == "password" and "..." in val and key in env_data:
            continue
        updates[key] = val
    _write_env(updates)
    # 即时生效：重载配置 + 重建 LLM 客户端
    Config.reload()
    agent.reload_client()
    return {"status": "ok"}


# ============================================================
# 插件管理
# ============================================================

@app.get("/plugins")
async def get_plugins():
    """获取所有已发现插件的状态列表。"""
    return {"plugins": agent.plugin_manager.get_all_plugins_info()}


@app.post("/plugins/install")
async def install_plugin(file: UploadFile = File(...)):
    """
    上传并安装一个插件 zip 文件。
    保存到临时路径，调用 plugin_manager.install_from_zip()。
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return {"ok": False, "error": "只接受 .zip 文件"}

    # 保存到临时文件
    temp_path = Path(f"_uki_plugin_upload_{file.filename}")
    try:
        content = await file.read()
        temp_path.write_bytes(content)
    except Exception as e:
        return {"ok": False, "error": f"文件写入失败: {e}"}

    # 安装
    try:
        ok = agent.plugin_manager.install_from_zip(str(temp_path))
        if ok:
            agent._rebuild_tool_list()
        return {"ok": ok, "error": None if ok else "安装失败，请检查 zip 是否有效"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        # 清理临时文件
        if temp_path.exists():
            temp_path.unlink()


@app.post("/plugins/toggle")
async def toggle_plugin(req: TogglePluginRequest):
    """启用或禁用指定插件。"""
    pm = agent.plugin_manager
    if req.enabled:
        ok = pm.enable_plugin(req.name)
    else:
        ok = pm.disable_plugin(req.name)

    if ok:
        agent._rebuild_tool_list()
    return {"ok": ok, "name": req.name, "enabled": req.enabled}


@app.delete("/plugins/{name}")
async def delete_plugin(name: str):
    """彻底删除指定插件。"""
    pm = agent.plugin_manager
    ok = pm.uninstall_plugin(name)
    if ok:
        agent._rebuild_tool_list()
    return {"ok": ok, "name": name}


# ============================================================
# 文件上传
# ============================================================

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传任意文件，保存到 uploads/ 目录。"""
    safe_name = Path(file.filename).name
    dest = UPLOAD_DIR / safe_name
    counter = 1
    stem, suffix = dest.stem, dest.suffix
    while dest.exists():
        dest = UPLOAD_DIR / f"{stem}_{counter}{suffix}"
        counter += 1
    try:
        content = await file.read()
        dest.write_bytes(content)
        return {"ok": True, "path": str(dest.resolve()), "name": safe_name, "size": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/uploads")
async def list_uploads():
    """列出 uploads/ 目录中所有已上传文件。"""
    if not UPLOAD_DIR.exists():
        return {"files": []}
    files = []
    for f in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file():
            stat = f.stat()
            files.append({
                "name": f.name,
                "path": str(f.resolve()),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
    return {"files": files}


@app.delete("/uploads/{name:path}")
async def delete_upload(name: str):
    """删除 uploads/ 目录中的指定文件。"""
    # 安全检查：防止路径遍历攻击
    safe_name = Path(name).name
    target = UPLOAD_DIR / safe_name
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": "文件不存在"}
    try:
        target.unlink()
        return {"ok": True, "name": safe_name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    if not Config.is_ready():
        print("请先在 .env 中配置 API key")
        sys.exit(1)
    uvicorn.run(app, host="127.0.0.1", port=8765)
