# -*- coding: utf-8 -*-
"""test_health.py — 健康自检分级判定回归测试。

重点覆盖 2026-09-14 的真实故障场景：服务器宕机 5 天，客户端必须判成 error
而不是模棱两可的"同步中"。
"""
import os
import sys

import utf8_output  # noqa: F401  # 中文输出兼容（CI 为英文 Windows/cp1252）
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import health


def _ago(**kw):
    return (datetime.now() - timedelta(**kw)).strftime("%H:%M:%S")


def test_ok_when_recent():
    """1 分钟前成功 -> ok。"""
    level, since = health.evaluate(_ago(minutes=1))
    assert level == "ok", "近期成功应判 ok，实际 %s" % level
    assert since is not None and since < 120
    print("PASS 1 近期成功判 ok")


def test_warn_between_5min_and_1h():
    """10 分钟前成功 -> warn。"""
    level, _ = health.evaluate(_ago(minutes=10))
    assert level == "warn", "10 分钟未成功应判 warn，实际 %s" % level
    print("PASS 2 5 分钟~1 小时未成功判 warn")


def test_error_after_one_hour():
    """2 小时前成功 -> error（正是宕机场景）。"""
    level, _ = health.evaluate(_ago(hours=2))
    assert level == "error", "1 小时以上未成功应判 error，实际 %s" % level
    print("PASS 3 超过 1 小时未成功判 error")


def test_error_on_fail_streak():
    """连续失败 3 次 -> error（即使时间还很近）。"""
    level, _ = health.evaluate(_ago(minutes=1), error="连接超时", fail_streak=3)
    assert level == "error", "连续失败 3 次应判 error，实际 %s" % level
    print("PASS 4 连续失败 3 次判 error")


def test_disabled_is_off():
    level, _ = health.evaluate(None, enabled=False)
    assert level == "off"
    print("PASS 5 未启用判 off")


def test_rank_detects_stale_server_day():
    """核心场景：服务器返回的 day 落后 -> 直接 error，并给出明确文案。"""
    rs = {
        "token": "x" * 64,
        "last_time": _ago(minutes=1),      # 上报本身"刚刚成功"
        "error": None,
        "error_streak": 0,
        "day": (date.today() - timedelta(days=5)).isoformat(),   # 数据停 5 天前
    }
    snap = health.snapshot({}, rs)
    rank = snap["rank"]
    assert rank["level"] == "error", "服务器数据落后应判 error，实际 %s" % rank["level"]
    assert "服务器异常" in rank["detail"], "文案未指明服务器异常: %s" % rank["detail"]
    assert "5" in rank["detail"], "文案未给出落后天数: %s" % rank["detail"]
    print("PASS 6 排名通道识别「服务器数据落后」（%s）" % rank["detail"][:50])


def test_rank_ok_when_day_current():
    """服务器 day = 今天 -> ok。"""
    rs = {"token": "x", "last_time": _ago(minutes=1), "error": None,
          "error_streak": 0, "day": date.today().isoformat()}
    snap = health.snapshot({}, rs)
    assert snap["rank"]["level"] == "ok", snap["rank"]
    print("PASS 7 服务器正常时判 ok")


def test_rank_off_when_logged_out():
    snap = health.snapshot({}, {"token": None})
    assert snap["rank"]["level"] == "off", snap["rank"]
    print("PASS 8 未登录判 off")


def test_proxy_states():
    """代理的四种状态映射正确。"""
    assert health.snapshot({"proxy_ready": True})["proxy"]["level"] == "ok"
    assert health.snapshot({"proxy_error": "端口被占用"})["proxy"]["level"] == "error"
    assert health.snapshot({"proxy_ready": False})["proxy"]["level"] == "warn"
    off = health.snapshot({"proxy_enabled": False})
    assert off["proxy"]["level"] == "off"
    print("PASS 9 代理四态映射正确")


def test_midnight_rollover():
    """跨零点保护：解析出的时间晚于当前时间时，应视为昨天而不是"未来"。"""
    future = (datetime.now() + timedelta(hours=3)).strftime("%H:%M:%S")
    level, since = health.evaluate(future)
    assert since is not None and since > 0, "未做跨零点回退: since=%s" % since
    assert level in ("ok", "warn", "error")
    print("PASS 10 跨零点时间回退（since=%ds）" % since)


def test_overall_precedence():
    """整体等级：error 优先于 warn 优先于 ok。"""
    s_ok = {"a": {"level": "ok"}, "b": {"level": "off"}}
    s_warn = {"a": {"level": "ok"}, "b": {"level": "warn"}}
    s_err = {"a": {"level": "warn"}, "b": {"level": "error"}}
    assert health.overall(s_ok) == "ok"
    assert health.overall(s_warn) == "warn"
    assert health.overall(s_err) == "error"
    assert health.overall({"a": {"level": "off"}}) == "off"
    print("PASS 11 整体等级优先级正确")


def test_humanize():
    assert health.humanize(30) == "30 秒前"
    assert health.humanize(300) == "5 分钟前"
    assert health.humanize(7200) == "2 小时前"
    assert health.humanize(None) == "尚无成功记录"
    print("PASS 12 时间人性化文案正确")


if __name__ == "__main__":
    test_ok_when_recent()
    test_warn_between_5min_and_1h()
    test_error_after_one_hour()
    test_error_on_fail_streak()
    test_disabled_is_off()
    test_rank_detects_stale_server_day()
    test_rank_ok_when_day_current()
    test_rank_off_when_logged_out()
    test_proxy_states()
    test_midnight_rollover()
    test_overall_precedence()
    test_humanize()
    print("\nALL HEALTH TESTS PASSED")
