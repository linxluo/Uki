"""
Uki 的命令系统

以 / 开头的输入由本地命令系统处理，不发给 LLM。
灵感来自 Claude Code 的斜杠命令。

设计原则：命令系统是可扩展的——后续课时（插件、钩子）
可以通过 register() 动态注册新命令。
"""

import textwrap
from uki.config import Config


class Command:
    """一个命令的定义"""
    def __init__(self, name: str, description: str, handler):
        self.name = name
        self.description = description
        self.handler = handler  # 一个函数，返回字符串

    def run(self, args: str = "") -> str:
        """执行命令，返回输出文本"""
        return self.handler(args)


class CommandRegistry:
    """命令注册表"""

    def __init__(self):
        self._commands: dict[str, Command] = {}

    def register(self, name: str, description: str, handler):
        """注册一个命令"""
        cmd = Command(name, description, handler)
        self._commands[name] = cmd
        return cmd

    def match(self, user_input: str) -> Command | None:
        """尝试匹配用户输入。返回匹配的 Command 或 None。"""
        if not user_input.startswith("/"):
            return None

        # 解析命令名（取第一个空格前的部分）
        parts = user_input.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # 精确匹配
        if cmd_name in self._commands:
            return self._commands[cmd_name]

        # 模糊匹配（补全 /h → /help）
        for name, cmd in self._commands.items():
            if name.startswith(cmd_name):
                return cmd

        return None

    def list_commands(self) -> str:
        """列出所有注册的命令"""
        lines = ["Uki 可用命令:"]
        for name, cmd in self._commands.items():
            lines.append(f"  {name:<15} {cmd.description}")
        return "\n".join(lines)


# ============================================================
# 内置命令的处理器
# ============================================================

def _cmd_help(args: str) -> str:
    """/help 命令"""
    registry = _cmd_help.__self__  # 会在 _create_builtins 中设置
    return registry.list_commands()


def _cmd_tools(args: str) -> str:
    """/tools 命令：列出 Uki 当前可用的工具"""
    from uki.tools import TOOL_DEFINITIONS
    lines = ["Uki 当前可用工具:"]
    for t in TOOL_DEFINITIONS:
        func = t["function"]
        lines.append(f"  🔧 {func['name']}: {func['description']}")
    return "\n".join(lines)


def _cmd_config(args: str) -> str:
    """/config 命令：显示当前配置"""
    return f"当前配置:\n{Config.summary()}"


def _cmd_model(args: str) -> str:
    """/model 命令：显示或切换模型"""
    if args.strip():
        return f"切换模型功能将在后续课程实现。当前模型: {Config.model}"
    return f"当前模型: {Config.model}"


def _cmd_clear(args: str) -> str:
    """/clear 命令：清除对话（提示用户重新开始）"""
    return "会话已清除。下次输入将开始全新对话。"


def _cmd_compact(args: str) -> str:
    """/compact 命令：压缩上下文（提示概念）"""
    return (
        "上下文压缩：\n"
        "  当前会话的上下文将自动管理。\n"
        "  当消息过多时，系统会自动保留最近的对话内容，\n"
        "  并压缩中间部分以节省 LLM token。\n"
        "  你无需手动操作，Uki 会在接近上限时自动处理。"
    )


def _cmd_context(args: str) -> str:
    """/context 命令：查看上下文用量（提示概念）"""
    return (
        "上下文用量：\n"
        "  LLM 的上下文窗口是有限的（通常在 8k~128k tokens 之间）。\n"
        "  Uki 的 system prompt、工具定义、对话历史都在消耗 token。\n"
        "  当用量接近上限时，Uki 会自动压缩旧消息。\n"
        "  你可以通过 UKI.md 写规则来减少重复的 system prompt 内容。"
    )


def create_builtin_registry() -> CommandRegistry:
    """创建并返回预装内置命令的注册表"""
    registry = CommandRegistry()

    registry.register("/help", "显示所有可用命令", _cmd_help)
    registry.register("/tools", "列出 Uki 当前可用的工具", _cmd_tools)
    registry.register("/config", "显示当前配置", _cmd_config)
    registry.register("/model", "显示当前使用的 LLM 模型", _cmd_model)
    registry.register("/clear", "清除对话历史", _cmd_clear)
    registry.register("/compact", "压缩上下文以节省 token", _cmd_compact)
    registry.register("/context", "查看当前上下文用量说明", _cmd_context)

    # 让 /help 处理器能访问注册表
    import types
    _cmd_help.__self__ = registry

    return registry
