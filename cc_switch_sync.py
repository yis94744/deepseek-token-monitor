# -*- coding: utf-8 -*-
"""CC Switch 数据源同步模块。

设计目标：不加任何转发跳数、不增加 API 延迟。
只以只读方式打开 CC Switch 的本地数据库 cc-switch.db，把它的用量明细
（proxy_request_logs 表）增量同步到本软件的 usage.db，Codex 等经
CC Switch 的对话就会自动出现在统计里。

计费口径：CC Switch 里存的是美元费用，本软件按 config.json 的人民币单价
用同样的 token 数重新计算，保证与本软件其他统计口径一致、可自行调整。
"""
import os
import sqlite3
from datetime import datetime

import pricing
import storage

_LOG = None  # 日志文件路径，由 run() 初始化


def _cc_db_path(config: dict) -> str:
    """解析 CC Switch 数据库路径，未配置时用默认位置。"""
    path = (config.get("cc_switch") or {}).get("db_path")
    if path:
        return os.path.expandvars(path)
    return os.path.join(os.path.expanduser("~"), ".cc-switch", "cc-switch.db")


def _log(text: str):
    """同步过程日志（仅排查用），写入 data 目录的 cc_sync.log。"""
    if not _LOG:
        return
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
    except Exception:
        pass


def sync_once(config: dict, cursor: int) -> tuple:
    """同步一轮：从 rowid>cursor 处增量读取，返回 (新游标, 新增条数)。"""
    db_path = _cc_db_path(config)
    app_types = list((config.get("cc_switch") or {}).get("app_types") or ["codex"])
    placeholders = ",".join("?" * len(app_types))

    conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT rowid, request_id, model, input_tokens, output_tokens, cache_read_tokens, "
            "status_code, created_at, input_token_semantics FROM proxy_request_logs "
            f"WHERE rowid > ? AND app_type IN ({placeholders}) ORDER BY rowid LIMIT 1000",
            [cursor] + app_types,
        ).fetchall()
    finally:
        conn.close()

    new_cursor = cursor
    pending = []  # 批量收集待写入记录，最后一次性事务写入
    for row in rows:
        (rowid, request_id, model, input_tokens, output_tokens, cache_read_tokens,
         status_code, created_at, semantics) = row
        new_cursor = max(new_cursor, rowid)
        if not model or status_code != 200:
            continue  # 失败请求没有有效用量，跳过
        hit = int(cache_read_tokens or 0)
        total_input = int(input_tokens or 0)
        # semantics=1：input_tokens 是"含缓存读取的总输入"；否则是不含缓存的未命中输入
        if semantics is None or semantics == 1:
            miss = max(total_input - hit, 0)
        else:
            miss = total_input
        completion = int(output_tokens or 0)
        if hit + miss + completion <= 0:
            continue
        price = pricing.get_price(model, config)
        cost = pricing.calc_cost(
            {"prompt_cache_hit_tokens": hit,
             "prompt_cache_miss_tokens": miss,
             "completion_tokens": completion}, price)
        try:
            dt = datetime.fromtimestamp(int(created_at))
        except Exception:
            continue
        key = "cc:" + (request_id or str(rowid))
        pending.append((key, dt, model, hit, miss, completion, cost))

    added = storage.add_external_requests(pending) if pending else 0
    return new_cursor, added


def run(config: dict, settings: dict, state: dict, stop_event):
    """后台线程入口：周期增量同步 CC Switch 用量。"""
    global _LOG
    cc = config.get("cc_switch") or {}
    data_dir = os.path.dirname(storage._db_path or "")
    if data_dir:
        _LOG = os.path.join(data_dir, "cc_sync.log")
    interval = max(2, int(cc.get("sync_interval_seconds", 2)))
    cursor = int(settings.get("cc_switch_cursor") or 0)
    state["cc_sync"] = {"enabled": bool(cc.get("enabled", True)), "total_added": 0,
                        "last_added": 0, "last_time": None, "error": None}
    _log(f"同步线程启动，起始游标={cursor}，间隔={interval}秒，db={_cc_db_path(config)}")

    while not stop_event.wait(interval):
        if not (config.get("cc_switch") or {}).get("enabled", True):
            state["cc_sync"]["enabled"] = False  # 设置页关闭后线程常驻等待，重新开启即恢复
            state["cc_sync"]["error"] = None
            continue
        try:
            new_cursor, added = sync_once(config, cursor)
            cursor = new_cursor
            settings["cc_switch_cursor"] = cursor  # 程序退出时随 settings.json 一起保存
            info = state["cc_sync"]
            info["enabled"] = True  # 恢复同步时标记启用（设置页开关状态同步）
            if added:
                info["total_added"] = info.get("total_added", 0) + added
                info["last_added"] = added
            info["last_time"] = datetime.now().strftime("%H:%M:%S")
            info["error"] = None
            _log(f"游标={cursor} 本轮新增={added} 累计={info.get('total_added', 0)}")
        except Exception as exc:
            state["cc_sync"]["error"] = str(exc)
            _log("ERROR: " + str(exc))