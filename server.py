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
import threading
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from uki.agent import UkiAgent
from uki.config import Config

app = FastAPI(title="Uki API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

agent = UkiAgent()


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


if __name__ == "__main__":
    import uvicorn
    if not Config.is_ready():
        print("请先在 .env 中配置 API key")
        sys.exit(1)
    uvicorn.run(app, host="127.0.0.1", port=8765)
