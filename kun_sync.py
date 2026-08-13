# -*- coding: utf-8 -*-
"""Kun 数据源同步模块。

设计目标：不加任何转发跳数、不增加 API 延迟、不改 Kun 的任何配置。
Kun 把每次模型调用的用量以 kind="usage" 事件追加写到
~/.kun/data/threads/<线程ID>/events.jsonl（父线程与子代理线程都有）。

重要：usage 事件的数值是"到第 N 轮为止的会话累积值"（turns=N，同一轮会
重复写两条相同快照），不是单次调用量。因此本模块用「累积差分」还原单轮
用量：本轮增量 = 当前累积 - 上次已导入的累积。Kun 会在文件过大时裁剪
开头，首次遇到某个线程时若 turns==1 直接导入（=首轮用量），否则只建立
基线不导入，避免把裁剪前的累积虚高计入。

计费口径：与 CC Switch 同步一致，按 config.json 的人民币单价用还原后的
token 数重新计算，保证与本软件其他统计口径一致、可自行调整。
只提取 usage 字段，不读取、不保存任何对话内容。
"""
import json
import os
from datetime import datetime

import pricing
import storage

_LOG = None  # 日志文件路径，由 run() 初始化

# observability 旧数据源覆盖到 2026-07-31（那批用量已入库）。
# events 差分只导入该日期之后的事件，避免同一批 7 月用量被两个数据源重复计费。
_CUTOFF_DATE = datetime(2026, 8, 1).date()


def _kun_dir(config: dict) -> str:
    """解析 Kun 会话数据目录，未配置时用默认位置。"""
    path = (config.get("kun") or {}).get("threads_dir")
    if path:
        return os.path.expandvars(path)
    return os.path.join(os.path.expanduser("~"), ".kun", "data", "threads")


def _log(text: str):
    """同步过程日志（仅排查用），写入 data 目录的 kun_sync.log。"""
    if not _LOG:
        return
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
    except Exception:
        pass


def _parse_event(line: str):
    """解析 events.jsonl 的一行，返回 dict 或 None（非 usage / 半行 / 无效跳过）。"""
    try:
        d = json.loads(line)
    except Exception:
        return None  # 半行（Kun 正在写入）或损坏行，等下次追加后重读
    if d.get("kind") != "usage":
        return None
    usage = d.get("usage") or {}
    if int(usage.get("totalTokens") or 0) <= 0:
        return None  # 占位事件（本轮无模型调用）
    model = d.get("model")
    thread_id = d.get("threadId")
    ts = d.get("timestamp")
    turns = int(usage.get("turns") or 0)
    if not model or not thread_id or not ts or turns <= 0:
        return None
    return {
        "thread": thread_id,
        "turns": turns,
        "ts": ts,
        "model": model,
        "hit": int(usage.get("cacheHitTokens") or 0),
        "miss": int(usage.get("cacheMissTokens") or 0),
        "comp": int(usage.get("completionTokens") or 0),
    }


def _to_local(ts: str):
    """UTC ISO 时间（带 Z）转本地 naive datetime，失败返回 None。"""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().replace(tzinfo=None)
    except Exception:
        return None


def sync_once(config: dict, settings: dict) -> int:
    """同步一轮：增量读取所有线程的 events.jsonl 并做累积差分，返回新增条数。

    游标（字节偏移 + 文件大小）存 settings["kun_sync_cursor"]；
    每个线程的已导入累积基线存 settings["kun_sync_baselines"]。
    """
    kun_dir = _kun_dir(config)
    if not os.path.isdir(kun_dir):
        return 0
    cursor = settings.get("kun_sync_cursor") or {}
    if not isinstance(cursor, dict):
        cursor = {}
    baselines = settings.get("kun_sync_baselines") or {}
    if not isinstance(baselines, dict):
        baselines = {}

    pending = []  # 批量收集待写入记录，最后一次性事务写入
    for name in sorted(os.listdir(kun_dir)):
        path = os.path.join(kun_dir, name, "events.jsonl")
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        pos = cursor.get(path, {})
        offset = int(pos.get("offset", 0) or 0)
        old_size = int(pos.get("size", 0) or 0)
        if offset > size or old_size > size:
            offset = 0  # 文件被重写/裁剪变小：从头读，差分与去重键保证不重复不虚高
        if offset == size:
            continue
        with open(path, "rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line:
                    break
                ev = _parse_event(line.decode("utf-8", errors="replace"))
                if not ev:
                    continue
                ev["dt"] = _to_local(ev["ts"])
                if ev["dt"] is None:
                    continue
                base = baselines.get(ev["thread"])
                cur = (ev["hit"], ev["miss"], ev["comp"])
                if base is None:
                    # 首次见到该线程：turns==1 时累积值即首轮用量，可直接导入；
                    # 否则说明历史被裁剪，只建基线避免把累积虚高计入
                    if ev["turns"] == 1 and ev["dt"].date() >= _CUTOFF_DATE:
                        pending.append((ev, cur))
                    baselines[ev["thread"]] = cur
                    continue
                prev_total = base[0] + base[1] + base[2]
                cur_total = cur[0] + cur[1] + cur[2]
                if cur_total < prev_total:
                    # 累积值回退（文件被整体重写）：以当前为新的基线起点
                    baselines[ev["thread"]] = cur
                    if ev["turns"] == 1 and ev["dt"].date() >= _CUTOFF_DATE:
                        pending.append((ev, cur))
                    continue
                delta = (cur[0] - base[0], cur[1] - base[1], cur[2] - base[2])
                baselines[ev["thread"]] = cur
                if delta[0] + delta[1] + delta[2] <= 0:
                    continue  # 同轮重复快照或未增长，跳过
                if ev["dt"].date() >= _CUTOFF_DATE:
                    pending.append((ev, delta))
            offset = f.tell()
        cursor[path] = {"offset": offset, "size": size}

    settings["kun_sync_cursor"] = cursor
    settings["kun_sync_baselines"] = baselines
    if not pending:
        return 0

    rows = []
    for ev, (hit, miss, comp) in pending:
        dt = _to_local(ev["ts"])
        if dt is None:
            continue
        price = pricing.get_price(ev["model"], config)
        cost = pricing.calc_cost(
            {"prompt_cache_hit_tokens": hit,
             "prompt_cache_miss_tokens": miss,
             "completion_tokens": comp}, price)
        # 去重键：线程 + 轮次。同一轮的两条相同快照只会产生一条增量（第二条差值为 0）
        key = "kun:" + str(ev["thread"]) + ":" + str(ev["turns"])
        rows.append((key, dt, ev["model"], hit, miss, comp, cost))
    return storage.add_external_requests(rows)


def run(config: dict, settings: dict, state: dict, stop_event):
    """后台线程入口：周期增量同步 Kun 会话用量事件。"""
    global _LOG
    kun = config.get("kun") or {}
    data_dir = os.path.dirname(storage._db_path or "")
    if data_dir:
        _LOG = os.path.join(data_dir, "kun_sync.log")
    interval = max(2, int(kun.get("sync_interval_seconds", 3)))
    state["kun_sync"] = {"enabled": bool(kun.get("enabled", True)), "total_added": 0,
                         "last_added": 0, "last_time": None, "error": None}
    _log(f"同步线程启动，间隔={interval}秒，目录={_kun_dir(config)}")

    while not stop_event.wait(interval):
        if not (config.get("kun") or {}).get("enabled", True):
            state["kun_sync"]["enabled"] = False  # 设置页关闭后线程常驻等待，重新开启即恢复
            state["kun_sync"]["error"] = None
            continue
        try:
            added = sync_once(config, settings)
            info = state["kun_sync"]
            info["enabled"] = True  # 恢复同步时标记启用（设置页开关状态同步）
            if added:
                info["total_added"] = info.get("total_added", 0) + added
                info["last_added"] = added
            info["last_time"] = datetime.now().strftime("%H:%M:%S")
            info["error"] = None
            _log(f"本轮新增={added} 累计={info.get('total_added', 0)}")
        except Exception as exc:
            state["kun_sync"]["error"] = str(exc)
            _log("ERROR: " + str(exc))
