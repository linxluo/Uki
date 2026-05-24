"""测试 embedding API"""
import sys
sys.path.insert(0, ".")
from pathlib import Path
from uki.memory import MemoryStore

test_db = Path("./_test_embed.db")
if test_db.exists():
    test_db.unlink()

from uki.config import Config
from openai import OpenAI

# 注入 API 客户端
client = OpenAI(api_key=Config.api_key, base_url=Config.base_url)
MemoryStore.set_embedding_client(client)

mem = MemoryStore(db_path=test_db)

# 测试: 添加几条记忆（会调 embedding API）
print("=== 添加记忆（调 embedding API）===")
try:
    mem.add("用户使用 Godot 4.x 开发点击解谜游戏", mem_type="fact", subject="tech_stack")
    mem.add("项目在 D:/openhanako/UkiAgent", mem_type="fact", subject="project_path")
    print("添加成功！")
    
    # 测试搜索
    print("\n=== 语义搜索 ===")
    results = mem.search("那个游戏引擎项目在哪")
    for r in results:
        print(f"  [{r['type']}] {r['value'][:50]}")
    
    if results:
        print("\n✅ embedding API 正常工作")
    else:
        print("\n⚠️  搜索无结果（可能 embedding API 不支持，降级到关键词）")

except Exception as e:
    print(f"API 调用失败: {e}")
    print("将 fallback 到关键词匹配")

mem._conn.close()
test_db.unlink()
