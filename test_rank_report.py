# -*- coding: utf-8 -*-
"""test_rank_report.py — 排名上报的安全边界回归测试。

锁定 2026-09-14 真实事故：_today_tokens() 在本地数据库未就绪时返回 0，
上报后把服务端"当日累计"覆盖成 0，当天已累积的 260 万 token 永久丢失。

服务端语义是"当日累计的最新值"（覆盖写），所以客户端**绝不能**在读取
失败时上报 0——必须跳过本次上报。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rank_client as rc


class _FakeClient:
    """记录被调用的上报客户端。"""
    def __init__(self):
        self.calls = []
        self.user = {"user_id": 1}

    def report_today(self, tokens, board_version=""):
        self.calls.append(tokens)
        return {"ok": True, "day": "2026-09-14", "board_version": "v1", "board": []}


def test_returns_none_when_db_not_ready():
    """数据库未初始化时必须返回 None，而不是 0。"""
    import storage
    saved = getattr(storage, "_db_path", None)
    try:
        storage._db_path = None
        got = rc._today_tokens()
        assert got is None, "数据库未就绪时应返回 None，实际返回 %r" % (got,)
        print("PASS 1 数据库未就绪 -> 返回 None（不再误报 0）")
    finally:
        storage._db_path = saved


def test_sync_skips_report_on_failure():
    """读取失败时必须跳过上报，绝不能发出 tokens=0。"""
    import storage
    saved = getattr(storage, "_db_path", None)
    try:
        storage._db_path = None          # 模拟未就绪
        c = _FakeClient()
        ok = rc._sync_once(c)
        assert ok is False, "读取失败时应返回 False"
        assert c.calls == [], "读取失败时不应发出任何上报，实际发了 %r" % (c.calls,)
        assert rc.get_state().get("error"), "应留下错误说明"
        print("PASS 2 读取失败 -> 跳过上报（未发送 tokens=0）")
    finally:
        storage._db_path = saved
        rc._state["error"] = None


def test_normal_report_still_works():
    """正常情况仍要照常上报真实用量。"""
    c = _FakeClient()
    saved = rc._today_tokens
    try:
        rc._today_tokens = lambda: 12345
        rc._state["board_version"] = ""
        ok = rc._sync_once(c)
        assert ok is True, "正常路径应返回 True"
        assert c.calls == [12345], "应上报真实用量，实际 %r" % (c.calls,)
        print("PASS 3 正常路径照常上报（tokens=12345）")
    finally:
        rc._today_tokens = saved


def test_zero_is_still_a_valid_value():
    """真实的 0（确实没有用量）仍应正常上报——只拦截"读取失败"。"""
    c = _FakeClient()
    saved = rc._today_tokens
    try:
        rc._today_tokens = lambda: 0
        rc._state["board_version"] = ""
        ok = rc._sync_once(c)
        assert ok is True and c.calls == [0], "真实的 0 应照常上报，实际 %r" % (c.calls,)
        print("PASS 4 真实的 0 仍正常上报（只拦截读取失败）")
    finally:
        rc._today_tokens = saved


if __name__ == "__main__":
    test_returns_none_when_db_not_ready()
    test_sync_skips_report_on_failure()
    test_normal_report_still_works()
    test_zero_is_still_a_valid_value()
    print("\nALL RANK REPORT TESTS PASSED")
