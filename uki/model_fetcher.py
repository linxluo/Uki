"""
模型信息获取器

从 OpenRouter API 拉取模型列表，获取每个模型的 context_length。
本地缓存 7 天，避免每次都请求。
"""

import json
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# 缓存文件路径
CACHE_FILE = Path("uki_model_cache.json")
CACHE_TTL = 7 * 24 * 3600  # 7 天

# OpenRouter 模型列表 API（免费，无需 key）
MODELS_API_URL = "https://openrouter.ai/api/v1/models"


def load_model_cache() -> dict[str, int]:
    """
    加载模型缓存。
    优先级：本地缓存（未过期）> 在线获取 > 空字典（降级）
    返回 {model_id_lower: context_length} 映射。
    """
    # 1. 尝试读本地缓存
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            age = time.time() - data.get("fetched_at", 0)
            if age < CACHE_TTL:
                return data.get("models", {})
        except (json.JSONDecodeError, KeyError):
            pass

    # 2. 在线获取
    try:
        models = _fetch_models_online()
        if models:
            _save_cache(models)
            return models
    except Exception:
        pass

    # 3. 降级：缓存过期且在线获取失败，仍用旧缓存
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            return data.get("models", {})
        except Exception:
            pass

    return {}


def _fetch_models_online() -> dict[str, int]:
    """从 OpenRouter 在线获取模型列表"""
    req = Request(MODELS_API_URL, headers={"User-Agent": "UkiAgent/1.0"})
    with urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8")
        data = json.loads(body)

    models = {}
    for item in data.get("data", []):
        model_id = item.get("id", "").lower()
        ctx_len = item.get("context_length", 0)
        if model_id and ctx_len:
            models[model_id] = ctx_len

    return models


def _save_cache(models: dict[str, int]):
    """写入本地缓存"""
    data = {
        "fetched_at": time.time(),
        "models": models,
    }
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def match_model_window(model_name: str, cache: dict[str, int]) -> dict[str, int] | None:
    """
    根据模型名在缓存中匹配上下文窗口。
    匹配策略：精确匹配 > 包含匹配 > 关键词匹配。
    返回 {"window": int, "max": int, "trim": int} 或 None。
    """
    model_lower = model_name.lower().strip()

    # 精确匹配
    if model_lower in cache:
        return _window_config(cache[model_lower])

    # 包含匹配：缓存 key 包含用户输入的模型名
    for cached_id, ctx_len in cache.items():
        if model_lower in cached_id:
            return _window_config(ctx_len)

    # 关键词匹配：提取模型名中的关键词（如 deepseek, gpt, claude）
    keywords = _extract_keywords(model_lower)
    for cached_id, ctx_len in cache.items():
        for kw in keywords:
            if kw in cached_id and len(kw) > 2:
                return _window_config(ctx_len)

    return None


def _window_config(context_length: int) -> dict[str, int]:
    """根据总窗口计算推荐上限、裁剪阈值和总结阈值"""
    return {
        "window": context_length,
        "max": int(context_length * 0.8),
        "trim": int(context_length * 0.6),
        "summary": min(int(context_length * 0.3), 8000),
    }


def _extract_keywords(model_name: str) -> list[str]:
    """从模型名中提取关键词，用于模糊匹配"""
    # deepseek-v4-flash → ["deepseek", "v4", "flash"]
    parts = model_name.replace("-", " ").replace("/", " ").replace(".", " ").split()
    return [p for p in parts if len(p) > 1 and not p.isdigit()]
