"""
Uki 的核心 Agent 类

实现了类似 Claude Code 的代理循环：
  你的指令 → 思考 → 行动（调工具）→ 观察结果 → 再思考 → ... → 完成

这是 Uki 从"聊天机器人"升级为"代理"的关键一步。
"""

import json
from openai import OpenAI
from uki.config import Config
from uki.tools import TOOL_DEFINITIONS, execute_tool

# 最大循环轮数，防止无限循环消耗费用
MAX_TURNS = 10


class UkiAgent:
    """Uki 的主 Agent"""

    def __init__(self):
        self.client = OpenAI(
            api_key=Config.api_key,
            base_url=Config.base_url,
        )
        self.model = Config.model
        self.system_prompt = (
            "你是 Uki，一个温暖、多变的日常助手。\n\n"
            "你有能力使用工具来完成用户的请求。面对任务时，按以下方式思考：\n"
            "1. 理解用户想要什么\n"
            "2. 如果需要查看文件或执行操作，调用相应的工具\n"
            "3. 根据工具返回的结果，决定下一步做什么\n"
            "4. 直到任务完成，给出清晰、友好的总结\n\n"
            "重要规则：\n"
            "- 优先使用工具获取真实信息，不要猜测\n"
            "- 每次只调用一个工具\n"
            "- 操作前先列出当前目录的文件，了解环境"
        )

    # ================================================================
    # 核心循环（这是第四课最重要的代码）
    # ================================================================

    def run(self, user_message: str):
        """
        代理循环：持续思考-行动-观察，直到任务完成。

        对应 Claude Code 的: Prompt → 收集上下文 → 执行动作 → 验证结果 → （循环）
        """
        # 消息历史：系统提示 + 用户消息 + 助手回复 + 工具结果
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]

        turn = 0
        while turn < MAX_TURNS:
            turn += 1
            print(f"\n--- 第 {turn} 轮 ---")

            # 让 LLM 思考并决定下一步
            response = self._call_llm(messages)

            choice = response.choices[0]
            message = choice.message

            # 情况 A：LLM 调用了工具
            if message.tool_calls:
                tool_call = message.tool_calls[0]
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                print(f"  🔧 调用工具: {tool_name}({tool_args})")

                # 执行工具
                result = execute_tool(tool_name, tool_args)
                print(f"  📋 工具结果: {result[:200]}{'...' if len(result) > 200 else ''}")

                # 把工具的调用和结果都加入消息历史
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": tool_call.function.arguments,
                        },
                    }],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
                # 继续循环，让 LLM 在工具结果的基础上再思考
                continue

            # 情况 B：LLM 给出了最终文本回复（无工具调用）
            content = message.content or ""
            if content.strip():
                print(f"\n  ✨ Uki: {content}")
                # 把回复加入历史，然后结束循环
                messages.append({
                    "role": "assistant",
                    "content": content,
                })
                return content

            # 情况 C：空回复（不太正常，但可以处理）
            print("  (Uki 没有进一步的行动或回复)")
            break

        print(f"\n  ⚠️ 达到最大轮数（{MAX_TURNS}），Uki 停止了思考。")

    # ================================================================
    # 底层 LLM 调用
    # ================================================================

    def _call_llm(self, messages: list):
        """调用 LLM，开启工具调用能力"""
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",  # LLM 自己决定要不要用工具
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
