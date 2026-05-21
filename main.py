"""
Uki 的入口文件

相当于 Claude Code 的 `claude` 命令。
运行方式: python main.py

功能: 启动 Uki 的对话循环。Uki 可以自主使用工具完成任务。
"""

from uki.config import Config
from uki.agent import UkiAgent


def main():
    print("=" * 50)
    print("  Uki v0.2")
    print("  一个能思考、会动手的日常助手")
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

    uki = UkiAgent()
    print("Uki 已就绪。试试让 Uki 帮你查看文件、搜索代码。输入 /exit 退出。")
    print("试试这样说：\"看看当前目录有什么文件\"\n")

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

        if user_input.lower() in ("/chat",):
            # 切换到简单聊天模式（第四课前的方式）
            print("(切换到简单聊天模式)")
            reply = uki.chat("和我聊聊吧")
            print(f"Uki: {reply}")
            print()
            continue

        # 使用代理循环（第四课的核心）
        uki.run(user_input)
        print()


if __name__ == "__main__":
    main()
