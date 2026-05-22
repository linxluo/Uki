"""
Uki 桌面窗口

基于 Tkinter 的轻量 GUI，替代命令行交互。
运行方式: python gui.py
"""

import sys
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext

from uki.agent import UkiAgent
from uki.config import Config


class _QueueStream:
    """自定义输出流，将 print 内容逐行推入队列"""

    def __init__(self, q: queue.Queue):
        self.q = q
        self._buffer = ""

    def write(self, text: str):
        self._buffer += text
        if "\n" in self._buffer:
            lines = self._buffer.split("\n")
            self._buffer = lines.pop()
            for line in lines:
                if line.strip():
                    self.q.put(line)

    def flush(self):
        if self._buffer.strip():
            self.q.put(self._buffer)
            self._buffer = ""


class UkiGUI:
    def __init__(self):
        self.agent = UkiAgent()
        self.output_queue = queue.Queue()
        self._setup_ui()
        self._poll_queue()

    def _setup_ui(self):
        self.root = tk.Tk()
        self.root.title("Uki")
        self.root.geometry("750x550")
        self.root.configure(bg="#1e1e1e")

        # 输出区域
        self.output = scrolledtext.ScrolledText(
            self.root,
            state="disabled",
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            font=("Consolas", 11),
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        # 输入区域
        input_frame = tk.Frame(self.root, bg="#1e1e1e")
        self.entry = tk.Entry(
            input_frame,
            bg="#2d2d2d",
            fg="#d4d4d4",
            insertbackground="white",
            font=("Consolas", 11),
            relief=tk.FLAT,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.entry.bind("<Return>", self._send)

        send_btn = tk.Button(
            input_frame,
            text="发送",
            command=self._send,
            bg="#007acc",
            fg="white",
            relief=tk.FLAT,
            font=("Microsoft YaHei", 10),
            padx=12,
        )
        send_btn.pack(side=tk.RIGHT)
        input_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.entry.focus_set()

        # 启动提示
        self._append("Uki v0.4 — 一个多变的日常助手\n")
        self._append("输入消息开始聊天，输入 /exit 关闭\n\n")

    def _send(self, event=None):
        msg = self.entry.get().strip()
        if not msg:
            return
        self.entry.delete(0, tk.END)

        if msg.lower() in ("/exit", "/quit", "exit", "quit"):
            self._append("再见！\n")
            self.root.after(500, self.root.destroy)
            return

        if msg.lower().startswith("/clear"):
            self.agent.clear_history()
            self._append("[会话已清除]\n\n")
            return

        self._append(f"▸ 你: {msg}\n")

        # 在后台线程中运行 Agent
        t = threading.Thread(target=self._run_agent, args=(msg,), daemon=True)
        t.start()

    def _run_agent(self, msg):
        """在后台线程执行 Agent，输出重定向到队列"""
        old_stdout = sys.stdout
        sys.stdout = _QueueStream(self.output_queue)
        try:
            self.agent.run(msg)
        except Exception as e:
            self.output_queue.put(f"[错误] {e}")
        finally:
            sys.stdout = old_stdout
            self.output_queue.put(None)  # 结束标记

    def _poll_queue(self):
        """主线程定时检查队列，更新 UI"""
        try:
            while True:
                line = self.output_queue.get_nowait()
                if line is None:
                    self._append("\n")
                    self.entry.configure(state="normal")
                    self.entry.focus_set()
                    return
                self._append(f"{line}\n")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append(self, text: str):
        self.output.configure(state="normal")
        self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.configure(state="disabled")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if not Config.is_ready():
        print("请先在 .env 中配置 API key")
        sys.exit(1)
    UkiGUI().run()
