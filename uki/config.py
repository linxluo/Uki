"""
Uki 的配置管理

读取 .env 文件和环境变量，管理所有配置项。
对应 Claude Code 的 settings.json 和 API 配置。
"""

import os
from dotenv import load_dotenv
from uki.model_fetcher import load_model_cache, match_model_window

# 加载 .env 文件中的环境变量
load_dotenv()

# ============================================================
# 已知模型的上下文窗口配置
# ============================================================
# 每个条目：总窗口 → 推荐的上限 (80%) → 触发压缩的阈值 (60%)
# 新模型在这里加一行即可。

MODEL_WINDOW_CONFIG: dict[str, dict[str, int]] = {
    "deepseek-v4-flash":    {"window": 1_000_000, "max": 800_000, "trim": 600_000, "summary": 8_000},
    "deepseek-v4-pro":      {"window": 1_000_000, "max": 800_000, "trim": 600_000, "summary": 8_000},
    "deepseek-chat":        {"window": 1_000_000, "max": 800_000, "trim": 600_000, "summary": 8_000},
    "deepseek-reasoner":    {"window": 1_000_000, "max": 800_000, "trim": 600_000, "summary": 8_000},
    "gpt-4o":               {"window":   128_000, "max": 100_000, "trim":  80_000, "summary": 8_000},
    "gpt-4o-mini":          {"window":   128_000, "max": 100_000, "trim":  80_000, "summary": 8_000},
    "gpt-4-turbo":          {"window":   128_000, "max": 100_000, "trim":  80_000, "summary": 8_000},
    "claude-3-5-sonnet":    {"window":   200_000, "max": 160_000, "trim": 120_000, "summary": 8_000},
    "claude-3-opus":        {"window":   200_000, "max": 160_000, "trim": 120_000, "summary": 8_000},
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
        """
        根据当前模型名匹配上下文窗口配置。
        优先级：.env 手动覆盖 > OpenRouter API 缓存 > 代码字典 > 保守默认
        """
        # 1. .env 手动覆盖
        env_window = os.getenv("UKI_CONTEXT_WINDOW", "")
        env_max = os.getenv("UKI_CONTEXT_MAX", "")
        env_trim = os.getenv("UKI_CONTEXT_TRIM", "")
        env_summary = os.getenv("UKI_SUMMARY_THRESHOLD", "")
        if env_window and env_max and env_trim:
            cfg = {
                "window": int(env_window),
                "max": int(env_max),
                "trim": int(env_trim),
            }
            cfg["summary"] = int(env_summary) if env_summary else min(int(env_window) * 0.3, 8000)
            return cfg

        # 2. OpenRouter API 缓存（覆盖范围最广，自动感知新模型）
        cache = load_model_cache()
        if cache:
            api_match = match_model_window(cls.model, cache)
            if api_match:
                return api_match

        # 3. 代码内置字典（已知模型的后备）
        for name, cfg in MODEL_WINDOW_CONFIG.items():
            if name in cls.model.lower():
                return cfg

        # 4. 保守默认
        return {"window": 16_000, "max": 12_000, "trim": 8_000, "summary": 4_800}

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
    def summary_threshold(cls) -> int:
        """LLM 自动总结阈值：对话历史超过此 token 数时触发总结"""
        return cls._match_model_config()["summary"]

    @classmethod
    def git_context_enabled(cls) -> bool:
        """是否在 system prompt 中注入 Git 状态（通过 UKI_GIT_CONTEXT=1 开启）"""
        return os.getenv("UKI_GIT_CONTEXT", "0") == "1"

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
            f"裁剪阈值: {cfg['trim'] // 1000}K | "
            f"总结阈值: {cfg['summary'] // 1000}K"
        )
