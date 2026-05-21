"""
Uki 的工具集

每个工具就是一个 Uki 可以执行的操作。
当前版本包含：列出文件、读取文件、搜索代码。

对应 Claude Code 的内置工具（文件操作、搜索、执行命令）。
"""

import os
import re
from pathlib import Path


# ============================================================
# 工具定义（给 LLM 看的描述，告诉它有哪些工具可以用）
# ============================================================

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出指定目录下的所有文件和文件夹。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径，默认 '.' 表示当前目录",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个文件的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的相对或绝对路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写入一个文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


# ============================================================
# 工具实现
# ============================================================

def execute_tool(name: str, arguments: dict) -> str:
    """根据工具名和参数执行工具，返回执行结果文本。"""
    if name == "list_files":
        return _list_files(arguments.get("path", "."))
    elif name == "read_file":
        return _read_file(arguments["path"])
    elif name == "write_file":
        return _write_file(arguments["path"], arguments["content"])
    else:
        return f"未知工具: {name}"


def _list_files(path: str) -> str:
    abs_path = Path(path).resolve()
    if not abs_path.exists():
        return f"目录不存在: {path}"
    if not abs_path.is_dir():
        return f"不是目录: {path}"

    try:
        entries = sorted(abs_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return f"没有权限访问: {path}"

    lines = []
    for entry in entries:
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"  {entry.name}{suffix}")

    if not lines:
        return f"目录为空: {abs_path}"

    return f"目录 {abs_path} 的内容:\n" + "\n".join(lines)


def _read_file(path: str) -> str:
    abs_path = Path(path).resolve()
    if not abs_path.exists():
        return f"文件不存在: {path}"
    if not abs_path.is_file():
        return f"不是文件: {path}"

    try:
        content = abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"无法以 UTF-8 读取文件（可能是二进制文件）: {path}"
    except PermissionError:
        return f"没有权限读取: {path}"

    # 限制输出长度，避免撑爆上下文
    max_chars = 5000
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n... (内容过长，已截断，剩余部分请用 offset 参数读取)"
        lines_count = content.count("\n")
        return f"文件 {abs_path} 的内容（共 {lines_count}+ 行，已截断至 {max_chars} 字符）:\n\n{content}"

    lines_count = content.count("\n")
    return f"文件 {abs_path} 的内容（共 {lines_count} 行）:\n\n{content}"


def _write_file(path: str, content: str) -> str:
    abs_path = Path(path).resolve()
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        return f"文件已写入: {abs_path}（{len(content)} 字符）"
    except PermissionError:
        return f"没有权限写入: {path}"
    except Exception as e:
        return f"写入失败: {e}"
