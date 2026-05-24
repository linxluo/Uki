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

    def list_commands_as_dict(self) -> list[dict]:
        """返回所有命令的结构化列表，供前端自动补全使用。"""
        return [
            {"name": name, "description": cmd.description}
            for name, cmd in self._commands.items()
        ]


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


def _cmd_plugin(args: str) -> str:
    """/plugin 命令：查看已加载的插件状态，或安装/管理插件"""
    if not _agent_ref:
        return "无法访问 Agent 实例。"

    parts = args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    sub_args = parts[1] if len(parts) > 1 else ""

    pm = _agent_ref.plugin_manager

    if sub == "install":
        if not sub_args:
            return "用法: /plugin install <插件zip文件路径>\n示例: /plugin install C:/Downloads/pdf_reader.zip"
        if pm.install_from_zip(sub_args):
            # 安装成功后刷新 agent 的工具列表
            _agent_ref._rebuild_tool_list()
            return f"插件安装成功！\n{pm.plugin_status()}"
        else:
            return "插件安装失败，请检查 zip 文件是否有效。"

    if sub == "list" or not sub:
        return pm.plugin_status()

    return f"未知子命令: {sub}\n可用: /plugin list | /plugin install <zip路径>"


def _cmd_memory(args: str) -> str:
    """/memory 命令：查看、添加、删除、搜索记忆"""
    if not _agent_ref:
        return "无法访问 Agent 实例。"

    parts = args.strip().split(maxsplit=2)
    sub = parts[0].lower() if parts else ""

    memory = _agent_ref.memory

    if sub == "add":
        if len(parts) < 2:
            return "用法: /memory add <内容> [标签,用逗号分隔]\n示例: /memory add 用户喜欢用 Godot 4.x 开发游戏 godot,游戏开发"
        content = parts[1]
        tags_str = parts[2] if len(parts) > 2 else ""
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        mem = memory.add(content, tags)
        return f"已添加记忆 [{mem['id']}]: {mem['content']}"

    elif sub == "remove" or sub == "delete":
        if len(parts) < 2:
            return "用法: /memory remove <关键词>\n示例: /memory remove Godot"
        keyword = parts[1]
        n = memory.remove(keyword)
        return f"已删除 {n} 条包含「{keyword}」的记忆。"

    elif sub == "search":
        if len(parts) < 2:
            return "用法: /memory search <关键词>"
        query = parts[1]
        results = memory.search(query, limit=10)
        if not results:
            return "未找到相关记忆。"
        lines = [f"找到 {len(results)} 条相关记忆:"]
        for mem in results:
            tags_str = f" [{', '.join(mem['tags'])}]" if mem.get('tags') else ""
            lines.append(f"  [{mem['id']}]{tags_str} {mem['content']}")
        return "\n".join(lines)

    elif sub == "list" or not sub:
        all_mem = memory.get_all()
        if not all_mem:
            return "记忆库为空。用 /memory add 添加第一条记忆吧。"
        lines = [f"共 {len(all_mem)} 条记忆:"]
        for mem in all_mem:
            tags_str = f" [{', '.join(mem['tags'])}]" if mem.get('tags') else ""
            lines.append(f"  [{mem['id']}]{tags_str} {mem['content']}")
        return "\n".join(lines)

    else:
        return "未知子命令。可用: /memory add <内容> [标签] | /memory list | /memory search <关键词> | /memory remove <关键词>"


def _cmd_create_my_plugin(args: str) -> str:
    """/createMyPlugin 命令：根据需求描述自动生成插件并安装"""
    if not _agent_ref:
        return "无法访问 Agent 实例。"

    requirement = args.strip()
    if not requirement:
        return "用法: /createMyPlugin <需求描述>\n示例: /createMyPlugin 创建一个翻译插件，支持中英互译"

    pm = _agent_ref.plugin_manager

    # 1. 构造 prompt，让 LLM 生成插件代码
    prompt = f"""请根据以下需求创建一个 Uki 插件。

需求：{requirement}

## 插件规范
一个 Uki 插件包含两个文件：

### uki_plugin.json（清单文件）
```json
{{
  "name": "插件名（英文，snake_case）",
  "version": "1.0.0",
  "description": "简短描述",
  "type": "python",
  "dependencies": ["需要的 pip 包名"]
}}
```

### plugin.py（插件代码）
```python
from uki.plugin_manager import UkiPlugin

class XxxPlugin(UkiPlugin):
    def on_load(self, agent=None):
        print(f"[Xxx] 插件已就绪")

    def get_tool_definitions(self):
        # 返回 OpenAI function calling 格式的工具定义列表
        return [...]

    def execute_tool(self, name, arguments):
        # 执行工具，返回结果字符串；不识别则返回 None
        ...

    def get_commands(self):
        # 返回 [(命令名, 描述, 处理函数), ...]
        return []
```

## 输出格式
请严格输出一个 JSON 对象，不要包含任何其他文字：
```json
{{
  "manifest": {{ "name": "...", "version": "1.0.0", "description": "...", "type": "python", "dependencies": [...] }},
  "plugin_py": "import ...\\n\\nclass ..."
}}
```
注意：plugin_py 中的字符串需要正确转义（\\n 表示换行，\\" 表示引号）。"""

    # 2. 调 LLM 生成代码
    try:
        response = _agent_ref.client.chat.completions.create(
            model=_agent_ref.model,
            messages=[
                {"role": "system", "content": "你是一个 Uki 插件生成器。根据用户需求生成完整可用的插件代码。只输出要求的 JSON，不要任何额外文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        raw = response.choices[0].message.content or ""
    except Exception as e:
        return f"LLM 调用失败: {e}"

    # 3. 解析 JSON
    import json as _json
    import re as _re
    # 尝试提取 JSON 块
    match = _re.search(r'\{[^{}]*"manifest"[^{}]*\}', raw, _re.DOTALL)
    if not match:
        # 尝试找任意最外层 JSON
        match = _re.search(r'\{.+\}', raw, _re.DOTALL)
    if not match:
        return f"无法解析 LLM 返回的内容。原始回复:\n{raw[:500]}"

    try:
        data = _json.loads(match.group())
    except _json.JSONDecodeError as e:
        return f"JSON 解析失败: {e}\n原始回复:\n{raw[:500]}"

    manifest = data.get("manifest", {})
    plugin_py = data.get("plugin_py", "")

    if not manifest.get("name") or not plugin_py:
        return f"生成的插件缺少必要字段（name 或 plugin_py）。\n返回内容:\n{raw[:500]}"

    name = manifest["name"]

    # 4. 写入插件目录
    import os as _os
    plugin_dir = pm._plugins_dir / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = plugin_dir / "uki_plugin.json"
    plugin_path = plugin_dir / "plugin.py"

    manifest_path.write_text(_json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    plugin_path.write_text(plugin_py, encoding="utf-8")

    # 5. 打包 zip
    import zipfile as _zf
    zip_path = pm._plugins_dir / f"{name}.zip"
    with _zf.ZipFile(str(zip_path), "w", _zf.ZIP_DEFLATED) as zf:
        zf.write(str(manifest_path), f"{name}/uki_plugin.json")
        zf.write(str(plugin_path), f"{name}/plugin.py")

    # 6. 安装
    ok = pm.install_from_zip(str(zip_path))
    if ok:
        _agent_ref._rebuild_tool_list()
        return f"插件「{name}」已创建并安装！\n\n位置: {plugin_dir}\nZip: {zip_path}\n\n你可以用 /plugin 查看状态，或在设置面板的「已安装的插件」中管理。"
    else:
        return f"插件文件已生成（{plugin_dir}），但自动安装失败。请手动拖入 {zip_path} 安装。"


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
    registry.register("/plugin", "查看已加载的插件状态", _cmd_plugin)
    registry.register("/createMyPlugin", "根据需求描述自动生成并安装插件", _cmd_create_my_plugin)
    registry.register("/memory", "管理 Uki 的长期记忆（添加/查看/搜索/删除）", _cmd_memory)

    # 让 /help 处理器能访问注册表
    import types
    _cmd_help.__self__ = registry

    return registry
