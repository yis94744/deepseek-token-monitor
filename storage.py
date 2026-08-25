# -*- coding: utf-8 -*-
"""数据存储模块：用 SQLite 保存每次请求明细与日/周/月汇总快照。

- requests          : 每次 API 调用的 token 与费用明细
- daily_summaries   : 每天 00:00 自动生成的日结快照
- weekly_summaries  : 每周一 00:00 自动生成的上周总结快照
- monthly_summaries : 每月 1 日 00:00 自动生成的上月总结快照
- balance_history   : 账户余额每日快照（每天一条，刷新成功自动记录）
"""
import hashlib
import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta

import pricing

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
    source_key TEXT,                                      -- 外部数据源去重键(CC Switch 同步用)
    api_key_hash TEXT,                                    -- API Key 指纹(sha256 前12位，按 Key 分组用)
    api_key_hint TEXT                                     -- API Key 显示提示(sk-****末4位，不存明文)
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

CREATE TABLE IF NOT EXISTS balance_history (
    date TEXT PRIMARY KEY,               -- 日期 YYYY-MM-DD（一天一条，后写的覆盖当天）
    balance REAL NOT NULL DEFAULT 0,     -- 当日余额(元)
    updated_at TEXT NOT NULL DEFAULT ''  -- 最近更新时间（ISO）
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
            # 旧库迁移：按 API Key 统计（v1.10.0）——补充指纹列，老数据为 NULL（归入"其他来源"）
            try:
                conn.execute("ALTER TABLE requests ADD COLUMN api_key_hash TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在，忽略
            try:
                conn.execute("ALTER TABLE requests ADD COLUMN api_key_hint TEXT")
            except sqlite3.OperationalError:
                pass  # 列已存在，忽略
            conn.commit()
        finally:
            conn.close()


def fingerprint_key(key: str):
    """把 API Key 转成 (指纹, 显示提示)，不保存明文。

    指纹 = sha256(key) 前 12 位十六进制，用于按 Key 分组统计；
    提示 = sk-****末4位（无 sk- 前缀时用 ****末4位），仅用于界面展示。
    传入空值返回 (None, None)。
    """
    if not key:
        return None, None
    key = key.strip()
    fp = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    tail = key[-4:]
    hint = "sk-****" + tail if key.startswith("sk-") else "****" + tail
    return fp, hint


def add_request(created_at, model: str, hit: int, miss: int, completion: int, cost: float,
                api_key_hash: str = None, api_key_hint: str = None):
    """记录一次 API 调用的 token 用量与费用（可带 API Key 指纹，供按 Key 统计）。"""
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO requests(created_at, date, model, prompt_cache_hit_tokens, "
                "prompt_cache_miss_tokens, completion_tokens, cost, api_key_hash, api_key_hint) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (created_at.isoformat(timespec="seconds"), created_at.date().isoformat(),
                 model, hit, miss, completion, cost, api_key_hash, api_key_hint),
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


def daily_stats(days: int = None) -> list:
    """按天聚合用量（全部历史，或最近 N 天），返回按日期倒序的列表。

    每项：date / requests / cache_hit / cache_miss / completion / tokens / cost。
    供主界面"每日统计"页使用。
    """
    where, args = "", ()
    if days:
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        where = " WHERE date >= ?"
        args = (start,)
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT date, COUNT(*), "
                "COALESCE(SUM(prompt_cache_hit_tokens),0), "
                "COALESCE(SUM(prompt_cache_miss_tokens),0), "
                "COALESCE(SUM(completion_tokens),0), "
                "COALESCE(SUM(cost),0) FROM requests" + where +
                " GROUP BY date ORDER BY date DESC", args).fetchall()
        finally:
            conn.close()
    return [
        {"date": r[0], "requests": r[1], "cache_hit": r[2], "cache_miss": r[3],
         "completion": r[4], "tokens": r[2] + r[3] + r[4], "cost": round(r[5], 6)}
        for r in rows
    ]


def save_balance_snapshot(balance: float):
    """记录当日账户余额快照（每天一条，同一天后写的覆盖），供余额每日统计使用。"""
    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO balance_history(date, balance, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET balance=excluded.balance, "
                "updated_at=excluded.updated_at",
                (today, float(balance), now))
            conn.commit()
        finally:
            conn.close()


def balance_history(days: int = None) -> list:
    """账户余额每日快照，返回按日期倒序的列表：date / balance / updated_at。

    供主界面"余额统计"页使用。
    """
    where, args = "", ()
    if days:
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        where = " WHERE date >= ?"
        args = (start,)
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT date, balance, updated_at FROM balance_history" + where +
                " ORDER BY date DESC", args).fetchall()
        finally:
            conn.close()
    return [{"date": r[0], "balance": round(r[1], 4), "updated_at": r[2]} for r in rows]


def period_stats(days: int = None, config: dict = None) -> list:
    """按天分时段统计（高峰 / 非高峰），返回按日期倒序的列表。

    每项：date / peak_requests / peak_tokens / peak_cost /
          off_requests / off_tokens / off_cost
    时段判定复用计价规则（默认工作日高峰 9:00-12:00 与 14:00-18:00，
    周末全天低谷价，可配置 peak_hours / weekend_offpeak_since 覆盖），
    按每条请求的 created_at 时刻归属。
    """
    where, args = "", ()
    if days:
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        where = " WHERE date >= ?"
        args = (start,)
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT date, created_at, prompt_cache_hit_tokens, "
                "prompt_cache_miss_tokens, completion_tokens, cost FROM requests" + where +
                " ORDER BY date DESC", args).fetchall()
        finally:
            conn.close()
    result = {}
    for date_s, created_at, hit, miss, comp, cost in rows:
        peak = False
        try:
            peak = pricing.is_peak_hour(datetime.fromisoformat(created_at), config)
        except Exception:
            pass
        d = result.setdefault(date_s, {
            "date": date_s, "peak_requests": 0, "peak_tokens": 0, "peak_cost": 0.0,
            "off_requests": 0, "off_tokens": 0, "off_cost": 0.0})
        tokens = hit + miss + comp
        if peak:
            d["peak_requests"] += 1
            d["peak_tokens"] += tokens
            d["peak_cost"] += cost or 0
        else:
            d["off_requests"] += 1
            d["off_tokens"] += tokens
            d["off_cost"] += cost or 0
    out = []
    for d in result.values():  # 按首次出现顺序（即日期倒序）
        d["peak_cost"] = round(d["peak_cost"], 6)
        d["off_cost"] = round(d["off_cost"], 6)
        out.append(d)
    return out


def rebill_all(config: dict) -> int:
    """按当前计价规则重算全部历史记录的费用（覆盖原 cost），返回改写行数。

    用于官网调价/修正高峰时段后对账：每条记录按 created_at 重新取价并重算。
    """
    fixed = 0
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT id, model, created_at, prompt_cache_hit_tokens, "
                "prompt_cache_miss_tokens, completion_tokens FROM requests").fetchall()
            for rid, model, created_at, hit, miss, comp in rows:
                try:
                    dt = datetime.fromisoformat(created_at)
                except Exception:
                    continue
                price = pricing.get_price(model, config, dt)
                cost = pricing.calc_cost(
                    {"prompt_cache_hit_tokens": hit,
                     "prompt_cache_miss_tokens": miss,
                     "completion_tokens": comp}, price)
                conn.execute("UPDATE requests SET cost=? WHERE id=?", (cost, rid))
                fixed += 1
            conn.commit()
        finally:
            conn.close()
    return fixed

def key_breakdown(date_from: str, date_to: str) -> list:
    """按 API Key 分组统计某日期区间的用量（仅本地代理流量带 Key 指纹）。

    返回按费用倒序的列表：key_hash / key_hint / requests / cache_hit / cache_miss /
    completion / cost；无 Key 指纹的记录（外部数据源导入）归入 hint 为"其他来源"的一组。
    """
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT api_key_hash, api_key_hint, COUNT(*), "
                "COALESCE(SUM(prompt_cache_hit_tokens),0), "
                "COALESCE(SUM(prompt_cache_miss_tokens),0), "
                "COALESCE(SUM(completion_tokens),0), COALESCE(SUM(cost),0) "
                "FROM requests WHERE date BETWEEN ? AND ? "
                "GROUP BY api_key_hash ORDER BY 7 DESC",
                (date_from, date_to),
            ).fetchall()
        finally:
            conn.close()
    return [
        {"key_hash": r[0] or "", "key_hint": r[1] or "其他来源（无 Key）",
         "requests": r[2], "cache_hit": r[3], "cache_miss": r[4],
         "completion": r[5], "cost": round(r[6], 6)}
        for r in rows
    ]


def key_daily_stats(days: int = 30) -> list:
    """近 N 天（含今天）按日×Key 聚合费用，返回按日期升序的列表。

    每项：date / keys（[(key_hint, cost), ...] 按费用倒序；同一天最多保留前 6 个 Key，
    其余并入 ("其他", 剩余费用)，避免堆叠图颜色过多）。
    """
    start_date = date.today() - timedelta(days=days - 1)
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT date, api_key_hint, COALESCE(SUM(cost),0) FROM requests "
                "WHERE date >= ? GROUP BY date, api_key_hash ORDER BY date, 3 DESC",
                (start_date.isoformat(),),
            ).fetchall()
        finally:
            conn.close()
    by_day = {}
    for d, hint, cost in rows:
        by_day.setdefault(d, []).append((hint or "其他来源", round(cost, 6)))
    result = []
    for i in range(days):
        d = (start_date + timedelta(days=i)).isoformat()
        segments = by_day.get(d, [])
        if len(segments) > 6:  # 只保留费用最高的 6 个 Key，其余并入"其他"
            segments = segments[:6] + [("其他", round(sum(c for _, c in segments[6:]), 6))]
        result.append({"date": d, "keys": segments})
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

def dsh_cumulative(sid: str):
    """返回某 Harness 会话已入库的累计总量 (hit, miss, comp)（各增量行求和），无记录则 None。

    供 dsh 同步做无状态差分：以库内全部记录求和为准，进程重启、settings
    丢失都不会漏记或重复计费——重启后第一次同步会补上"上次入库以来的全部
    增量"；去重键用累计总量本身，同一总量只入库一次。
    """
    return _cumulative("dsh", sid)


def yq_cumulative(sid: str):
    """返回某 YQ Harness 会话已入库的累计总量 (hit, miss, comp)，无记录则 None。

    与 dsh_cumulative 同口径（无状态差分、总量去重），前缀为 yq:。
    """
    return _cumulative("yq", sid)


def _cumulative(prefix: str, sid: str):
    """按来源前缀求某会话已入库的累计总量，供无状态差分同步使用。"""
    with _lock:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(prompt_cache_hit_tokens),0), "
                "COALESCE(SUM(prompt_cache_miss_tokens),0), "
                "COALESCE(SUM(completion_tokens),0) "
                "FROM requests WHERE source_key LIKE ?",
                (prefix + ":" + sid + ":%",),
            ).fetchone()
        finally:
            conn.close()
    if row is None or (row[0] == 0 and row[1] == 0 and row[2] == 0):
        return None
    return (int(row[0]), int(row[1]), int(row[2]))


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