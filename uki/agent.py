"""
Uki 的核心 Agent 类

这是 Uki 的"大脑"。目前只做了最基础的事：
接收你的消息，发送给 LLM，返回回复。

随着课程推进，这里会逐步加入记忆、Skill、权限控制等功能。
"""

from openai import OpenAI
from uki.config import Config


class UkiAgent:
    """Uki 的主 Agent"""

    def __init__(self):
        self.client = OpenAI(
            api_key=Config.api_key,
            base_url=Config.base_url,
        )
        self.model = Config.model

    def chat(self, user_message: str) -> str:
        """
        发送消息给 LLM 并返回回复。
        这是 Uki 最核心的能力，对应 Claude Code 中的"输入指令→获得响应"。
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是 Uki，一个温暖、多变的日常助手。用简洁、友好的方式回答问题。"
                    },
                    {
                        "role": "user",
                        "content": user_message,
                    },
                ],
            )
            return response.choices[0].message.content or "(Uki 没有返回内容)"
        except Exception as e:
            return f"(Uki 出错了: {e})"
