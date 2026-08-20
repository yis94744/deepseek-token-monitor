# -*- coding: utf-8 -*-
"""定时任务模块：余额定时刷新 + 每日/每周/每月自动总结。

- 余额：每隔 balance_refresh_seconds 秒调用官方余额接口刷新一次
- 日结：每天 00:00 后自动把"昨天"的数据固化为日结快照
- 周结：每周一 00:00 后自动把"上周"的数据固化为周结快照
- 月结：每月 1 日 00:00 后自动把"上个月"的数据固化为月结快照
"""
import json
import threading
import urllib.request
from datetime import date, datetime

import storage

_BALANCE_URL = "https://api.deepseek.com/user/balance"


def fetch_balance(config: dict) -> float:
    """调用官方余额接口，返回 CNY 总余额；失败时抛出异常。"""
    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("config.json 未配置 api_key")
    req = urllib.request.Request(
        _BALANCE_URL,
        headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for info in data.get("balance_infos", []):
        if info.get("currency") == "CNY":
            return float(info.get("total_balance") or 0)
    raise ValueError("余额响应中未找到 CNY 币种信息")


def run(config: dict, state: dict, stop_event: threading.Event):
    """后台线程入口：周期刷新余额；检测跨天/跨周/跨月并自动写总结快照。"""
    interval = max(30, int(config.get("balance_refresh_seconds", 300)))

    last_balance_ts = None
    last_day = date.today()
    last_week = storage.week_range_start()
    last_month = last_day.strftime("%Y-%m")

    while not stop_event.is_set():
        now = datetime.now()

        # 1) 余额定时刷新
        if last_balance_ts is None or (now - last_balance_ts).total_seconds() >= interval:
            last_balance_ts = now
            try:
                state["balance"] = fetch_balance(config)
                state["balance_error"] = None
                storage.save_balance_snapshot(state["balance"])  # 余额每日快照
            except Exception as exc:
                state["balance"] = None
                state["balance_error"] = str(exc)
            state["balance_updated_at"] = now.isoformat(timespec="seconds")

        today = now.date()

        # 2) 跨天 → 给"昨天"写日结快照
        if today != last_day:
            storage.save_daily_summary(last_day)
            last_day = today

        # 3) 跨周 → 给"上周"写周结快照
        this_week = storage.week_range_start()
        if this_week != last_week:
            storage.save_weekly_summary(last_week)
            last_week = this_week

        # 4) 跨月 → 给"上个月"写月结快照
        this_month = today.strftime("%Y-%m")
        if this_month != last_month:
            storage.save_monthly_summary(last_month)
            last_month = this_month

        stop_event.wait(5)