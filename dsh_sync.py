# -*- coding: utf-8 -*-
"""DeepSeek Harness 数据源同步模块。

设计目标：不加任何转发跳数、不增加 API 延迟、不改 Harness 的任何配置。
DeepSeek Harness（DSH）把每个会话的用量投影缓存写到
~/.dsh/storages/session_projcache.json：
  tables.sessions.<会话ID>.rows.tokenUsage.val.totals = {
    uncachedInputTokens, outputTokens, cacheReadTokens, cacheWriteTokens }
本模块只读该文件，把各会话的用量增量同步到本软件的 usage.db。

计费口径：与 CC Switch / Kun 同步一致，按 config.json 的人民币单价用
token 数重新计算。只提取用量数值，不读取任何对话内容。
"""
import json
import os
from datetime import datetime

import pricing
import storage

_LOG = None  # 日志文件路径，由 run() 初始化


def _projcache_path(config: dict) -> str:
    """解析 Harness 用量投影缓存路径，未配置时用默认位置。"""
    path = (config.get("dsh") or {}).get("projcache_path")
    if path:
        return os.path.expandvars(path)
    return os.path.join(os.path.expanduser("~"), ".dsh", "storages", "session_projcache.json")


def _log(text: str):
    """同步过程日志（仅排查用），写入 data 目录的 dsh_sync.log。"""
    if not _LOG:
        return
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
    except Exception:
        pass


def sync_once(config: dict, settings: dict) -> int:
    """同步一轮：读取用量投影缓存并做会话级差分，返回新增条数。

    每个会话的上次已导入总量记在 settings["dsh_baselines"]；
    首次见到的会话直接导入其全部用量（Harness 会话数少、总量真实），
    之后只导入增量，避免重复计费。
    """
    path = _projcache_path(config)
    if not os.path.isfile(path):
        return 0  # Harness 尚未生成数据
    try:
        mtime_ms = int(os.path.getmtime(path) * 1000)
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return 0  # 文件被 Harness 写入中/损坏，跳过等下一轮
    sessions = ((data.get("tables") or {}).get("sessions") or {})
    if not sessions:
        return 0

    baselines = settings.get("dsh_baselines") or {}
    if not isinstance(baselines, dict):
        baselines = {}

    pending = []
    for sid, sdata in sessions.items():
        rows = sdata.get("rows") or {}
        tu = (rows.get("tokenUsage") or {}).get("val") or {}
        totals = tu.get("totals") or {}
        hit = int(totals.get("cacheReadTokens") or 0)
        miss = int(totals.get("uncachedInputTokens") or 0)
        comp = int(totals.get("outputTokens") or 0)
        if hit + miss + comp <= 0:
            continue
        prev = baselines.get(sid)
        if prev is None:
            delta = (hit, miss, comp)  # 首见：导入该会话全部用量
            n = 1
        else:
            delta = (hit - prev["h"], miss - prev["m"], comp - prev["c"])
            n = prev["n"] + 1
            if delta[0] + delta[1] + delta[2] <= 0:
                continue  # 总量未增长，跳过
        # 去重键用每会话递增序号：同一会话每次导入键唯一；
        # 若 settings 丢失导致序号重置，旧键会被 INSERT OR IGNORE 挡住，绝不重复计费
        baselines[sid] = {"h": hit, "m": miss, "c": comp, "n": n}
        pending.append((sid, n, delta))

    settings["dsh_baselines"] = baselines
    if not pending:
        return 0

    dt = datetime.fromtimestamp(mtime_ms / 1000.0)  # 投影最后更新时刻≈用量发生时刻
    rows_out = []
    for sid, n, (hit, miss, comp) in pending:
        model = config.get("unknown_model_fallback", "deepseek-v4-flash")
        price = pricing.get_price(model, config)
        cost = pricing.calc_cost(
            {"prompt_cache_hit_tokens": hit,
             "prompt_cache_miss_tokens": miss,
             "completion_tokens": comp}, price)
        key = "dsh:" + str(sid) + ":" + str(n)
        rows_out.append((key, dt, model, hit, miss, comp, cost))
    return storage.add_external_requests(rows_out)


def run(config: dict, settings: dict, state: dict, stop_event):
    """后台线程入口：周期增量同步 Harness 会话用量。"""
    global _LOG
    dsh = config.get("dsh") or {}
    data_dir = os.path.dirname(storage._db_path or "")
    if data_dir:
        _LOG = os.path.join(data_dir, "dsh_sync.log")
    interval = max(2, int(dsh.get("sync_interval_seconds", 5)))
    state["dsh_sync"] = {"enabled": bool(dsh.get("enabled", True)), "total_added": 0,
                         "last_added": 0, "last_time": None, "error": None}
    _log(f"同步线程启动，间隔={interval}秒，文件={_projcache_path(config)}")

    while not stop_event.wait(interval):
        if not (config.get("dsh") or {}).get("enabled", True):
            state["dsh_sync"]["enabled"] = False  # 设置页关闭后线程常驻等待，重新开启即恢复
            state["dsh_sync"]["error"] = None
            continue
        try:
            added = sync_once(config, settings)
            info = state["dsh_sync"]
            info["enabled"] = True  # 恢复同步时标记启用（设置页开关状态同步）
            if added:
                info["total_added"] = info.get("total_added", 0) + added
                info["last_added"] = added
            info["last_time"] = datetime.now().strftime("%H:%M:%S")
            info["error"] = None
            _log(f"本轮新增={added} 累计={info.get('total_added', 0)}")
        except Exception as exc:
            state["dsh_sync"]["error"] = str(exc)
            _log("ERROR: " + str(exc))
