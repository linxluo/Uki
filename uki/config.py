"""
Uki 的配置管理

读取 .env 文件和环境变量，管理所有配置项。
对应 Claude Code 的 settings.json 和 API 配置。
"""

import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()


class Config:
    """Uki 的全局配置"""

    # LLM 配置
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    @classmethod
    def is_ready(cls) -> bool:
        """检查是否已配置好 API key"""
        return bool(cls.api_key and cls.api_key != "sk-your-key-here")

    @classmethod
    def summary(cls) -> str:
        """打印当前配置摘要"""
        key_status = "已配置 ✓" if cls.is_ready() else "未配置 ✗（请复制 .env.example 为 .env 并填 API key）"
        return (
            f"API Key: {key_status}\n"
            f"接口地址: {cls.base_url}\n"
            f"模型: {cls.model}"
        )
