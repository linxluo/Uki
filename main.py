"""
Uki 的入口文件

相当于 Claude Code 的 `claude` 命令。
运行方式: python main.py

功能: 启动一个简单的对话循环，让你和 Uki 聊天。
"""

from uki.config import Config
from uki.agent import UkiAgent


def main():
    print("=" * 50)
    print("  Uki v0.1")
    print("  一个多变的日常助手")
    print("=" * 50)
    print()
    print(Config.summary())
    print()

    if not Config.is_ready():
        print("请先配置 API key：")
        print("  1. 复制 .env.example 为 .env")
        print("  2. 在 .env 中填入你的 API key")
        print("  3. 重新运行 python main.py")
        return

    print("Uki 已就绪。输入消息开始聊天，输入 /exit 退出。")
    print()

    uki = UkiAgent()

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            print("Uki: 再见！有需要随时找我。")
            break

        print("Uki: ", end="", flush=True)
        reply = uki.chat(user_input)
        print(reply)
        print()


if __name__ == "__main__":
    main()
