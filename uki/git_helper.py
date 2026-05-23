"""
Git 状态辅助

封装 Git 命令调用，为 Agent 提供项目版本感知能力。
对应 Claude Code 的 Git 状态注入。
"""

import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    """安全执行 git 命令，返回 stdout。失败返回空字符串。"""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_branch(cwd: Path | None = None) -> str:
    """获取当前分支名"""
    return _run_git(["branch", "--show-current"], cwd)


def get_status(cwd: Path | None = None) -> dict:
    """
    获取工作区状态。
    返回 {"modified": [...], "staged": [...], "untracked": [...]}
    """
    output = _run_git(["status", "--porcelain"], cwd)
    if not output:
        return {"modified": [], "staged": [], "untracked": []}

    result = {"modified": [], "staged": [], "untracked": []}
    for line in output.split("\n"):
        if not line:
            continue
        # Git status --porcelain 格式: XY filename
        # X = staged 状态, Y = unstaged 状态
        status = line[:2]
        filename = line[3:].strip()
        if status[0] in ("M", "A", "D", "R"):
            result["staged"].append(filename)
        if status[1] in ("M", "D"):
            result["modified"].append(filename)
        if status[0] == "?":
            result["untracked"].append(filename)
    return result


def get_recent_commits(n: int = 5, cwd: Path | None = None) -> list[str]:
    """获取最近 n 条提交信息（一行格式）"""
    output = _run_git(["log", f"-{n}", "--oneline"], cwd)
    if not output:
        return []
    return output.split("\n")


def get_summary(cwd: Path | None = None) -> str:
    """
    生成 Git 状态摘要，用于注入 system prompt。
    如果不在 Git 仓库中，返回空字符串。
    """
    branch = get_branch(cwd)
    if not branch:
        return ""

    lines = [f"当前 Git 分支: {branch}"]

    status = get_status(cwd)
    changes = []
    if status["staged"]:
        changes.append(f"已暂存 {len(status['staged'])} 个文件")
    if status["modified"]:
        changes.append(f"已修改 {len(status['modified'])} 个文件")
    if status["untracked"]:
        changes.append(f"未跟踪 {len(status['untracked'])} 个文件")
    if changes:
        lines.append("工作区: " + "，".join(changes))
    else:
        lines.append("工作区: 干净")

    commits = get_recent_commits(5)
    if commits:
        lines.append(f"最近 5 次提交:")
        for c in commits:
            lines.append(f"  {c}")

    return "\n".join(lines)
