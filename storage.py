# -*- coding: utf-8 -*-
"""数据存储模块：用 SQLite 保存每次请求明细与日/周/月汇总快照。

- requests          : 每次 API 调用的 token 与费用明细
- daily_summaries   : 每天 00:00 自动生成的日结快照
- weekly_summaries  : 每周一 00:00 自动生成的上周总结快照
- monthly_summaries : 每月 1 日 00:00 自动生成的上月总结快照
"""
import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta

_lock = threading.Lock()   # 全局锁：保证多个线程访问 SQLite 时串行执行
_db_path = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,            -- 请求时间（ISO 格式）
    date TEXT NOT NULL,                  -- 请求日期 YYYY-MM-DD（本地时区）
    model TEXT NOT NULL,                 -- 模型名
    prompt_cache_hit_tokens INTEGER NOT NULL DEFAULT 0,   -- 输入(缓存命中) token
    prompt_cache_miss_tokens INTEGER NOT NULL DEFAULT 0,  -- 输入(缓存未命中) token
    completion_tokens INTEGER NOT NULL DEFAULT 0,         -- 输出 token
    cost REAL NOT NULL DEFAULT 0,                         -- 本次费用(元)
    source_key TEXT                                       -- 外部数据源去重键(CC Switch 同步用)
);
CREATE INDEX IF NOT EXISTS idx_requests_date ON requests(date);

CREATE TABLE IF NOT EXISTS daily_summaries (
    date TEXT PRIMARY KEY,               -- 日期 YYYY-MM-DD
    requests INTEGER NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    cache_miss INTEGER NOT NULL DEFAULT 0,
    completion INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    breakdown TEXT NOT NULL DEFAULT '{}' -- 各模型明细(JSON)
);

CREATE TABLE IF NOT EXISTS weekly_summaries (
    week_start TEXT PRIMARY KEY,         -- 该周的周一日期 YYYY-MM-DD
    requests INTEGER NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    cache_miss INTEGER NOT NULL DEFAULT 0,
    completion INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    breakdown TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS monthly_summaries (
    month TEXT PRIMARY KEY,              -- 月份 YYYY-MM
    requests INTEGER NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    cache_miss INTEGER NOT NULL DEFAULT 0,
    completion INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    breakdown TEXT NOT NULL DEFAULT '{}'
);
"""


def _conn():
    """新建 SQLite 连接（每次操作独立连接，配合全局锁保证线程安全）。"""
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(data_dir: str):
    """初始化数据库文件与表结构。"""
    global _db_path
    os.makedirs(data_dir, exist_ok=True)
    _db_path = os.path.join(data_dir, "usage.db")
    with _lock:
        conn = _conn()
        try:
            conn.executescript(_SCHEMA)
            # 旧库迁移：为兼容 CC Switch 数据源，补充 source_key 列并建立去重索引
            try:
                conn.execute("ALTER TABLE requests ADD COLUMN source_key TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在，忽略
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_requests_source_key ON requests(source_key)")
            conn.commit()
        finally:
            conn.close()


def add_request(created_at, model: str, hit: int, miss: int, completion: int, cost: float):
    """记录一次 API 调用的 token 用量与费用。"""
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO requests(created_at, date, model, prompt_cache_hit_tokens, "
                "prompt_cache_miss_tokens, completion_tokens, cost) VALUES(?,?,?,?,?,?,?)",
                (created_at.isoformat(timespec="seconds"), created_at.date().isoformat(),
                 model, hit, miss, completion, cost),
            )
            conn.commit()
        finally:
            conn.close()


def _aggregate(date_from: str, date_to: str) -> dict:
    """聚合某日期区间的总量（含当天的日边界）。"""
    with _lock:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(prompt_cache_hit_tokens),0), "
                "COALESCE(SUM(prompt_cache_miss_tokens),0), COALESCE(SUM(completion_tokens),0), "
                "COALESCE(SUM(cost),0) FROM requests WHERE date BETWEEN ? AND ?",
                (date_from, date_to),
            ).fetchone()
        finally:
            conn.close()
    return {
        "requests": row[0], "cache_hit": row[1], "cache_miss": row[2],
        "completion": row[3], "cost": round(row[4], 6),
    }


def _model_breakdown(date_from: str, date_to: str) -> list:
    """按模型分组统计某日期区间用量，返回列表。"""
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT model, COUNT(*), COALESCE(SUM(prompt_cache_hit_tokens),0), "
                "COALESCE(SUM(prompt_cache_miss_tokens),0), COALESCE(SUM(completion_tokens),0), "
                "COALESCE(SUM(cost),0) FROM requests WHERE date BETWEEN ? AND ? "
                "GROUP BY model ORDER BY cost DESC",
                (date_from, date_to),
            ).fetchall()
        finally:
            conn.close()
    return [
        {"model": r[0], "requests": r[1], "cache_hit": r[2], "cache_miss": r[3],
         "completion": r[4], "cost": round(r[5], 6)}
        for r in rows
    ]


def today_stats() -> dict:
    """今日累计（实时聚合）。"""
    today = date.today().isoformat()
    return _aggregate(today, today)


def today_breakdown() -> list:
    """今日各模型明细。"""
    today = date.today().isoformat()
    return _model_breakdown(today, today)


def week_range_start() -> date:
    """本周的周一日期。"""
    today = date.today()
    return today - timedelta(days=today.weekday())


def this_week_stats() -> dict:
    """本周至今累计（周一起）。"""
    monday = week_range_start()
    return _aggregate(monday.isoformat(), date.today().isoformat())


def this_week_breakdown() -> list:
    """本周至今各模型明细。"""
    monday = week_range_start()
    return _model_breakdown(monday.isoformat(), date.today().isoformat())


def this_month_stats() -> dict:
    """本月至今累计。"""
    start = date.today().replace(day=1).isoformat()
    return _aggregate(start, date.today().isoformat())


def this_month_breakdown() -> list:
    """本月至今各模型明细。"""
    start = date.today().replace(day=1).isoformat()
    return _model_breakdown(start, date.today().isoformat())


def save_daily_summary(target_date: date):
    """把某一天的数据固化为日结快照（每天 00:00 自动更新时调用）。"""
    d = target_date.isoformat()
    agg = _aggregate(d, d)
    brk = json.dumps(_model_breakdown(d, d), ensure_ascii=False)
    _upsert("daily_summaries", d, agg, brk)


def save_weekly_summary(week_start: date):
    """把某一周（周一起 7 天）的数据固化为周结快照（每周一自动调用）。"""
    week_end = week_start + timedelta(days=6)
    agg = _aggregate(week_start.isoformat(), week_end.isoformat())
    brk = json.dumps(_model_breakdown(week_start.isoformat(), week_end.isoformat()), ensure_ascii=False)
    _upsert("weekly_summaries", week_start.isoformat(), agg, brk)


def save_monthly_summary(month: str):
    """把某个月（month 形如 YYYY-MM）的数据固化为月结快照（每月 1 日自动调用）。"""
    agg = _aggregate(month + "-01", month + "-31")
    brk = json.dumps(_model_breakdown(month + "-01", month + "-31"), ensure_ascii=False)
    _upsert("monthly_summaries", month, agg, brk)


def _upsert(table: str, key: str, agg: dict, breakdown: str):
    """按不同表的主键列写入/覆盖汇总快照。"""
    key_col = {"daily_summaries": "date",
               "weekly_summaries": "week_start",
               "monthly_summaries": "month"}[table]
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO {table}({key_col}, requests, cache_hit, cache_miss, "
                f"completion, cost, breakdown) VALUES(?,?,?,?,?,?,?)",
                (key, agg["requests"], agg["cache_hit"], agg["cache_miss"],
                 agg["completion"], agg["cost"], breakdown),
            )
            conn.commit()
        finally:
            conn.close()


def list_daily_summaries(limit: int = 60) -> list:
    """最近的日结快照列表。"""
    return _list_summaries("daily_summaries", "date", limit)


def list_weekly_summaries(limit: int = 52) -> list:
    """最近的周结快照列表。"""
    return _list_summaries("weekly_summaries", "week_start", limit)


def list_monthly_summaries(limit: int = 24) -> list:
    """最近的月结快照列表。"""
    return _list_summaries("monthly_summaries", "month", limit)


def _list_summaries(table: str, key_col: str, limit: int) -> list:
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                f"SELECT {key_col}, requests, cache_hit, cache_miss, completion, cost "
                f"FROM {table} ORDER BY {key_col} DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {"key": r[0], "requests": r[1], "cache_hit": r[2], "cache_miss": r[3],
         "completion": r[4], "cost": round(r[5], 6)}
        for r in rows
    ]

def past_days_stats(days: int = 7) -> list:
    """过去 N 天（含今天）按日聚合，返回按日期升序的列表；无数据的日期补 0。

    供主界面仪表盘绘制"最近 7 天费用柱状图"使用。
    """
    today = date.today()
    start = today - timedelta(days=days - 1)
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT date, COUNT(*), "
                "COALESCE(SUM(prompt_cache_hit_tokens + prompt_cache_miss_tokens + completion_tokens),0), "
                "COALESCE(SUM(cost),0) FROM requests WHERE date BETWEEN ? AND ? GROUP BY date",
                (start.isoformat(), today.isoformat()),
            ).fetchall()
        finally:
            conn.close()
    by_day = {r[0]: {"requests": r[1], "tokens": r[2], "cost": round(r[3], 6)} for r in rows}
    result = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        item = dict(by_day.get(d) or {"requests": 0, "tokens": 0, "cost": 0.0})
        item["date"] = d
        result.append(item)
    return result

def add_external_request(source_key: str, created_at, model: str, hit: int, miss: int,
                         completion: int, cost: float) -> bool:
    """记录一条来自外部数据源（如 CC Switch）的调用，source_key 用于去重。

    返回 True 表示新插入，False 表示已存在（重复记录被忽略）。
    """
    with _lock:
        conn = _conn()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO requests(created_at, date, model, prompt_cache_hit_tokens, "
                "prompt_cache_miss_tokens, completion_tokens, cost, source_key) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (created_at.isoformat(timespec="seconds"), created_at.date().isoformat(),
                 model, hit, miss, completion, cost, source_key),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

def add_external_requests(rows: list) -> int:
    """批量写入外部数据源记录（同一连接、一次事务，速度快）。

    rows 为 (source_key, created_at, model, hit, miss, completion, cost) 列表。
    返回实际新增条数（source_key 重复的会被忽略）。
    """
    added = 0
    with _lock:
        conn = _conn()
        try:
            for source_key, created_at, model, hit, miss, completion, cost in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO requests(created_at, date, model, "
                    "prompt_cache_hit_tokens, prompt_cache_miss_tokens, completion_tokens, "
                    "cost, source_key) VALUES(?,?,?,?,?,?,?,?)",
                    (created_at.isoformat(timespec="seconds"), created_at.date().isoformat(),
                     model, hit, miss, completion, cost, source_key),
                )
                added += cur.rowcount
            conn.commit()
        finally:
            conn.close()
    return added

def max_request_id() -> int:
    """返回明细表当前最大 id（用于悬浮窗检测新增记录）。"""
    with _lock:
        conn = _conn()
        try:
            r = conn.execute("SELECT COALESCE(MAX(id),0) FROM requests").fetchone()
        finally:
            conn.close()
    return int(r[0])


def new_requests_since(min_id: int, seconds: int = 8) -> list:
    """返回 id>min_id 且发生在最近 seconds 秒内的 (id, token总量) 列表。

    供悬浮窗弹出 "+N" 动效使用：只取近期实时新增，避免首次同步历史时刷屏。
    """
    since = (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT id, prompt_cache_hit_tokens + prompt_cache_miss_tokens + completion_tokens "
                "FROM requests WHERE id > ? AND created_at >= ? ORDER BY id",
                (min_id, since),
            ).fetchall()
        finally:
            conn.close()
    return [(int(r[0]), int(r[1])) for r in rows]