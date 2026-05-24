"""
Uki 记忆系统（第 16 课）

跨会话长期记忆的存储、检索和注入。

每条记忆是一个记录：
- content: 记忆内容
- tags: 标签列表（用于分类和检索）
- timestamp: 创建时间
- importance: 重要程度 1-10
- embedding: 语义向量（自动生成，用于语义搜索）

存储位置：~/.uki_memory.json

检索策略：
- 优先语义搜索（本地 embedding 模型，余弦相似度）
- 模型未就绪时 fallback 到关键词匹配
"""

from __future__ import annotations

import json
import os
import re
import time
import math
from pathlib import Path


MEMORY_FILE = Path.home() / ".uki_memory.json"

# 每次注入到 system prompt 的记忆条数上限
MAX_INJECT_MEMORIES = 5


class MemoryStore:
    """记忆存储引擎。优先语义搜索，fallback 关键词匹配。"""

    # 本地 embedding 模型名
    EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

    # 重要性衰减参数
    MAX_AGE_DAYS = 90      # 超过此天数的记忆会被衰减
    MIN_IMPORTANCE = 2     # 低于此值的记忆将被删除
    DECAY_RATE = 0.5       # 每周衰减一半重要性（无命中时）

    def __init__(self, path: Path | None = None):
        self.file = path or MEMORY_FILE
        self._memories: list[dict] = []
        self._model = None  # 懒加载
        self._model_available = True
        self._decay_checked = False  # 每个 session 只衰减一次
        self._load()

    # ============================================================
    # Embedding 模型（懒加载）
    # ============================================================

    def _get_model(self):
        """懒加载 embedding 模型。失败返回 None"""
        if self._model is not None:
            return self._model
        if not self._model_available:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.EMBEDDING_MODEL)
            return self._model
        except (ImportError, OSError) as e:
            self._model_available = False
            print(f"[memory] embedding 模型加载失败，使用关键词匹配: {e}")
            return None

    def _encode(self, text: str) -> list[float] | None:
        """将文本编码为向量"""
        model = self._get_model()
        if model is None:
            return None
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """余弦相似度（假设向量已归一化，直接点积）"""
        if not a or not b or len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b))

    # ============================================================
    # 增删查
    # ============================================================

    def add(self, content: str, tags: list[str] | None = None, importance: int = 5) -> dict:
        """添加一条记忆，自动生成 embedding 向量"""
        mem = {
            "id": _short_id(),
            "content": content.strip(),
            "tags": tags or [],
            "importance": min(max(importance, 1), 10),
            "timestamp": time.time(),
            "timestamp_str": _format_time(time.time()),
            "embedding": self._encode(content.strip()),
            "access_count": 0,       # 被检索到的次数
            "last_accessed": time.time(),
        }
        self._memories.append(mem)
        self._save()
        return mem

    def remove(self, query: str) -> int:
        """删除匹配 query 的记忆（模糊匹配 content），返回删除数量"""
        query_lower = query.lower()
        before = len(self._memories)
        self._memories = [
            m for m in self._memories
            if query_lower not in m["content"].lower()
        ]
        removed = before - len(self._memories)
        if removed:
            self._save()
        return removed

    def get_all(self) -> list[dict]:
        """返回全部记忆"""
        return list(self._memories)

    def search(self, query: str, limit: int = MAX_INJECT_MEMORIES) -> list[dict]:
        """
        检索相关记忆。
        优先语义搜索（embedding + 余弦相似度），
        模型未就绪时 fallback 到关键词匹配。
        自动记录访问次数，定期衰减旧记忆。
        """
        if not self._memories:
            return []

        # 每个 session 运行一次衰减
        if not self._decay_checked:
            self._apply_decay()
            self._decay_checked = True

        # 尝试语义搜索
        query_vec = self._encode(query)
        if query_vec is not None and any(m.get("embedding") for m in self._memories):
            results = self._semantic_search(query_vec, limit)
        else:
            results = self._keyword_search(query, limit)

        # 更新访问计数
        now = time.time()
        for mem in results:
            mem["access_count"] = mem.get("access_count", 0) + 1
            mem["last_accessed"] = now
        if results:
            self._save()

        return results

    def _semantic_search(self, query_vec: list[float], limit: int) -> list[dict]:
        """基于 embedding 余弦相似度的语义搜索"""
        scored: list[tuple[float, dict]] = []
        for mem in self._memories:
            emb = mem.get("embedding")
            if emb is None:
                continue
            similarity = self._cosine_similarity(query_vec, emb)
            # 加权：相似度 + 重要性
            score = similarity * 10 + mem.get("importance", 5) * 0.1
            if similarity > 0.2:  # 过滤明显无关的
                scored.append((score, mem))

        scored.sort(key=lambda x: -x[0])
        return [mem for _, mem in scored[:limit]]

    def _keyword_search(self, query: str, limit: int) -> list[dict]:
        """关键词匹配（fallback）"""
        query_words = set(_tokenize(query))
        if not query_words:
            return []

        scored: list[tuple[int, dict]] = []
        for mem in self._memories:
            score = _relevance_score(query_words, mem)
            if score > 0:
                scored.append((score, mem))

        scored.sort(key=lambda x: (-x[0], x[1].get("importance", 5)))
        return [mem for _, mem in scored[:limit]]

    def count(self) -> int:
        return len(self._memories)

    # ============================================================
    # 重要性衰减
    # ============================================================

    def _apply_decay(self):
        """
        衰减记忆的重要性。
        - 从未被访问过且超过 MAX_AGE_DAYS → 重要性减 DECAY_RATE
        - 低于 MIN_IMPORTANCE → 删除
        """
        now = time.time()
        seven_days = 7 * 24 * 3600
        aged_day = self.MAX_AGE_DAYS * 24 * 3600

        to_remove: list[str] = []
        for mem in self._memories:
            age = now - mem["timestamp"]
            access_count = mem.get("access_count", 0)

            # 从未被访问过的旧记忆：衰减
            if access_count == 0 and age > aged_day:
                mem["importance"] = mem.get("importance", 5) - self.DECAY_RATE

            # 低重要性：删除
            if mem.get("importance", 5) < self.MIN_IMPORTANCE:
                to_remove.append(mem["id"])

            # 最近被频繁访问的记忆：升重要性
            elif access_count >= 5 and age < seven_days * 4:
                mem["importance"] = min(10, mem.get("importance", 5) + 1)

        if to_remove:
            self._memories = [m for m in self._memories if m["id"] not in to_remove]
            print(f"[memory] 衰减清理了 {len(to_remove)} 条低重要性记忆")

        if to_remove:
            self._save()

    # ============================================================
    # 持久化
    # ============================================================

    def _load(self):
        if self.file.exists():
            try:
                data = json.loads(self.file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._memories = data
            except (json.JSONDecodeError, OSError):
                self._memories = []

    def _save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(
            json.dumps(self._memories, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ============================================================
    # 注入
    # ============================================================

    def inject_prompt(self, user_message: str) -> str:
        """
        生成注入到 system prompt 的记忆片段。
        空消息返回空字符串。
        """
        relevant = self.search(user_message)
        if not relevant:
            return ""

        lines = ["", "## 相关记忆"]
        for i, mem in enumerate(relevant, 1):
            tags_str = f" [{', '.join(mem['tags'])}]" if mem.get("tags") else ""
            lines.append(f"{i}.{tags_str} {mem['content']}")

        return "\n".join(lines)


# ============================================================
# 内部工具
# ============================================================

def _short_id() -> str:
    """6 位短 ID"""
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _tokenize(text: str) -> list[str]:
    """中文+英文混合分词"""
    tokens: list[str] = []
    # 提取中文单字
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.extend(chinese)
    # 提取英文单词
    english = re.findall(r"[a-zA-Z]+", text.lower())
    tokens.extend(english)
    # 提取数字
    numbers = re.findall(r"\d+", text)
    tokens.extend(numbers)
    return tokens


def _relevance_score(query_words: set, mem: dict) -> int:
    """计算记忆与查询的相关性分数"""
    score = 0

    # 标签精确匹配（权重最高）
    mem_tags = mem.get("tags", [])
    for tag in mem_tags:
        tag_lower = tag.lower()
        for qw in query_words:
            if qw in tag_lower:
                score += 10

    # 内容关键词重叠
    content_lower = mem["content"].lower()
    for qw in query_words:
        if qw in content_lower:
            score += 3

    # 重要性加权
    importance = mem.get("importance", 5)
    score += importance

    return score


def _format_time(ts: float) -> str:
    """时间戳转为可读字符串"""
    from datetime import datetime
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")


# ============================================================
# S4: 插件自动建议（重复脚本检测）
# ============================================================

# 建议阈值：同一脚本模式出现 N 次后触发建议
SUGGEST_THRESHOLD = 3

# 记录最近的命令执行
_COMMAND_HISTORY: list[dict] = []


def record_command(command: str) -> None:
    """记录一次命令执行，用于后续模式检测"""
    _COMMAND_HISTORY.append({
        "command": command,
        "timestamp": time.time(),
    })
    # 只保留最近 50 条
    if len(_COMMAND_HISTORY) > 50:
        _COMMAND_HISTORY.pop(0)


def check_suggestion(memory: MemoryStore) -> str | None:
    """
    检查是否应该建议创建插件。
    返回建议文本，或 None（不需要建议）。
    """
    if len(_COMMAND_HISTORY) < SUGGEST_THRESHOLD:
        return None

    # 提取命令中的关键模式（去掉参数，保留命令名 + 核心操作）
    patterns: dict[str, int] = {}
    for entry in _COMMAND_HISTORY:
        pattern = _extract_pattern(entry["command"])
        if pattern:
            patterns[pattern] = patterns.get(pattern, 0) + 1

    # 找到重复最多的模式
    for pattern, count in patterns.items():
        if count >= SUGGEST_THRESHOLD:
            # 检查用户是否已经拒绝了该建议
            if _already_suggested(memory, pattern):
                continue
            suggestion = (
                f"💡 我注意到你经常执行 `{pattern}` 这类命令。"
                f"要不要我帮你生成一个插件，以后一条命令就能完成？"
            )
            return suggestion

    return None


def _extract_pattern(command: str) -> str:
    """从命令中提取模式标识"""
    # 去掉 pip install 后的包名
    if "pip install" in command:
        return "pip install <package>"
    # 去掉 python -c 后的代码
    if "python -c" in command:
        return "python -c <script>"
    # 去掉文件路径参数
    if re.search(r"python\s+\S+\.py", command):
        return "python <script.py>"
    # 通用：保留命令前 3 个词
    parts = command.split()
    key = " ".join(parts[:4])
    if len(key) > 40:
        key = key[:40] + "..."
    return key


def _already_suggested(memory: MemoryStore, pattern: str) -> bool:
    """检查该模式是否已经被建议过"""
    for mem in memory.get_all():
        if "插件建议" in mem.get("content", "") and pattern in mem["content"]:
            return True
    return False
