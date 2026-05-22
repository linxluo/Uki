"""
Uki 桌面窗口 (Tkinter 暗色主题)

零额外依赖，纯 Python 自带 Tkinter 实现的深色 GUI。
运行: python gui.py
"""

import sys
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from uki.agent import UkiAgent
from uki.config import Config


# 颜色主题
BG      = "#1a1a2e"
BG_INPUT = "#16213e"
BG_BTN  = "#0f3460"
FG      = "#e0e0e0"
FG_DIM  = "#8888aa"
ACCENT  = "#e94560"
FONT    = ("Segoe UI", 11)
FONT_SM = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI", 16, "bold")


class _QueueStream:
    def __init__(self, q: queue.Queue):
        self.q = q
        self._buf = ""

    def write(self, text: str):
        self._buf += text
        if "\n" in self._buf:
            lines = self._buf.split("\n")
            self._buf = lines.pop()
            for line in lines:
                if line.strip():
                    self.q.put(line.strip())

    def flush(self):
        if self._buf.strip():
            self.q.put(self._buf)
            self._buf = ""


class UkiGUI:
    def __init__(self):
        self.agent = UkiAgent()
        self.output_queue = queue.Queue()
        self._running = False
        self._setup_ui()
        self._poll_queue()

    def _setup_ui(self):
        self.root = tk.Tk()
        self.root.title("Uki")
        self.root.geometry("820x600")
        self.root.minsize(500, 400)
        self.root.configure(bg=BG)

        # 顶部
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=20, pady=(14, 0))

        tk.Label(top, text="Uki", font=FONT_TITLE, fg=ACCENT, bg=BG).pack(side="left")
        tk.Label(top, text=Config.model, font=FONT_SM, fg=FG_DIM, bg=BG).pack(side="right")

        # 输出区域
        self.output = scrolledtext.ScrolledText(
            self.root, wrap="word", font=FONT,
            bg=BG, fg=FG, insertbackground=FG,
            relief="flat", borderwidth=0,
            padx=12, pady=12,
            highlightthickness=0,
        )
        self.output.pack(fill="both", expand=True, padx=20, pady=(10, 0))
        self.output.configure(state="disabled")

        # 输入栏
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=20, pady=(10, 16))

        self.entry = tk.Entry(
            bar, font=FONT,
            bg=BG_INPUT, fg=FG, insertbackground=FG,
            relief="flat", highlightthickness=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", self._send)
        self.entry.focus_set()

        self.btn = tk.Button(
            bar, text="发送", command=self._send,
            bg=ACCENT, fg="white", font=FONT,
            relief="flat", padx=20, pady=4,
            activebackground="#ff6b81", activeforeground="white",
            cursor="hand2",
        )
        self.btn.pack(side="right", padx=(10, 0))

        # 启动问候
        self._append("Uki 已就绪。\n\n")

    def _send(self, event=None):
        if self._running:
            return
        msg = self.entry.get().strip()
        if not msg:
            return
        self.entry.delete(0, "end")

        if msg.lower() in ("/exit", "/quit"):
            self.root.after(200, self.root.destroy)
            return

        if msg.lower().startswith("/clear"):
            self.agent.clear_history()
            self._append("─" * 40 + "\n[会话已清除]\n\n")
            return

        self._append(f"▸ 你: {msg}\n")
        self._running = True
        self.btn.configure(state="disabled", text="…")
        threading.Thread(target=self._run_agent, args=(msg,), daemon=True).start()

    def _run_agent(self, msg):
        old = sys.stdout
        sys.stdout = _QueueStream(self.output_queue)
        try:
            self.agent.run(msg)
        except Exception as e:
            self.output_queue.put(f"[错误] {e}")
        finally:
            sys.stdout = old
            self.output_queue.put(None)

    def _poll_queue(self):
        try:
            while True:
                line = self.output_queue.get_nowait()
                if line is None:
                    self._append("\n")
                    self._running = False
                    self.btn.configure(state="normal", text="发送")
                    self.entry.focus_set()
                    return
                self._append(f"{line}\n")
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _append(self, text: str):
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if not Config.is_ready():
        print("请先在 .env 中配置 API key")
        sys.exit(1)
    UkiGUI().run()
