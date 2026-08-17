# -*- coding: utf-8 -*-
"""全库重新计费：按官网峰谷计价规则重算 usage.db 中所有记录的费用。

前提：监控进程已停止（库未被独占锁住）。运行：
    python rebill.py [usage.db 路径]
默认库路径为 %APPDATA%\\DeepSeekTokenMonitor\\data\\usage.db。

步骤：
1. dsh 会话按解析出的真实模型重归属（沿用 dsh_sync 的模型解析）；
2. 所有行（dsh / cc / kun / 代理）按 model + created_at 重新取价并重算 cost：
   - 2026-08-17 之前 → legacy 平峰价；
   - 高峰时段（默认每日 9:00-14:00）→ peak 价；
   - 其余 → 空闲价。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dsh_sync
import pricing

APPDATA = os.environ.get("APPDATA") or r"C:\Users\kelang\AppData\Roaming"
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(APPDATA, "DeepSeekTokenMonitor", "data", "usage.db")
CONFIG_PATH = os.path.join(APPDATA, "DeepSeekTokenMonitor", "config.json")

config = json.load(open(CONFIG_PATH, encoding="utf-8")) if os.path.isfile(CONFIG_PATH) else {}
print("库:", DB)
print("官网新价生效:", pricing._legacy_until(config).strftime("%Y-%m-%d"),
      "| 高峰时段:", pricing._peak_window(config))

con = sqlite3.connect(DB)
cur = con.cursor()

# ---- 1) dsh 会话模型重归属 ----
sids = [r[0] for r in cur.execute(
    "SELECT DISTINCT substr(source_key, 5, instr(substr(source_key, 5), ':') - 1) "
    "FROM requests WHERE source_key LIKE 'dsh:%'").fetchall()]
models = dsh_sync._resolve_session_models(config, {}, sids)
print("\ndsh 模型解析:", json.dumps(models, ensure_ascii=False))
for sid in sids:
    model = models.get(sid)
    if not model:
        continue
    cur.execute("UPDATE requests SET model=? WHERE source_key LIKE ? AND model != ?",
                (model, "dsh:" + sid + ":%", model))
con.commit()

# ---- 2) 全库重算费用 ----
cur.execute("SELECT COUNT(*) FROM requests")
total = cur.fetchone()[0]
print("\n重算 %d 行费用..." % total)

old_total = 0.0
new_total = 0.0
fixed = 0
bad = 0
cur.execute("SELECT id, model, created_at, prompt_cache_hit_tokens, "
            "prompt_cache_miss_tokens, completion_tokens, cost FROM requests")
for rid, model, created_at, hit, miss, comp, old_cost in cur.fetchall():
    old_total += old_cost or 0
    try:
        dt = datetime.fromisoformat(created_at)
    except Exception:
        bad += 1
        continue
    price = pricing.get_price(model, config, dt)
    new_cost = pricing.calc_cost({"prompt_cache_hit_tokens": hit,
                                  "prompt_cache_miss_tokens": miss,
                                  "completion_tokens": comp}, price)
    new_total += new_cost
    if abs((new_cost or 0) - (old_cost or 0)) > 1e-9:
        cur.execute("UPDATE requests SET cost=? WHERE id=?", (new_cost, rid))
        fixed += 1
con.commit()

print("费用合计: %.6f -> %.6f 元 (差额 %+.6f)" % (old_total, new_total, new_total - old_total))
print("改写行数: %d, 时间解析失败(未改): %d" % (fixed, bad))

# ---- 3) 按来源/模型汇总 ----
print("\n按来源汇总:")
for r in cur.execute(
    "SELECT CASE WHEN source_key LIKE 'dsh:%' THEN 'dsh' WHEN source_key LIKE 'cc:%' THEN 'cc' "
    "WHEN source_key LIKE 'kun:%' THEN 'kun' ELSE 'proxy' END AS src, "
    "COUNT(*), SUM(cost) FROM requests GROUP BY src").fetchall():
    print("  %-6s %5d 行  费用 %10.6f 元" % (r[0], r[1], r[2] or 0))
print("\n按模型汇总:")
for r in cur.execute(
    "SELECT model, COUNT(*), SUM(prompt_cache_hit_tokens + prompt_cache_miss_tokens + completion_tokens), "
    "SUM(cost) FROM requests GROUP BY model ORDER BY 4 DESC").fetchall():
    print("  %-16s %5d 行  %12d tokens  费用 %10.6f 元" % (r[0], r[1], r[2] or 0, r[3] or 0))
con.close()
