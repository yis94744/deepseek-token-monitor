# -*- coding: utf-8 -*-
"""验证修复后的 dsh_sync：在真实库副本上跑 sync_once 两次。
预期：第一次补上缺口（当前会话 4084911f 应出现一条大额记录），第二次 0 新增。"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dsh_sync
import storage

APPDATA = os.environ.get("APPDATA") or r"C:\Users\kelang\AppData\Roaming"
LIVE_DB = os.path.join(APPDATA, "DeepSeekTokenMonitor", "data", "usage.db")
CONFIG = json.load(open(os.path.join(APPDATA, "DeepSeekTokenMonitor", "config.json"), encoding="utf-8"))

probe_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp-test")
os.makedirs(probe_dir, exist_ok=True)
probe_db = os.path.join(probe_dir, "usage.db")
shutil.copyfile(LIVE_DB, probe_db)
storage.init_db(probe_dir)  # 指向副本
print("测试库:", probe_db)

before = {}
con = sqlite3.connect(probe_db)
for (sid,) in con.execute(
    "SELECT DISTINCT substr(source_key, 5, instr(substr(source_key, 5), ':') - 1) "
    "FROM requests WHERE source_key LIKE 'dsh:%'").fetchall():
    before[sid] = con.execute(
        "SELECT COUNT(*), SUM(prompt_cache_hit_tokens + prompt_cache_miss_tokens + completion_tokens) "
        "FROM requests WHERE source_key LIKE ?", ("dsh:" + sid + ":%",)).fetchone()
con.close()

print("\n== 第一次 sync_once（应补缺口）==")
added1 = dsh_sync.sync_once(CONFIG, {})
print("新增条数:", added1)

con = sqlite3.connect(probe_db)
print("\n== 新增的 dsh 记录（含模型）==")
for r in con.execute(
    "SELECT id, created_at, model, prompt_cache_hit_tokens, prompt_cache_miss_tokens, "
    "completion_tokens, cost, source_key FROM requests WHERE source_key LIKE 'dsh:%' "
    "ORDER BY id DESC LIMIT 8").fetchall():
    print(" ", r)
con.close()

print("\n== 第二次 sync_once（应为 0，去重）==")
added2 = dsh_sync.sync_once(CONFIG, {})
print("新增条数:", added2)

print("\n== 各会话: 修复前 rows/tokens -> 修复后 ==")
con = sqlite3.connect(probe_db)
for sid in sorted(before):
    after = con.execute(
        "SELECT COUNT(*), SUM(prompt_cache_hit_tokens + prompt_cache_miss_tokens + completion_tokens) "
        "FROM requests WHERE source_key LIKE ?", ("dsh:" + sid + ":%",)).fetchone()
    b = before[sid]
    print("  %s: %d行/%d -> %d行/%d  (+%d行, +%d tokens)" % (
        sid, b[0], b[1] or 0, after[0], after[1] or 0, after[0] - b[0], (after[1] or 0) - (b[1] or 0)))
con.close()
shutil.rmtree(probe_dir, ignore_errors=True)
print("\n测试库已清理")
