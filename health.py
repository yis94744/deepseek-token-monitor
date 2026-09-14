# -*- coding: utf-8 -*-
"""健康自检：把"静默失败"变成"看得见的红字"。

背景（2026-09-14 实测）：云排名服务器宕机 5 天，客户端界面上只显示
"排名同步失败"，updater.log 里却是"已是最新版本"——两条独立通道的健康度
完全脱钩，用户完全无法判断到底哪一块坏了。

本模块提供一个统一的健康快照 + 分级判定：

  等级   判定                                    界面颜色
  ok     最近成功 < 5 分钟                       绿
  warn   最近成功 < 1 小时，或偶发失败            黄
  error  最近成功 >= 1 小时，或连续失败 >= 3 次    红
  off    该功能未启用                             灰

额外判据（能抓出"进程活着但业务停止"的半死状态）：
  - 排名通道：服务器返回的 day 落后本地日期 >= 1 天 -> 直接 error
"""
from datetime import date, datetime

# 等级 -> 颜色（与 token_monitor 的配色体系一致）
LEVEL_COLORS = {
    "ok": "#3f9e5a",       # C_GREEN
    "warn": "#d77522",     # C_ORANGE_DEEP
    "error": "#d9534f",    # C_RED
    "off": "#8a6a4d",      # C_SUB
    "unknown": "#8a6a4d",
}

WARN_AFTER_SECONDS = 5 * 60      # 5 分钟没成功 -> 黄
ERROR_AFTER_SECONDS = 60 * 60    # 1 小时没成功 -> 红
ERROR_FAIL_STREAK = 3            # 连续失败 3 次 -> 红


def _parse_time(value):
    """把 "HH:MM:SS" / ISO 字符串解析成 datetime（仅用于算差值）。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    # "HH:MM:SS"（各 sync 模块的 last_time 格式）
    for fmt in ("%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            t = datetime.strptime(text, fmt)
            now = datetime.now()
            if fmt == "%H:%M:%S":
                return now.replace(hour=t.hour, minute=t.minute, second=t.second,
                                   microsecond=0)
            return t
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def evaluate(last_ok, error=None, fail_streak=0, enabled=True, extra_stale=False):
    """计算单个通道的健康等级。

    返回 (level, seconds_since_ok or None)。
    """
    if not enabled:
        return "off", None
    last_dt = _parse_time(last_ok)
    if last_dt is None:
        # 从未成功过：有错误就是 error，否则等一会（unknown）
        if error:
            return "error", None
        return "unknown", None

    # 跨零点保护：last_time 只有时分秒，若解析出来比现在晚很多，说明是昨天的
    now = datetime.now()
    if last_dt > now:
        from datetime import timedelta
        last_dt -= timedelta(days=1)
    delta = (now - last_dt).total_seconds()

    if extra_stale or delta >= ERROR_AFTER_SECONDS or fail_streak >= ERROR_FAIL_STREAK:
        return "error", delta
    if delta >= WARN_AFTER_SECONDS or error or fail_streak > 0:
        return "warn", delta
    return "ok", delta


def humanize(seconds):
    """把秒数变成"3 分钟前 / 2 小时前"这类人话。"""
    if seconds is None:
        return "尚无成功记录"
    seconds = int(seconds)
    if seconds < 60:
        return "%d 秒前" % seconds
    if seconds < 3600:
        return "%d 分钟前" % (seconds // 60)
    if seconds < 86400:
        return "%d 小时前" % (seconds // 3600)
    return "%d 天前" % (seconds // 86400)


def snapshot(state, rank_state=None, now=None):
    """汇总所有通道的健康状态，供界面一次性渲染。

    返回 {通道名: {level, detail, color, since}}
    通道：proxy / balance / cc / dsh / codebuddy / workbuddy / rank / update
    """
    out = {}

    # ---- 本地代理 ----
    if not state.get("proxy_enabled", True):
        out["proxy"] = {"level": "off", "detail": "已关闭", "since": None}
    elif state.get("proxy_error"):
        out["proxy"] = {"level": "error", "detail": str(state["proxy_error"]), "since": None}
    elif state.get("proxy_ready"):
        out["proxy"] = {"level": "ok", "detail": "运行中", "since": 0}
    else:
        out["proxy"] = {"level": "warn", "detail": "启动中…", "since": None}

    # ---- 余额接口 ----
    if state.get("balance") is not None:
        out["balance"] = {"level": "ok", "detail": "正常", "since": 0,
                          "last_ok": state.get("balance_updated_at")}
    elif state.get("balance_error"):
        out["balance"] = {"level": "warn", "detail": str(state["balance_error"])[:60],
                          "since": None, "last_ok": state.get("balance_updated_at")}
    else:
        out["balance"] = {"level": "unknown", "detail": "加载中…", "since": None}

    # ---- 各数据源同步 ----
    for key, label in (("cc_sync", "CC Switch"), ("dsh_sync", "DSH Harness"),
                       ("codebuddy_sync", "CodeBuddy"), ("workbuddy_sync", "WorkBuddy")):
        info = state.get(key) or {}
        enabled = bool(info.get("enabled"))
        if not info:
            out[key] = {"level": "unknown", "detail": "未启动", "since": None}
            continue
        level, since = evaluate(info.get("last_time"), info.get("error"),
                                info.get("fail_streak", 0), enabled)
        detail = ("运行中 · 累计导入 %s 条 · %s"
                  % (info.get("total_added", 0), humanize(since))) if level == "ok" \
            else (str(info.get("error"))[:60] if info.get("error")
                  else "%s · 上次成功 %s" % (label, humanize(since)))
        out[key] = {"level": level, "detail": detail, "since": since,
                    "label": label, "last_ok": info.get("last_time")}

    # ---- 排名通道（核心：能识别"服务器异常"）----
    if rank_state is None:
        out["rank"] = {"level": "off", "detail": "未登录", "since": None}
    else:
        token = rank_state.get("token")
        if not token:
            out["rank"] = {"level": "off", "detail": "未登录", "since": None}
        else:
            server_day = rank_state.get("day")
            stale = False
            stale_reason = ""
            if server_day:
                try:
                    d = date.fromisoformat(str(server_day))
                    behind = (date.today() - d).days
                    if behind >= 1:
                        stale = True
                        stale_reason = "服务器数据停留在 %s（落后 %d 天）" % (server_day, behind)
                except Exception:
                    pass
            else:
                # 从未拿到过 day：若已经报错，视为异常
                if rank_state.get("error"):
                    stale = True
                    stale_reason = str(rank_state["error"])[:70]
            level, since = evaluate(rank_state.get("last_time"),
                                    rank_state.get("error"),
                                    rank_state.get("error_streak", 0),
                                    True, extra_stale=stale)
            if stale and stale_reason:
                detail = "服务器异常：" + stale_reason
            elif rank_state.get("error"):
                detail = "%s · 上次成功 %s" % (str(rank_state["error"])[:50], humanize(since))
            else:
                detail = "同步正常 · %s" % humanize(since)
            out["rank"] = {"level": level, "detail": detail, "since": since,
                           "last_ok": rank_state.get("last_time")}

    # ---- 更新检查 ----
    up = state.get("update")
    if not state.get("update_enabled", True):
        out["update"] = {"level": "off", "detail": "已关闭", "since": None}
    elif not up:
        out["update"] = {"level": "unknown", "detail": "未检查", "since": None}
    elif up.get("error"):
        out["update"] = {"level": "warn", "detail": str(up["error"])[:60], "since": None}
    else:
        out["update"] = {"level": "ok",
                         "detail": "已是最新 %s" % (up.get("tag") or ""), "since": 0}

    for v in out.values():
        v["color"] = LEVEL_COLORS.get(v["level"], LEVEL_COLORS["unknown"])
    return out


def overall(snap):
    """整体健康等级：任一 error -> error；任一 warn -> warn；否则 ok。"""
    levels = [v["level"] for v in snap.values()]
    if "error" in levels:
        return "error"
    if "warn" in levels:
        return "warn"
    if all(l == "off" for l in levels):
        return "off"
    return "ok"


def problems(snap):
    """列出所有非 ok/off 的通道，供状态栏摘要文字使用。"""
    return [(k, v) for k, v in snap.items() if v["level"] in ("error", "warn")]
