# -*- coding: utf-8 -*-
"""计价模块：按 DeepSeek 官网峰谷计费规则计算费用。

- 官网自 2026-08-17 0 时（北京时间）起对 V4 系列采用峰谷计价：
  高峰时段价格为空闲时段的两倍，空闲时段价格为高峰时段价格的一半。
- 高峰时段（工作日）：北京时间 9:00-12:00 与 14:00-18:00（含起点、不含终点）；
  可用 config.json 顶层 `peak_hours` 覆盖，格式支持：
    新格式 [[9,12],[14,18]]（多段）；旧格式 {"start_hour":9,"end_hour":14}（单段，兼容）
- 周末规则：2026-08-23 0 时起，周六/周日全天统一按低谷价计费（不再区分峰谷）；
  生效时刻可用 config.json 顶层 `weekend_offpeak_since: "2026-08-23"` 覆盖（留空则不启用）
- 2026-08-17 之前的调用按旧平峰价（models.<model>.legacy）结算，
  生效时刻可用 config.json 顶层 `legacy_until: "2026-08-17"` 覆盖。
- 2026-09-10 12:00 起 Flash 系列降价（空闲 命中0.02/未命中1/输出4），
  高峰翻倍（0.04/2/8）；用 models.<model>.tiers 多段价表达：
    tiers: [{"since": "2026-09-10 12:00", "cache_hit":..., ...}, ...]
  get_price 按 ts 选"since 不晚于 ts 的最后一段"；未配 tiers 时退回
  legacy / 基准价 / peak 三段式（完全向后兼容老配置）。
- 2026-09-14 12:00 起 V4 Pro 下线，请求路由到 V4.1 Flash 并按 Flash 计费：
  用 config 顶层 `v4_pro_retire_at` + `v4_pro_retire_model` 配置。
- 单价单位：元 / 百万 tokens。config.json 的 models 段可随时修改。
"""

from datetime import datetime

_LEGACY_UNTIL_DEFAULT = datetime(2026, 8, 17, 0, 0, 0)  # 官网新价生效时刻
_WEEKEND_OFFPEAK_SINCE_DEFAULT = datetime(2026, 8, 23, 0, 0, 0)  # 周末低谷价生效时刻


def _parse_ts(raw):
    """解析配置里的时间字符串：支持 "YYYY-MM-DD" 与 "YYYY-MM-DD HH:MM"（含 "T"）。"""
    if not raw:
        return None
    s = str(raw).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _tiers(entry) -> list:
    """模型条目里的多段价（按 since 升序）。无则返回空列表。"""
    raw = (entry or {}).get("tiers")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        since = _parse_ts(item.get("since"))
        if since is None:
            continue
        out.append((since, item))
    out.sort(key=lambda x: x[0])
    return out


def _pick_tier(entry, ts):
    """按 ts 选多段价中生效的那一段；ts 早于所有段则返回 None。"""
    tiers = _tiers(entry)
    if not tiers:
        return None
    chosen = None
    for since, item in tiers:
        if ts >= since:
            chosen = item
        else:
            break
    return chosen


def _v4_pro_retire(config):
    """V4 Pro 下线时刻与替代模型（下线后按替代模型计费）。未配置返回 (None, None)。"""
    at = _parse_ts((config or {}).get("v4_pro_retire_at"))
    model = (config or {}).get("v4_pro_retire_model") or ""
    return at, (model or None)


def _legacy_until(config) -> datetime:
    """官网新价生效时刻（可配置覆盖）。"""
    raw = (config or {}).get("legacy_until")
    if raw:
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d")
        except Exception:
            pass
    return _LEGACY_UNTIL_DEFAULT


def _weekend_offpeak_since(config):
    """周末全天低谷价的生效时刻（留空/无法解析则不启用周末规则）。"""
    raw = (config or {}).get("weekend_offpeak_since")
    if raw:
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d")
        except Exception:
            return None
    return _WEEKEND_OFFPEAK_SINCE_DEFAULT


def _peak_window(config) -> list:
    """高峰时段列表 [(start, end), ...]，默认工作日 9:00-12:00 与 14:00-18:00。

    兼容旧格式 {"start_hour": 9, "end_hour": 14}（单段，向后兼容）。
    """
    peak = (config or {}).get("peak_hours")
    if isinstance(peak, dict):  # 旧格式单段
        try:
            return [(int(peak.get("start_hour", 9)), int(peak.get("end_hour", 12)))]
        except Exception:
            return []
    windows = []
    try:
        for item in peak or []:
            windows.append((int(item[0]), int(item[1])))
    except Exception:
        windows = []
    if not windows:
        return [(9, 12), (14, 18)]
    return windows


def _in_any_window(hour: int, windows: list) -> bool:
    for start, end in windows:
        if start <= end:
            if start <= hour < end:
                return True
        else:  # 跨天时段
            if hour >= start or hour < end:
                return True
    return False


def is_peak_hour(dt, config=None) -> bool:
    """某时刻是否处于高峰时段。

    规则（2026-08-17 起）：工作日高峰 9:00-12:00 与 14:00-18:00；
    2026-08-23 起周末（周六/周日）全天按低谷价，不再区分峰谷。
    """
    if dt is None:
        return False
    since = _weekend_offpeak_since(config)
    if dt.weekday() >= 5 and since is not None and dt >= since:
        return False  # 周末全天低谷价
    return _in_any_window(dt.hour, _peak_window(config))


def resolve_model(model: str, config: dict, ts=None) -> str:
    """按下线路由规则解析实际计费模型名。

    2026-09-14 12:00 起 V4 Pro 下线：官方把 V4 Pro 请求路由到 V4.1 Flash，
    并按 V4.1 Flash 单价计费（配置项 v4_pro_retire_at / v4_pro_retire_model）。
    """
    at, repl = _v4_pro_retire(config)
    if at is not None and repl and ts is not None and ts >= at:
        m = str(model or "").lower()
        # 覆盖 v4-pro 及带版本后缀的别名（如 deepseek-v4-pro-0813）
        if "v4-pro" in m or "v4.pro" in m:
            return repl
    return model


def get_price(model: str, config: dict, ts=None) -> dict:
    """按时间取模型单价表：

    取价优先级（先命中先返回）：
    1. ts 命中多段价 tiers（since 不晚于 ts 的最后一段）→ 该段价（含其 peak 子表）
    2. ts 早于官网新价生效时刻 → 旧平峰价 legacy（未配置则用基准价）
    3. ts 处于高峰时段 → peak 表（未配置则用基准价）
    4. 其余（含 ts 为空）→ 基准价 cache_hit/cache_miss/output（即空闲价）

    V4 Pro 下线后（默认 2026-09-14 12:00）自动按替代模型（V4.1 Flash）计价。
    遇到未配置的模型时按兜底模型计价，避免漏计费。
    """
    models = config.get("models", {})
    model = resolve_model(model, config, ts)
    entry = models.get(model) or {}
    if not entry:
        fallback = config.get("unknown_model_fallback", "")
        entry = models.get(fallback, {})
    if ts is not None:
        tier = _pick_tier(entry, ts)
        if tier is not None:
            # 多段价：段内仍区分峰谷（段的 peak 子表可选）
            if is_peak_hour(ts, config):
                peak = tier.get("peak") or {}
                if peak.get("cache_miss") is not None:
                    return peak
            if tier.get("cache_miss") is not None:
                return tier
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
