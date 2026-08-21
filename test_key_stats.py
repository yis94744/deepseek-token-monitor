# -*- coding: utf-8 -*-
"""按 API Key 统计功能的临时自测脚本（用完即删，不入库）。"""
import os
import shutil
import tempfile
from datetime import datetime, timedelta

import storage

tmp = tempfile.mkdtemp(prefix="dsh-key-test-")
try:
    storage.init_db(tmp)

    # --- 1) 指纹助手 ---
    h1, hint1 = storage.fingerprint_key("sk-abc123456789wxyz")
    h1b, hint1b = storage.fingerprint_key("sk-abc123456789wxyz")   # 同一 Key 指纹稳定
    h2, hint2 = storage.fingerprint_key("sk-zzz999888777abcd")
    assert h1 == h1b and len(h1) == 12
    assert hint1 == "sk-****wxyz" and hint2 == "sk-****abcd" and h1 != h2
    assert storage.fingerprint_key("") == (None, None)
    assert storage.fingerprint_key(None) == (None, None)
    h3, hint3 = storage.fingerprint_key("plain-key-0001")          # 非 sk- 前缀
    assert hint3 == "****0001"
    print("PASS 1 指纹助手")

    # --- 2) 入库带指纹 + 旧库迁移模拟 ---
    now = datetime.now()
    storage.add_request(now, "deepseek-v4-flash", 100, 200, 300, 0.01, h1, hint1)
    storage.add_request(now - timedelta(minutes=1), "deepseek-v4-flash", 10, 20, 30, 0.001, h1, hint1)
    storage.add_request(now - timedelta(minutes=2), "deepseek-v4-pro", 50, 60, 70, 0.02, h2, hint2)
    storage.add_request(now - timedelta(days=1), "deepseek-v4-flash", 5, 5, 5, 0.0005, h2, hint2)
    # 外部数据源（无 Key）
    storage.add_external_request("cc:test-1", now - timedelta(minutes=3),
                                 "deepseek-v4-flash", 1, 2, 3, 0.0001)
    storage.add_external_request("cc:test-2", now - timedelta(days=1),
                                 "deepseek-v4-flash", 1, 2, 3, 0.0002)
    assert storage.max_request_id() >= 6
    print("PASS 2 入库")

    # --- 3) key_breakdown：今日（仅代理流量 3 条，2 个 Key + 无 Key 组） ---
    t = now.date().isoformat()
    b = storage.key_breakdown(t, t)
    assert len(b) == 3, b
    by_hint = {r["key_hint"]: r for r in b}
    assert by_hint["sk-****wxyz"]["requests"] == 2
    assert abs(by_hint["sk-****wxyz"]["cost"] - 0.011) < 1e-9
    assert by_hint["sk-****abcd"]["requests"] == 1
    assert "其他来源（无 Key）" in by_hint
    assert b[0]["cost"] >= b[1]["cost"] >= b[2]["cost"]  # 按费用倒序
    print("PASS 3 key_breakdown 今日")

    # --- 4) key_breakdown：本月含跨天 ---
    m = now.strftime("%Y-%m")
    bm = storage.key_breakdown(m + "-01", t)
    assert sum(r["requests"] for r in bm) == 6
    print("PASS 4 key_breakdown 本月")

    # --- 5) key_daily_stats：近 30 天补零 + 堆叠数据 ---
    kd = storage.key_daily_stats(30)
    assert len(kd) == 30
    assert kd[-1]["date"] == t  # 升序、最后一天是今天
    day_rows = [d for d in kd if d["keys"]]
    assert len(day_rows) == 2  # 只有今天和昨天有数据
    assert {h for h, _ in day_rows[-1]["keys"]} == {"sk-****wxyz", "sk-****abcd", "其他来源"}
    assert sum(c for _, c in day_rows[-1]["keys"]) > 0
    print("PASS 5 key_daily_stats")

    # --- 6) 超过 6 个 Key 时的合并 ---
    for i in range(8):
        h, hint = storage.fingerprint_key(f"sk-extra-key-number-{i:02d}")
        storage.add_request(now, "deepseek-v4-flash", 1, 1, 1, 0.001 * (i + 1), h, hint)
    kd2 = storage.key_daily_stats(30)
    today_seg = kd2[-1]["keys"]
    assert len(today_seg) == 7, today_seg  # 前 6 + "其他"
    assert today_seg[-1][0] == "其他"
    print("PASS 6 超过 6 个 Key 合并为其他")

    # --- 7) 老库迁移：无新列时 init_db 自动 ALTER ---
    tmp2 = tempfile.mkdtemp(prefix="dsh-key-migrate-")
    os.remove(os.path.join(tmp2, "usage.db")) if os.path.exists(os.path.join(tmp2, "usage.db")) else None
    import sqlite3
    conn = sqlite3.connect(os.path.join(tmp2, "usage.db"))
    conn.executescript("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL, date TEXT NOT NULL, model TEXT NOT NULL,
            prompt_cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
            prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0,
            source_key TEXT
        );
    """)
    conn.commit()
    conn.close()
    storage.init_db(tmp2)  # 应自动补 api_key_hash / api_key_hint 列
    conn = sqlite3.connect(os.path.join(tmp2, "usage.db"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()]
    conn.close()
    assert "api_key_hash" in cols and "api_key_hint" in cols, cols
    print("PASS 7 老库迁移自动加列")

    print("\nALL TESTS PASSED")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
