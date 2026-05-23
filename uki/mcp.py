"""
MCP 工具管理器

从 .uki_tools.json 加载外部工具定义，将其注册为 Uki 可调用的工具。
对应 Claude Code 的 MCP 机制——工具不再硬编码，而是通过配置动态接入。
"""

import json
import subprocess
import shlex
from pathlib import Path
from typing import Any

MCP_CONFIG_FILE = ".uki_tools.json"


class MCPTool:
    """一个外部工具的定义"""

    def __init__(self, name: str, description: str, parameters: dict, command: str):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.command = command  # 命令模板，{param} 会被替换为实际参数值

    def to_openai_definition(self) -> dict:
        """转为 OpenAI function calling 格式"""
        props = {}
        required = []
        for pname, pinfo in self.parameters.items():
            props[pname] = {
                "type": pinfo.get("type", "string"),
                "description": pinfo.get("description", ""),
            }
            if pinfo.get("required", False):
                required.append(pname)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }

    def execute(self, arguments: dict) -> str:
        """执行外部命令，{参数名} 替换为实际值"""
        cmd = self.command
        for key, value in arguments.items():
            cmd = cmd.replace(f"{{{key}}}", shlex.quote(str(value)))

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=Path.cwd(),
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                err = result.stderr.strip()
                return f"命令执行失败 (exit={result.returncode}): {err}" if err else f"命令执行失败 (exit={result.returncode})"
            if not output:
                return "命令执行成功，无输出。"
            # 截断
            if len(output) > 4000:
                output = output[:4000] + "\n...（输出过长，已截断）"
            return output
        except subprocess.TimeoutExpired:
            return "命令执行超时（30 秒）"
        except Exception as e:
            return f"命令执行异常: {e}"


class MCPManager:
    """管理所有 MCP 外部工具"""

    def __init__(self):
        self.tools: list[MCPTool] = []
        self._load()

    def _load(self):
        """从配置文件加载外部工具"""
        config_path = Path(MCP_CONFIG_FILE)
        if not config_path.exists():
            return

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            return

        for entry in data.get("tools", []):
            try:
                tool = MCPTool(
                    name=entry["name"],
                    description=entry.get("description", ""),
                    parameters=entry.get("parameters", {}),
                    command=entry["command"],
                )
                self.tools.append(tool)
            except (KeyError, Exception):
                continue

    def get_definitions(self) -> list[dict]:
        """获取所有工具的 OpenAI 格式定义"""
        return [t.to_openai_definition() for t in self.tools]

    def execute(self, name: str, arguments: dict) -> str | None:
        """
        执行指定名称的外部工具。
        返回执行结果字符串，或 None（表示不是 MCP 工具，交给内置工具处理）。
        """
        for tool in self.tools:
            if tool.name == name:
                return tool.execute(arguments)
        return None
