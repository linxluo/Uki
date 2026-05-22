"""
Uki 的配置管理

读取 .env 文件和环境变量，管理所有配置项。
对应 Claude Code 的 settings.json 和 API 配置。
"""

import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# ============================================================
# 已知模型的上下文窗口配置
# ============================================================
# 每个条目：总窗口 → 推荐的上限 (80%) → 触发压缩的阈值 (60%)
# 新模型在这里加一行即可。

MODEL_WINDOW_CONFIG: dict[str, dict[str, int]] = {
    "deepseek-v4-flash":    {"window": 1_000_000, "max": 800_000, "trim": 600_000},
    "deepseek-v4-pro":      {"window": 1_000_000, "max": 800_000, "trim": 600_000},
    "deepseek-chat":        {"window": 1_000_000, "max": 800_000, "trim": 600_000},  # 旧名
    "deepseek-reasoner":    {"window": 1_000_000, "max": 800_000, "trim": 600_000},  # 旧名
    "gpt-4o":               {"window":   128_000, "max": 100_000, "trim":  80_000},
    "gpt-4o-mini":          {"window":   128_000, "max": 100_000, "trim":  80_000},
    "gpt-4-turbo":          {"window":   128_000, "max": 100_000, "trim":  80_000},
    "claude-3-5-sonnet":    {"window":   200_000, "max": 160_000, "trim": 120_000},
    "claude-3-opus":        {"window":   200_000, "max": 160_000, "trim": 120_000},
}


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

    # ============================================================
    # 上下文窗口（根据当前模型自动匹配）
    # ============================================================

    @classmethod
    def _match_model_config(cls) -> dict[str, int]:
        """根据当前模型名匹配已知配置。优先级：.env 手动覆盖 > 代码内置匹配 > 保守默认"""
        # 1. .env 里手动覆盖（适配新模型不用改代码）
        env_window = os.getenv("UKI_CONTEXT_WINDOW", "")
        env_max = os.getenv("UKI_CONTEXT_MAX", "")
        env_trim = os.getenv("UKI_CONTEXT_TRIM", "")
        if env_window and env_max and env_trim:
            return {
                "window": int(env_window),
                "max": int(env_max),
                "trim": int(env_trim),
            }

        # 2. 代码内置的已知模型匹配
        for name, cfg in MODEL_WINDOW_CONFIG.items():
            if name in cls.model.lower():
                return cfg

        # 3. 未知模型：保守假设 16K
        return {"window": 16_000, "max": 12_000, "trim": 8_000}

    @classmethod
    def context_window(cls) -> int:
        """模型总上下文窗口大小"""
        return cls._match_model_config()["window"]

    @classmethod
    def max_context_tokens(cls) -> int:
        """推荐上限（80%）：超过此值时触发警告，但不会强制压缩"""
        return cls._match_model_config()["max"]

    @classmethod
    def trim_threshold(cls) -> int:
        """自动压缩阈值（60%）：超过此值时强制裁剪中间消息"""
        return cls._match_model_config()["trim"]

    @classmethod
    def summary(cls) -> str:
        """打印当前配置摘要"""
        key_status = "已配置 ✓" if cls.is_ready() else "未配置 ✗（请复制 .env.example 为 .env 并填 API key）"
        cfg = cls._match_model_config()
        return (
            f"API Key: {key_status}\n"
            f"接口地址: {cls.base_url}\n"
            f"模型: {cls.model}\n"
            f"上下文窗口: {cfg['window'] // 1000}K tokens | "
            f"推荐上限: {cfg['max'] // 1000}K | "
            f"裁剪阈值: {cfg['trim'] // 1000}K"
        )
