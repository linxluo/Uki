"""
Uki 的核心 Agent 类

实现了类似 Claude Code 的代理循环：
  你的指令 → 思考 → 行动（调工具）→ 观察结果 → 再思考 → ... → 完成

这是 Uki 从"聊天机器人"升级为"代理"的关键一步。
"""

import json
from pathlib import Path
from openai import OpenAI
from uki.config import Config
from uki.tools import TOOL_DEFINITIONS, execute_tool
from uki.mcp_client import MCPManager
from uki.plugin_manager import PluginManager
from uki import display
from uki.git_helper import get_summary

# 最大循环轮数，防止无限循环消耗费用
MAX_TURNS = 10
SUBAGENT_MAX_TURNS = 4  # 子代理轮数更少，防止递归爆炸

# Token 相关阈值（从 Config 动态读取，不再写死）
# 如果你强制需要一个值，可以写 0 让 Uki 自动匹配模型
FORCE_MAX_CONTEXT_TOKENS = 0   # 设为 0 则使用 Config 的自动匹配
FORCE_TRIM_THRESHOLD = 0


def _assistant_msg(message) -> dict:
    """
    手动构造 API 标准格式的 assistant 消息。
    content 和 tool_calls 手动构造确保格式正确。
    其余所有未知扩展字段从 model_dump 提取，确保模型升级后不丢字段。
    """
    msg = {"role": "assistant", "content": message.content}

    if message.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]

    # 从 model_dump 提取所有不在已知列表中的字段
    known = {"role", "content", "tool_calls", "refusal", "function_call", "audio"}
    dumped = message.model_dump(exclude_none=True, exclude=known)
    msg.update(dumped)

    return msg

# 项目规则文件名（对应 Claude Code 的 CLAUDE.md）
RULES_FILE = "UKI.md"


class UkiAgent:
    """Uki 的主 Agent"""

    def __init__(self):
        self.client = OpenAI(
            api_key=Config.api_key,
            base_url=Config.base_url,
        )
        self.model = Config.model
        self.rules = self._load_rules()
        self.system_prompt = self._build_system_prompt()
        self.conversation_history: list[dict] = []  # 跨轮次对话历史
        self.summary_threshold = Config.summary_threshold()  # 触发总结的 token 阈值
        self.has_recent_summary = False  # 防止连续两轮重复总结

        # 【第十三课】MCP 外部工具
        self.mcp = MCPManager()

        # 【第十五课】插件系统
        self.plugin_manager = PluginManager()
        self.plugin_manager.set_agent(self)

        # 合并内置工具 + MCP 工具 + 插件工具
        self._rebuild_tool_list()

        # 【第十一课】权限控制
        self.permission_mode = "default"  # default | auto | readonly
        self.permission_callback = None   # 外部注入的确认回调

    def clear_history(self):
        """清除跨轮次对话历史"""
        self.conversation_history = []
        self.has_recent_summary = False

    def reload_client(self):
        """重新创建 LLM 客户端，用于配置修改后即时生效。"""
        self.client = OpenAI(
            api_key=Config.api_key,
            base_url=Config.base_url,
        )
        self.model = Config.model

    # ================================================================
    # 工具列表管理（第十五课）
    # ================================================================

    def _rebuild_tool_list(self):
        """重建合并后的工具列表：内置 + MCP + 插件。"""
        self.all_tools = (
            list(TOOL_DEFINITIONS)
            + self.mcp.get_definitions()
            + self.plugin_manager.get_all_tool_definitions()
        )

    def load_plugins(self):
        """加载插件并刷新工具列表。在 Agent 初始化后调用。"""
        count = self.plugin_manager.load_all()
        if count > 0:
            self._rebuild_tool_list()
        return count

    # ================================================================
    # 子代理（第十四课）
    # ================================================================

    def _run_subagent(self, task: str) -> str:
        """
        启动一个轻量子代理完成子任务。
        子代理仅拥有只读工具，独立上下文，无权限限制。
        """
        display.info(f"🤖 子代理启动: {task[:60]}...")

        sub_tool_names = {"list_files", "read_file", "search_code"}
        sub_tools = [t for t in self.all_tools if t["function"]["name"] in sub_tool_names]

        messages = [
            {"role": "system", "content": "你是 Uki 的子代理，专注完成单个任务。使用工具获取信息，给出简洁准确的结果。不要写文件，不要派新子代理。"},
            {"role": "user", "content": task},
        ]

        for turn in range(1, SUBAGENT_MAX_TURNS + 1):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=sub_tools,
                tool_choice="auto",
            )
            msg = response.choices[0].message

            if msg.tool_calls:
                tc = msg.tool_calls[0]
                args = json.loads(tc.function.arguments)
                result = execute_tool(tc.function.name, args)
                messages.append(_assistant_msg(msg))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                continue

            if msg.content:
                display.success("子代理完成")
                return f"[子代理结果]\n任务: {task}\n结果: {msg.content}"

        return f"[子代理] 未能在 {SUBAGENT_MAX_TURNS} 轮内完成: {task}"

    # ================================================================
    # 权限控制（第十一课）
    # ================================================================

    WRITE_TOOLS = {"write_file", "execute_bash"}

    def set_mode(self, mode: str):
        """切换权限模式：default / auto / readonly"""
        if mode in ("default", "auto", "readonly"):
            self.permission_mode = mode

    def _check_permission(self, tool_name: str) -> bool:
        """检查是否允许执行此工具"""
        is_write = tool_name in self.WRITE_TOOLS

        if self.permission_mode == "auto":
            return True

        if self.permission_mode == "readonly" and is_write:
            display.warning(f"只读模式下拒绝写入操作: {tool_name}")
            return False

        if self.permission_mode == "default" and is_write:
            if self.permission_callback:
                return self.permission_callback(tool_name)
            display.warning(f"默认模式下无确认回调，拒绝写入: {tool_name}")
            return False

        return True

    # ================================================================
    # LLM 自动总结（第八课核心功能）
    # ================================================================

    def _maybe_summarize(self):
        """
        检查对话历史是否需要总结。
        条件：历史 token 数超过阈值，且上一轮没有刚刚总结过。
        """
        if not self.conversation_history:
            return
        if self.has_recent_summary:
            return

        tokens = self._estimate_tokens(self.conversation_history)
        if tokens < self.summary_threshold:
            return

        display.info(f"对话历史达 {tokens} tokens（阈值 {self.summary_threshold}），正在生成摘要...")
        summary = self._summarize_history()
        if summary:
            self.conversation_history = [
                {"role": "system", "content": f"【对话历史摘要】{summary}"}
            ]
            self.has_recent_summary = True
            display.success(f"摘要完成，压缩至约 {self._estimate_tokens(self.conversation_history)} tokens")

    def _summarize_history(self) -> str:
        """
        调 LLM 对 conversation_history 做语义摘要。
        要求保留：用户的核心需求、已做的决策、未解决的问题、关键约束。
        丢弃：重复的迭代过程、工具调用的中间细节。
        """
        # 把历史拼成文本
        lines = []
        for msg in self.conversation_history:
            role = "用户" if msg["role"] == "user" else "Uki"
            content = msg.get("content", "") or ""
            if content.strip():
                lines.append(f"{role}: {content}")
        history_text = "\n".join(lines)

        summary_prompt = (
            "请将以下对话历史总结为一段中文摘要（500 字以内）。\n\n"
            "必须保留这些信息：\n"
            "  - 用户的核心需求和目标\n"
            "  - 已经做出的重要决策\n"
            "  - 尚未解决的问题\n"
            "  - 用户明确提出的约束或偏好\n\n"
            "可以丢弃这些信息：\n"
            "  - 重复的迭代和微调过程\n"
            "  - 工具调用的中间细节\n"
            "  - 寒暄和无关话题\n\n"
            f"=== 对话历史 ===\n{history_text}\n=== 结束 ==="
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=800,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"  ⚠️ 摘要生成失败: {e}")
            return ""

    def _load_rules(self) -> str:
        """读取项目规则文件 UKI.md（对应 Claude Code 的 CLAUDE.md）"""
        rules_path = Path(RULES_FILE)
        if rules_path.exists():
            try:
                content = rules_path.read_text(encoding="utf-8").strip()
                if content:
                    return f"\n\n## 项目规则（来自 {RULES_FILE}）\n以下规则由项目维护者设定，每次对话都必须遵守：\n\n{content}"
            except Exception:
                pass
        return ""

    def _build_system_prompt(self) -> str:
        """构建完整的系统提示词（基础角色 + Git 状态 + 项目规则）"""
        base = (
            "你是 Uki，一个温暖、多变的日常助手。"
            "回复时使用 Markdown 格式：标题用 ##，加粗用 **文字**，列表用 - 开头，代码用 `` ` ``。段落之间空一行。"
        )
        # Git 状态仅在开发模式下注入（通过 UKI_GIT_CONTEXT=1 控制）
        if Config.git_context_enabled():
            git_status = get_summary()
            if git_status:
                git_block = f"\n\n## Git 状态\n{git_status}"
            else:
                git_block = ""
        else:
            git_block = ""
        return base + git_block + self.rules

    # ================================================================
    # 核心循环（这是第四课最重要的代码）
    # ================================================================

    def run(self, user_message: str):
        """
        代理循环：持续思考-行动-观察，直到任务完成。

        对应 Claude Code 的: Prompt → 收集上下文 → 执行动作 → 验证结果 → （循环）
        """
        # 消息历史：system prompt + 之前的对话历史 + 当前用户消息
        messages = [{"role": "system", "content": self.system_prompt}]

        # 【第八课】LLM 自动总结：对话历史过长时先压缩再发给 LLM
        self._maybe_summarize()

        messages.extend(self.conversation_history)
        messages.append({"role": "user", "content": user_message})

        turn = 0
        while turn < MAX_TURNS:
            turn += 1

            # 【第八课】检查上下文用量
            max_tokens = FORCE_MAX_CONTEXT_TOKENS or Config.max_context_tokens()
            trim_tokens = FORCE_TRIM_THRESHOLD or Config.trim_threshold()
            token_est = self._estimate_tokens(messages)
            if token_est > max_tokens:
                display.warning(f"上下文接近推荐上限（约 {token_est}/{max_tokens} tokens），正在自动压缩...")
                messages = self._trim_context(messages, trim_tokens)
                display.success(f"压缩后约 {self._estimate_tokens(messages)} tokens")

            display.thinking(turn)

            # 让 LLM 思考并决定下一步
            response = self._call_llm(messages)

            choice = response.choices[0]
            message = choice.message

            # 情况 A：LLM 调用了工具
            if message.tool_calls:
                messages.append(_assistant_msg(message))
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)
                    display.using_tool(tool_name, tool_args)

                    if not self._check_permission(tool_name):
                        messages.append({"role": "tool", "tool_call_id": tool_call.id,
                            "content": f"操作被拒绝: {tool_name}"})
                        continue

                    if tool_name == "delegate":
                        result = self._run_subagent(tool_args.get("task", ""))
                    else:
                        # 优先级：插件 > MCP > 内置工具
                        result = self.plugin_manager.execute_tool(tool_name, tool_args)
                        if result is None:
                            result = self.mcp.execute(tool_name, tool_args)
                        if result is None:
                            result = execute_tool(tool_name, tool_args)
                    display.tool_result(result)
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                continue

            # 情况 B：LLM 给出了最终文本回复（无工具调用）
            content = message.content or ""
            if content.strip():
                display.agent_reply(content)
                # 把当前对话保存到跨轮次历史
                self.conversation_history.append({"role": "user", "content": user_message})
                self.conversation_history.append({"role": "assistant", "content": content})
                self.has_recent_summary = False  # 新一轮对话了，允许再次总结
                return content

            # 情况 C：空回复（不太正常，但可以处理）
            display.info("Uki 没有进一步的行动或回复")
            break

        display.warning(f"达到最大轮数（{MAX_TURNS}），Uki 停止了思考。")

    def _estimate_tokens(self, messages: list) -> int:
        """粗略估算当前消息的 token 数。兼容 dict 和 Pydantic 对象。"""
        total = 0
        for msg in messages:
            try:
                content = msg.get("content", "") or ""
            except AttributeError:
                content = getattr(msg, "content", None) or ""
            total += len(content)
        return int(total / 1.5)

    def _trim_context(self, messages: list, target_tokens: int) -> list:
        """
        裁剪上下文：保留 system prompt 和最近的消息。
        删除中间轮次的消息，确保总 token 数在 target_tokens 以下。
        """
        if len(messages) <= 2:
            return messages

        # system 消息永远保留
        system_msg = messages[0]
        rest = messages[1:]

        # 从后面保留消息，直到接近阈值
        kept = []
        current_tokens = self._estimate_tokens([system_msg])

        for msg in reversed(rest):
            try:
                msg_len = len(msg.get("content", "") or "")
            except AttributeError:
                msg_len = len(getattr(msg, "content", None) or "")
            msg_tokens = msg_len / 1.5
            if current_tokens + msg_tokens > target_tokens:
                break
            kept.insert(0, msg)
            current_tokens += msg_tokens

        result = [system_msg]
        if len(kept) < len(rest):
            result.append({
                "role": "system",
                "content": "（中间的对话已被压缩以节省上下文空间）"
            })
        result.extend(kept)
        return result

    # ================================================================
    # 底层 LLM 调用
    # ================================================================

    def _call_llm(self, messages: list):
        """调用 LLM，开启工具调用能力。使用合并后的工具列表（内置 + MCP）。"""
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.all_tools,
            tool_choice="auto",
        )

    # ================================================================
    # 保留简单对话方法（向后兼容）
    # ================================================================

    def chat(self, user_message: str) -> str:
        """简单的单次对话，不使用工具"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是 Uki，一个温暖、多变的日常助手。用简洁、友好的方式回答问题。"},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content or "(Uki 没有返回内容)"
        except Exception as e:
            return f"(Uki 出错了: {e})"
