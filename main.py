"""
Uki 的入口文件

相当于 Claude Code 的 `claude` 命令。
运行方式: python main.py
"""

from uki.config import Config
from uki.agent import UkiAgent
from uki.commands import create_builtin_registry
from uki import display


def main():
    display.divider("═")
    print(f"  Uki v0.4")
    print(f"  一个能思考、会动手的日常助手")
    display.divider("═")
    print()
    print(Config.summary())
    print()

    if not Config.is_ready():
        display.warning("请先配置 API key")
        print("  1. 复制 .env.example 为 .env")
        print("  2. 在 .env 中填入你的 API key")
        print("  3. 重新运行 python main.py")
        return

    commands = create_builtin_registry()
    uki = UkiAgent()

    display.success("Uki 已就绪。输入 /help 查看可用命令，输入 /exit 退出。")
    display.info("试试这样说：\"看看当前目录有什么文件\"")
    print()

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

        if user_input.lower().startswith("/clear"):
            uki.clear_history()
            display.info("[本地命令] 会话已清除，Uki 不再记得之前的对话。")
            print()
            continue

        cmd = commands.match(user_input)
        if cmd:
            display.section("本地命令")
            print(cmd.run(""))
            print()
            continue

        uki.run(user_input)
        print()


if __name__ == "__main__":
    main()
