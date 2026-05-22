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


def _cmd_init(args: str) -> str:
    """/init 命令：初始化项目规则文件（对应 Claude Code 的 /init）"""
    from pathlib import Path
    root = Path(".")
    created = []
    uki_md = root / "UKI.md"
    if not uki_md.exists():
        uki_md.write_text("""# Uki 的项目规则

> 这个文件会被 Uki 在每次对话中自动读取。

## 沟通风格
- 用中文回复
- 语气温暖、简洁

## 工作习惯
- 操作文件前先列出目录了解环境
- 修改文件前先读取原文件内容
- 优先使用工具获取真实信息，不要猜测
- 每次只调用一个工具

## 技术约定
- （这里可以写代码风格、命名规范等）

## 个人偏好
- （这里可以写任何想让 Uki 记住的偏好）
""", encoding="utf-8")
        created.append("UKI.md（项目规则）")
    env_example = root / ".env.example"
    if not env_example.exists():
        env_example.write_text("""OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
""", encoding="utf-8")
        created.append(".env.example（API 配置模板）")
    if created:
        return "已创建：\n" + "\n".join(f"  ✓ {c}" for c in created) + "\n\n请编辑这些文件后重启 Uki。"
    return "UKI.md 和 .env.example 已存在，无需初始化。"


# /mode 命令需要访问 agent 实例，通过模块级变量注入
_agent_ref = None

def set_agent_ref(agent):
    global _agent_ref
    _agent_ref = agent

def _cmd_mode(args: str) -> str:
    """/mode 命令：查看或切换权限模式"""
    if not _agent_ref:
        return "无法访问 Agent 实例。"
    arg = args.strip().lower()
    if arg in ("default", "auto", "readonly"):
        _agent_ref.set_mode(arg)
        return f"权限模式已切换为: {arg}"
    modes = {
        "default": "默认模式 — 写入文件前需确认",
        "auto": "自动模式 — 所有操作直接执行",
        "readonly": "只读模式 — 可以读、搜、列，不能写文件",
    }
    current = _agent_ref.permission_mode
    lines = [f"当前模式: {current}"]
    for m, desc in modes.items():
        marker = " ← 当前" if m == current else ""
        lines.append(f"  {m:10} {desc}{marker}")
    lines.append("\n切换: /mode auto | /mode default | /mode readonly")
    return "\n".join(lines)


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
    registry.register("/init", "初始化项目规则文件（UKI.md 和 .env.example）", _cmd_init)
    registry.register("/mode", "查看或切换权限模式（default/auto/readonly）", _cmd_mode)

    # 让 /help 处理器能访问注册表
    import types
    _cmd_help.__self__ = registry

    return registry
