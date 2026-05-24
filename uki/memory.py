"""
Uki 记忆系统（第 16 课 - v2 SQLite 版）

跨会话长期记忆的存储、检索和注入。

记忆规范：
- type: fact / preference / pattern / plugin_suggestion
- subject: 简短标识（如 "project_path", "language_preference"）
- value: 具体内容
- confidence: 置信度 0.0-1.0（LLM 回顾自动提取的 < 用户手动标记的）

生命周期：
  产生 → 规范(type/subject/value) → 去重(type+subject) → 存入 SQLite
  → 分层检索(importance优先) → LLM反馈 → 衰减 → 冷存储归档

存储：SQLite（项目目录 .uki_memory.db）
embedding 存储为 JSON TEXT 列，搜索时在应用层做余弦相似度。
"""

from __future__ import annotations

import json
import os
import re
import time
import math
import sqlite3
from pathlib import Path


# 存储位置
DB_FILE = Path.home() / ".uki_memory.db"
OLD_JSON_FILE = Path.home() / ".uki_memory.json"

# 每次注入上限
MAX_INJECT_MEMORIES = 5

# 衰减参数
MAX_AGE_DAYS = 90
MIN_IMPORTANCE = 2
DECAY_RATE = 0.5


# ============================================================
# SQLite Schema
# ============================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    subject TEXT NOT NULL,
    value TEXT NOT NULL,
    content TEXT,
    tags TEXT DEFAULT '[]',
    importance INTEGER DEFAULT 5,
    confidence REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    last_accessed REAL,
    embedding TEXT,
    created_at REAL,
    updated_at REAL,
    source TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_type_subject ON memories(type, subject);
CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance);
CREATE INDEX IF NOT EXISTS idx_status ON memories(status);

CREATE TABLE IF NOT EXISTS cold_memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    subject TEXT NOT NULL,
    value TEXT NOT NULL,
    content TEXT,
    tags TEXT DEFAULT '[]',
    importance INTEGER DEFAULT 5,
    confidence REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    last_accessed REAL,
    embedding TEXT,
    created_at REAL,
    updated_at REAL,
    source TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'archived',
    archived_at REAL
);

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
"""


# ============================================================
# MemoryStore
# ============================================================

class MemoryStore:
    """记忆存储引擎。SQLite 后台，embedding 搜索在应用层。"""

    EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_FILE
        self._conn: sqlite3.Connection | None = None
        self._model = None
        self._model_available = True
        self._decay_checked = False
        self._init_db()
        self._migrate_from_json()

    # ============================================================
    # SQLite 初始化
    # ============================================================

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

    def _migrate_from_json(self):
        """从旧版 ~/.uki_memory.json 迁移到 SQLite"""
        if not OLD_JSON_FILE.exists():
            return
        try:
            old_data = json.loads(OLD_JSON_FILE.read_text(encoding="utf-8"))
            if not isinstance(old_data, list) or not old_data:
                return

            conn = self._get_conn()
            migrated = 0
            for item in old_data:
                content = item.get("content", "")
                tags = item.get("tags", [])
                # 推断 type：有 tags 的偏 fact，标签含"偏好/习惯"的归 preference
                mem_type = "preference" if any(
                    t in str(tags).lower() for t in ["偏好", "习惯", "喜欢", "preference"]
                ) else "fact"
                subject = _infer_subject(content, mem_type)
                self._insert_row(conn, {
                    "id": item.get("id", _short_id()),
                    "type": mem_type,
                    "subject": subject,
                    "value": content,
                    "content": content,
                    "tags": json.dumps(item.get("tags", []), ensure_ascii=False),
                    "importance": item.get("importance", 5),
                    "confidence": 0.8,
                    "access_count": item.get("access_count", 0),
                    "last_accessed": item.get("last_accessed", time.time()),
                    "embedding": json.dumps(item.get("embedding")) if item.get("embedding") else None,
                    "created_at": item.get("timestamp", time.time()),
                    "updated_at": time.time(),
                    "source": "migrated",
                    "status": "active",
                }, on_conflict="ignore")
                migrated += 1

            conn.commit()
            if migrated:
                # 备份旧文件后删除
                backup = OLD_JSON_FILE.with_suffix(".json.bak")
                OLD_JSON_FILE.rename(backup)
                print(f"[memory] 已从 JSON 迁移 {migrated} 条记忆到 SQLite")
        except (json.JSONDecodeError, OSError, sqlite3.Error):
            pass

    # ============================================================
    # CRUD
    # ============================================================

    def add(self, content: str, tags: list[str] | None = None,
            importance: int = 5, confidence: float = 0.5,
            mem_type: str = "fact", subject: str = "",
            source: str = "manual") -> dict:
        """添加一条记忆。同 type+subject 会合并而非新增（去重）。"""
        conn = self._get_conn()
        now = time.time()

        if not subject:
            subject = _infer_subject(content, mem_type)

        # 去重检查
        existing = self._find_by_subject(mem_type, subject)
        if existing:
            existing_conf = existing.get("confidence", 0.5)
            if confidence >= existing_conf:
                # 新记忆置信度更高 → 覆盖 value
                conn.execute(
                    """UPDATE memories SET
                        value = ?, content = ?, tags = ?,
                        importance = MAX(importance, ?),
                        confidence = ?,
                        updated_at = ?,
                        source = source || ',merged'
                    WHERE id = ?""",
                    (content.strip(), content.strip(),
                     json.dumps(tags or [], ensure_ascii=False),
                     importance, confidence, now, existing["id"]),
                )
            else:
                # 新记忆置信度更低 → 不覆盖 value，只升 importance + 记录冲突来源
                conn.execute(
                    """UPDATE memories SET
                        importance = MAX(importance, ?),
                        updated_at = ?,
                        source = source || ',conflict_lowconf'
                    WHERE id = ?""",
                    (importance, now, existing["id"]),
                )
            conn.commit()
            return dict(self._get_row(existing["id"]))

        # 新记忆
        mem_id = _short_id()
        embedding = self._encode(content.strip())
        row = {
            "id": mem_id, "type": mem_type, "subject": subject,
            "value": content.strip(), "content": content.strip(),
            "tags": json.dumps(tags or [], ensure_ascii=False),
            "importance": min(max(importance, 1), 10),
            "confidence": min(max(confidence, 0.0), 1.0),
            "access_count": 0, "last_accessed": now,
            "embedding": json.dumps(embedding) if embedding else None,
            "created_at": now, "updated_at": now,
            "source": source, "status": "active",
        }
        self._insert_row(conn, row)
        conn.commit()
        return dict(self._get_row(mem_id))

    def remove(self, query: str) -> int:
        """删除 content 中包含 query 的记忆。返回删除数量。"""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM memories WHERE status='active' AND (content LIKE ? OR value LIKE ?)",
            (f"%{query}%", f"%{query}%"),
        )
        conn.commit()
        return cursor.rowcount

    def get_all(self, status: str = "active") -> list[dict]:
        """返回全部活跃记忆"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE status=? ORDER BY importance DESC, updated_at DESC",
            (status,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def search(self, query: str, limit: int = MAX_INJECT_MEMORIES,
               min_importance: int = 0) -> list[dict]:
        """
        分层检索相关记忆。
        优先语义搜索，fallback 关键词匹配。
        按 importance 降序，limit 条。
        """
        conn = self._get_conn()

        # 每个 session 运行一次衰减
        if not self._decay_checked:
            self._apply_decay()
            self._decay_checked = True

        # 取活跃记忆
        rows = conn.execute(
            "SELECT * FROM memories WHERE status='active' AND importance >= ?",
            (min_importance,),
        ).fetchall()

        if not rows:
            return []

        memories = [_row_to_dict(r) for r in rows]

        # 语义搜索
        query_vec = self._encode(query)
        if query_vec is not None and any(m.get("embedding") for m in memories):
            results = self._semantic_search(query_vec, memories, limit)
        else:
            results = self._keyword_search(query, memories, limit)

        # 更新访问计数
        now = time.time()
        ids = [m["id"] for m in results]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id IN ({placeholders})",
                [now] + ids,
            )
            conn.commit()

        return results

    def count(self) -> int:
        conn = self._get_conn()
        return conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status='active'"
        ).fetchone()[0]

    # ============================================================
    # 分层检索
    # ============================================================

    def search_layered(self, query: str) -> list[dict]:
        """
        分层检索：
        第一层: importance >= 7 → top-2
        不够 → 第二层: importance >= 4 → top-3 追加
        还不够 → 全量 top-5
        """
        results = self.search(query, limit=2, min_importance=7)
        if len(results) < 2:
            more = self.search(query, limit=3, min_importance=4)
            for m in more:
                if m["id"] not in {r["id"] for r in results}:
                    results.append(m)
        if len(results) < 3:
            more = self.search(query, limit=5)
            for m in more:
                if m["id"] not in {r["id"] for r in results}:
                    results.append(m)
        return results[:MAX_INJECT_MEMORIES]

    # ============================================================
    # LLM 反馈
    # ============================================================

    def mark_unhelpful(self, memory_id: str) -> bool:
        """
        LLM 反馈某条记忆无用。
        importance 骤降到 0，下次衰减时会被清理到冷存储。
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE memories SET importance=0, updated_at=? WHERE id=? AND status='active'",
            (time.time(), memory_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def mark_helpful(self, memory_id: str) -> bool:
        """LLM 反馈某条记忆有用，提升 importance"""
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE memories SET importance=MIN(10, importance+2), access_count=access_count+1, updated_at=? WHERE id=? AND status='active'",
            (time.time(), memory_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    # ============================================================
    # 冷存储
    # ============================================================

    def archive(self, memory_ids: list[str] | None = None, query: str | None = None) -> int:
        """
        将记忆移到冷存储。
        - 传 memory_ids: 归档指定记忆
        - 传 query: 归档包含关键词的记忆
        - 都不传: 归档 importance < MIN_IMPORTANCE 的记忆
        """
        conn = self._get_conn()
        now = time.time()
        moved = 0

        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            conn.execute(
                f"""INSERT INTO cold_memories SELECT *, ? FROM memories WHERE id IN ({placeholders})""",
                [now] + memory_ids,
            )
            cursor = conn.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})",
                memory_ids,
            )
            moved = cursor.rowcount
        elif query:
            conn.execute(
                "INSERT INTO cold_memories SELECT *, ? FROM memories WHERE status='active' AND (content LIKE ? OR value LIKE ?)",
                (now, f"%{query}%", f"%{query}%"),
            )
            cursor = conn.execute(
                "DELETE FROM memories WHERE status='active' AND (content LIKE ? OR value LIKE ?)",
                (f"%{query}%", f"%{query}%"),
            )
            moved = cursor.rowcount
        else:
            conn.execute(
                "INSERT INTO cold_memories SELECT *, ? FROM memories WHERE status='active' AND importance < ?",
                (now, MIN_IMPORTANCE),
            )
            cursor = conn.execute(
                "DELETE FROM memories WHERE status='active' AND importance < ?",
                (MIN_IMPORTANCE,),
            )
            moved = cursor.rowcount

        conn.commit()
        if moved:
            print(f"[memory] {moved} 条记忆已归档到冷存储")
        return moved

    def get_cold_count(self) -> int:
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM cold_memories").fetchone()[0]

    # ============================================================
    # 注入
    # ============================================================

    def inject_prompt(self, user_message: str) -> str:
        """生成注入到 system prompt 的记忆片段"""
        relevant = self.search_layered(user_message)
        if not relevant:
            return ""

        lines = ["", "## 相关记忆"]
        for i, mem in enumerate(relevant, 1):
            tags = json.loads(mem.get("tags", "[]"))
            tags_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"{i}.{tags_str} {mem['content'] or mem['value']}")

        return "\n".join(lines)

    # ============================================================
    # Embedding
    # ============================================================

    def _get_model(self):
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
            print(f"[memory] embedding 模型未安装，使用关键词匹配: {e}")
            return None

    def _encode(self, text: str) -> list[float] | None:
        model = self._get_model()
        if model is None:
            return None
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b))

    # ============================================================
    # 搜索实现
    # ============================================================

    def _semantic_search(self, query_vec: list[float], memories: list[dict], limit: int) -> list[dict]:
        scored: list[tuple[float, dict]] = []
        for mem in memories:
            emb_json = mem.get("embedding")
            if not emb_json:
                continue
            try:
                emb = json.loads(emb_json) if isinstance(emb_json, str) else emb_json
            except (json.JSONDecodeError, TypeError):
                continue
            if not emb:
                continue
            similarity = self._cosine_similarity(query_vec, emb)
            if similarity > 0.2:
                score = similarity * 10 + mem.get("importance", 5) * 0.1
                scored.append((score, mem))
        scored.sort(key=lambda x: -x[0])
        return [mem for _, mem in scored[:limit]]

    def _keyword_search(self, query: str, memories: list[dict], limit: int) -> list[dict]:
        query_words = set(_tokenize(query))
        if not query_words:
            return []
        scored: list[tuple[int, dict]] = []
        for mem in memories:
            score = _relevance_score(query_words, mem)
            if score > 0:
                scored.append((score, mem))
        scored.sort(key=lambda x: (-x[0], x[1].get("importance", 5)))
        return [mem for _, mem in scored[:limit]]

    # ============================================================
    # 重要性衰减
    # ============================================================

    def _apply_decay(self):
        conn = self._get_conn()
        now = time.time()
        aged_ago = now - (MAX_AGE_DAYS * 24 * 3600)

        # 衰减：从未访问过且超过 MAX_AGE_DAYS 的记忆重要性 -0.5
        conn.execute(
            """UPDATE memories SET importance = MAX(0, importance - ?)
               WHERE status='active' AND access_count=0 AND created_at < ?""",
            (DECAY_RATE, aged_ago),
        )

        # 标记 important=0 的为待归档
        stale = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status='active' AND importance=0"
        ).fetchone()[0]

        # 最近频繁访问的：升 importance
        recent = now - (4 * 7 * 24 * 3600)  # 4 周
        conn.execute(
            """UPDATE memories SET importance = MIN(10, importance + 1)
               WHERE status='active' AND access_count >= 5 AND last_accessed > ?""",
            (recent,),
        )

        conn.commit()

        if stale > 0:
            import_count = self.archive()
            if import_count > 0:
                print(f"[memory] 衰减: {import_count} 条低重要性记忆已归档")

    # ============================================================
    # SQLite 辅助
    # ============================================================

    def _insert_row(self, conn, row: dict, on_conflict: str = "ignore"):
        """插入一行，on_conflict='ignore' 跳过重复"""
        columns = list(row.keys())
        placeholders = ",".join("?" for _ in columns)
        cols = ",".join(columns)
        or_ignore = "OR IGNORE" if on_conflict == "ignore" else ""
        conn.execute(
            f"INSERT {or_ignore} INTO memories ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )

    def _get_row(self, mem_id: str) -> sqlite3.Row | None:
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM memories WHERE id=?", (mem_id,)
        ).fetchone()

    def _find_by_subject(self, mem_type: str, subject: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memories WHERE type=? AND subject=? AND status='active'",
            (mem_type, subject),
        ).fetchone()
        return _row_to_dict(row) if row else None


# ============================================================
# 工具函数
# ============================================================

def _short_id() -> str:
    import random, string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    tokens.extend(re.findall(r"[\u4e00-\u9fff]", text))
    tokens.extend(re.findall(r"[a-zA-Z]+", text.lower()))
    tokens.extend(re.findall(r"\d+", text))
    return tokens


def _relevance_score(query_words: set, mem: dict) -> int:
    score = 0
    tags = mem.get("tags", "[]")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    for tag in tags:
        tag_lower = tag.lower()
        for qw in query_words:
            if qw in tag_lower:
                score += 10
    content = (mem.get("content") or mem.get("value", "")).lower()
    for qw in query_words:
        if qw in content:
            score += 3
    return score + mem.get("importance", 5)


def _infer_subject(content: str, mem_type: str) -> str:
    """从内容推断 subject 标识"""
    content_lower = content.lower()
    # 常用关键词 → subject 映射
    patterns = [
        (r"项目|project|路径|path", "project_path"),
        (r"godot|引擎|游戏引擎|game engine", "tech_stack_godot"),
        (r"语言|language|中文|英文|english|chinese", "language_preference"),
        (r"工作|work|早上|上午|晚上|时间", "work_schedule"),
        (r"习惯|偏好|喜欢|prefer", "user_preference"),
        (r"python|rust|go|node|java|typescript", "programming_language"),
        (r"api.key|token|密钥|凭证", "credentials"),
        (r"bug|fix|debug|修复", "bug_record"),
        (r"插件|plugin|skill", "plugin_related"),
    ]
    for pattern, subject in patterns:
        if re.search(pattern, content_lower):
            return subject
    # 默认：取前 8 个字的 hash
    import hashlib
    return "mem_" + hashlib.md5(content.encode()).hexdigest()[:8]


def _row_to_dict(row: sqlite3.Row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    # 还原 JSON 字段
    for key in ("tags", "embedding"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _format_time(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ============================================================
# S4: 插件自动建议（保留，单开函数不变）
# ============================================================

SUGGEST_THRESHOLD = 3
_COMMAND_HISTORY: list[dict] = []


def record_command(command: str) -> None:
    _COMMAND_HISTORY.append({"command": command, "timestamp": time.time()})
    if len(_COMMAND_HISTORY) > 50:
        _COMMAND_HISTORY.pop(0)


def check_suggestion(memory: MemoryStore) -> str | None:
    if len(_COMMAND_HISTORY) < SUGGEST_THRESHOLD:
        return None
    patterns: dict[str, int] = {}
    for entry in _COMMAND_HISTORY:
        pattern = _extract_pattern(entry["command"])
        if pattern:
            patterns[pattern] = patterns.get(pattern, 0) + 1
    for pattern, count in patterns.items():
        if count >= SUGGEST_THRESHOLD:
            if _already_suggested_db(memory, pattern):
                continue
            return (
                f"💡 我注意到你经常执行 `{pattern}` 这类命令。"
                f"要不要我帮你生成一个插件，以后一条命令就能完成？"
            )
    return None


def _extract_pattern(command: str) -> str:
    if "pip install" in command:
        return "pip install <package>"
    if "python -c" in command:
        return "python -c <script>"
    if re.search(r"python\s+\S+\.py", command):
        return "python <script.py>"
    parts = command.split()
    key = " ".join(parts[:4])
    return key[:40] + "..." if len(key) > 40 else key


def _already_suggested_db(memory: MemoryStore, pattern: str) -> bool:
    conn = memory._get_conn()
    row = conn.execute(
        "SELECT 1 FROM memories WHERE type='plugin_suggestion' AND content LIKE ?",
        (f"%{pattern}%",),
    ).fetchone()
    return row is not None
