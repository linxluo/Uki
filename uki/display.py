"""
终端输出辅助

为 Uki 的不同运行阶段提供统一的视觉符号和颜色标记。
对应 Claude Code 界面中的状态符号系统。
"""

# ANSI 颜色码
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"


# ============================================================
# 阶段标记
# ============================================================

def thinking(turn: int):
    """进入新一轮思考"""
    print(f"\n{_BOLD}{_CYAN}  💭 第 {turn} 轮思考{_RESET}")


def using_tool(name: str, args: dict):
    """正在调用工具"""
    print(f"  {_YELLOW}🔧 调用工具{_RESET}: {name}({_args_str(args)})")


def tool_result(text: str):
    """工具执行结果（截断显示）"""
    short = text[:150].replace("\n", " ")
    suffix = "..." if len(text) > 150 else ""
    print(f"  {_DIM}📋 {short}{suffix}{_RESET}")


def agent_reply(text: str):
    """Agent 最终回复"""
    print(f"\n  {_GREEN}✨ Uki{_RESET}: {text}")


def warning(text: str):
    """警告信息"""
    print(f"  {_YELLOW}⚠️  {text}{_RESET}")


def success(text: str):
    """成功信息"""
    print(f"  {_GREEN}✓ {text}{_RESET}")


def quiet(text: str):
    """低调信息（自动记忆等）"""
    print(f"  {_DIM}📝 {text}{_RESET}")


def error(text: str):
    """错误信息"""
    print(f"  {_RED}✗ {text}{_RESET}")


def info(text: str):
    """一般信息"""
    print(f"  {_DIM}{text}{_RESET}")


# ============================================================
# 辅助
# ============================================================

def _args_str(args: dict) -> str:
    """格式化工具参数显示"""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        val = str(v)
        if len(val) > 40:
            val = val[:40] + "..."
        parts.append(f"{k}={val}")
    return ", ".join(parts)


def divider(char: str = "─", width: int = 50):
    """分隔线"""
    print(f"{_DIM}{char * width}{_RESET}")


def section(title: str):
    """区块标题"""
    print(f"\n{_BOLD}{_MAGENTA}  ▸ {title}{_RESET}")
