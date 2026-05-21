"""
Uki 的入口文件

相当于 Claude Code 的 `claude` 命令。
运行方式: python main.py
"""

from uki.config import Config
from uki.agent import UkiAgent
from uki.commands import create_builtin_registry


def main():
    print("=" * 50)
    print("  Uki v0.3")
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

    # 初始化命令系统
    commands = create_builtin_registry()

    uki = UkiAgent()
    print("Uki 已就绪。输入 /help 查看可用命令，输入 /exit 退出。")
    print("试试这样说：\"看看当前目录有什么文件\"")
    print()

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        # 本地退出命令
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            print("Uki: 再见！有需要随时找我。")
            break

        # 先检查是否是本地命令（以 / 开头）
        cmd = commands.match(user_input)
        if cmd:
            print(f"[本地命令] {cmd.run('')}")
            print()
            continue

        # 不是命令 → 交给 Agent 处理
        uki.run(user_input)
        print()


if __name__ == "__main__":
    main()
