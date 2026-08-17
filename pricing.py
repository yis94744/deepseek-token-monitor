# -*- coding: utf-8 -*-
"""计价模块：按 DeepSeek 官网峰谷计费规则计算费用。

- 官网自 2026-08-17 0 时（北京时间）起对 V4 系列采用峰谷计价：
  高峰时段价格为空闲时段的两倍。
- 高峰时段：每日 09:00-14:00（本地时间，含 9 点整、不含 14 点整）；
  可用 config.json 顶层 `peak_hours: {start_hour, end_hour}` 覆盖。
- 2026-08-17 之前的调用按旧平峰价（models.<model>.legacy）结算，
  生效时刻可用 config.json 顶层 `legacy_until: "2026-08-17"` 覆盖。
- 单价单位：元 / 百万 tokens。config.json 的 models 段可随时修改。
"""

from datetime import datetime

_LEGACY_UNTIL_DEFAULT = datetime(2026, 8, 17, 0, 0, 0)  # 官网新价生效时刻


def _legacy_until(config) -> datetime:
    """官网新价生效时刻（可配置覆盖）。"""
    raw = (config or {}).get("legacy_until")
    if raw:
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d")
        except Exception:
            pass
    return _LEGACY_UNTIL_DEFAULT


def _peak_window(config) -> tuple:
    """高峰时段 [start_hour, end_hour)，默认每日 09:00-14:00。"""
    peak = (config or {}).get("peak_hours") or {}
    try:
        start = int(peak.get("start_hour", 9))
        end = int(peak.get("end_hour", 14))
    except Exception:
        start, end = 9, 14
    return start, end


def is_peak_hour(dt, config=None) -> bool:
    """某时刻是否处于官网高峰时段（默认每日 09:00-14:00，[start, end)）。"""
    if dt is None:
        return False
    start, end = _peak_window(config)
    if start <= end:
        return start <= dt.hour < end
    return dt.hour >= start or dt.hour < end  # 跨天时段


def get_price(model: str, config: dict, ts=None) -> dict:
    """按时间取模型单价表：

    - ts 早于官网新价生效时刻 → 旧平峰价 legacy（未配置则用基准价）
    - ts 处于高峰时段 → peak 表（未配置则用基准价）
    - 其余（含 ts 为空）→ 基准价 cache_hit/cache_miss/output（即空闲价）
    遇到未配置的模型时按兜底模型计价，避免漏计费。
    """
    models = config.get("models", {})
    entry = models.get(model) or {}
    if not entry:
        fallback = config.get("unknown_model_fallback", "")
        entry = models.get(fallback, {})
    if ts is not None:
        if ts < _legacy_until(config):
            legacy = entry.get("legacy") or {}
            if legacy.get("cache_miss") is not None:
                return legacy
        if is_peak_hour(ts, config):
            peak = entry.get("peak") or {}
            if peak.get("cache_miss") is not None:
                return peak
    return entry


def calc_usage(usage: dict) -> tuple:
    """从 usage 中取出三类 token 数：(缓存命中, 缓存未命中, 输出)。"""
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    return hit, miss, completion


def calc_cost(usage: dict, price: dict) -> float:
    """计算一次调用的费用 = 命中数*命中单价 + 未命中数*未命中单价 + 输出数*输出单价。"""
    hit, miss, completion = calc_usage(usage)
    cost = (
        hit * float(price.get("cache_hit", 0.0))
        + miss * float(price.get("cache_miss", 0.0))
        + completion * float(price.get("output", 0.0))
    ) / 1_000_000.0
    return round(cost, 6)
