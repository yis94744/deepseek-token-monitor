# -*- coding: utf-8 -*-
"""DSH 同步 v2 测试：目录分片/旧单文件解析、差分去重、真实数据 dry-run。"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dsh_sync
import storage

BASE = os.path.dirname(os.path.abspath(__file__))
# 干跑真实 DSH 数据时切到真实库太危险，这里全部用临时库
tmp = tempfile.mkdtemp(prefix="dsh-sync-test-")

def make_shard(sid, hit, miss, comp, extra_rows=None):
    rows = {
        "tokenUsage": {"ver": 1, "seq": 1, "val": {
            "totals": {"uncachedInputTokens": miss, "outputTokens": comp,
                       "cacheReadTokens": hit, "cacheWriteTokens": 0},
            "last": {"turn": 1, "step": 1, "buckets": {}}}}}
    if extra_rows:
        rows.update(extra_rows)
    return {"version": 5, "record": {"identity": {"createdAt": 1, "cwd": "X"},
                                     "rows": rows}}

def make_legacy(sessions):
    return {"unit": {"name": "session_projcache", "version": 3},
            "tables": {"sessions": sessions}}

def write_shards(dirpath, shards):
    sd = os.path.join(dirpath, "sessions")
    os.makedirs(sd, exist_ok=True)
    for sid, payload in shards.items():
        with open(os.path.join(sd, sid + ".json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

try:
    storage.init_db(tmp)

    # ---------- 1) 目录分片格式解析 ----------
    d = tempfile.mkdtemp(prefix="shards-")
    write_shards(d, {
        "s1": make_shard("s1", hit=1000, miss=2000, comp=300),
        "s2": make_shard("s2", hit=0, miss=500, comp=50),
        "s3": make_shard("s3", hit=0, miss=0, comp=0),     # 全 0，应被跳过
        "bad": {"version": 5, "record": "not-a-dict"},      # 损坏，应被跳过
    })
    cfg = {"dsh": {"projcache_path": d, "home": os.path.join(tmp, "no-home"),
                   "models": {}}, "unknown_model_fallback": "deepseek-v4-flash",
           "models": {"deepseek-v4-flash": {"cache_hit": 0.05, "cache_miss": 1.5, "output": 4.5},
                      "deepseek-v4-pro": {"cache_hit": 0.15, "cache_miss": 4.5, "output": 13.5}}}
    settings = {}
    added = dsh_sync.sync_once(cfg, settings)   # 首轮：全量导入 2 个会话
    assert added == 2, added
    rows = storage._cumulative("dsh", "s1")
    assert rows == (1000, 2000, 300), rows
    rows2 = storage._cumulative("dsh", "s2")
    assert rows2 == (0, 500, 50), rows2
    print("PASS 1 目录分片解析 + 首轮全量导入")

    # ---------- 2) 无变化 → 0 新增（幂等） ----------
    assert dsh_sync.sync_once(cfg, settings) == 0
    print("PASS 2 无变化不重复导入")

    # ---------- 3) 增量差分：总量增长只补增量 ----------
    write_shards(d, {
        "s1": make_shard("s1", hit=1000, miss=2500, comp=400),  # miss +500, comp +100
        "s2": make_shard("s2", hit=10, miss=500, comp=50),      # hit +10
    })
    added = dsh_sync.sync_once(cfg, settings)
    assert added == 2, added
    assert storage._cumulative("dsh", "s1") == (1000, 2500, 400)
    assert storage._cumulative("dsh", "s2") == (10, 500, 50)
    # 明细行检查：应存 delta 而非全量
    import sqlite3
    conn = sqlite3.connect(os.path.join(tmp, "usage.db"))
    s1rows = conn.execute("SELECT prompt_cache_hit_tokens, prompt_cache_miss_tokens, "
                          "completion_tokens, model FROM requests WHERE source_key LIKE 'dsh:s1:%' "
                          "ORDER BY id").fetchall()
    conn.close()
    assert s1rows == [(1000, 2000, 300, "deepseek-v4-flash"),
                      (0, 500, 100, "deepseek-v4-flash")], s1rows
    print("PASS 3 增量差分正确（明细=delta）")

    # ---------- 4) 回退（总量变小）不导入 ----------
    write_shards(d, {"s1": make_shard("s1", hit=500, miss=100, comp=10)})
    assert dsh_sync.sync_once(cfg, settings) == 0
    print("PASS 4 总量回退被跳过")

    # ---------- 5) 旧单文件格式兼容 ----------
    d2 = tempfile.mkdtemp(prefix="legacy-")
    legacy_path = os.path.join(d2, "session_projcache.json")
    with open(legacy_path, "w", encoding="utf-8") as f:
        json.dump(make_legacy({"s9": {"identity": {}, "rows": {
            "tokenUsage": {"ver": 1, "val": {"totals": {
                "uncachedInputTokens": 777, "outputTokens": 88, "cacheReadTokens": 0}}}}}}), f)
    cfg2 = dict(cfg); cfg2["dsh"] = {"projcache_path": legacy_path, "models": {}}
    storage2 = tempfile.mkdtemp(prefix="db2-")
    storage.init_db(storage2)
    try:
        added = dsh_sync.sync_once(cfg2, settings)
        assert added == 1, added
        assert storage._cumulative("dsh", "s9") == (0, 777, 88)
        print("PASS 5 旧单文件格式兼容")
    finally:
        storage.init_db(tmp)  # 切回主临时库

    # ---------- 6) settings.yaml 默认模型兜底（无需 API） ----------
    fake_home = tempfile.mkdtemp(prefix="home-")
    with open(os.path.join(fake_home, "settings.yaml"), "w", encoding="utf-8") as f:
        f.write("agent-default-model:\n  provider: deepseek-official\n"
                "  model: deepseek-v4-pro\n  reasoningEffort: max\n")
    cfg3 = dict(cfg)
    cfg3["dsh"] = {"home": fake_home, "projcache_path": d, "models": {},
                   "api_base": "http://127.0.0.1:1"}  # 连不上的 API → 走兜底
    d3 = tempfile.mkdtemp(prefix="shards3-")
    write_shards(d3, {"s7": make_shard("s7", hit=1, miss=2, comp=3)})
    cfg3["dsh"]["projcache_path"] = d3
    db3 = tempfile.mkdtemp(prefix="db3-")
    storage.init_db(db3)
    try:
        dsh_sync.sync_once(cfg3, {})
        assert storage._cumulative("dsh", "s7") == (1, 2, 3)
        conn = sqlite3.connect(os.path.join(db3, "usage.db"))
        model = conn.execute("SELECT model FROM requests WHERE source_key LIKE 'dsh:s7:%'"
                             ).fetchone()[0]
        conn.close()
        assert model == "deepseek-v4-pro", model
        print("PASS 6 settings.yaml 默认模型兜底")
    finally:
        storage.init_db(tmp)

    # ---------- 7) 真实数据 dry-run（只算增量，不入真实库） ----------
    real_cfg = {"dsh": {"models": {}}, "unknown_model_fallback": "deepseek-v4-flash",
                "models": {"deepseek-v4-flash": {"cache_hit": 0.05, "cache_miss": 1.5,
                                                 "output": 4.5},
                           "deepseek-v4-pro": {"cache_hit": 0.15, "cache_miss": 4.5,
                                               "output": 13.5}}}
    real_home = os.path.join(os.environ.get("USERPROFILE", ""), ".dsh")
    shards_dir = os.path.join(real_home, "storages", "session_projcache")
    if os.path.isdir(shards_dir):
        total = 0
        print(f"-- 真实 DSH 投影目录 {shards_dir} --")
        for sid, rows, dt in dsh_sync._iter_sessions_from(real_cfg):
            tu = (rows.get("tokenUsage") or {}).get("val") or {}
            t = tu.get("totals") or {}
            hit, miss, comp = (int(t.get("cacheReadTokens") or 0),
                               int(t.get("uncachedInputTokens") or 0),
                               int(t.get("outputTokens") or 0))
            total += 1
            if hit + miss + comp > 0:
                print(f"  {sid[:38]}  hit={hit:,} miss={miss:,} comp={comp:,} "
                      f"updated={dt:%m-%d %H:%M}")
        assert total >= 1
        print("PASS 7 真实数据解析（无异常）")
    else:
        print("SKIP 7 本机无真实 DSH 投影")

    print("\nALL DSH SYNC TESTS PASSED")
finally:
    storage._db_path = None
    shutil.rmtree(tmp, ignore_errors=True)
