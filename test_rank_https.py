# -*- coding: utf-8 -*-
"""test_rank_https.py — HTTPS 优先与自动回退的回归测试。

背景：服务器 443 尚未在腾讯云安全组放行时，客户端不能因此不可用。
策略：HTTPS 优先，握手失败自动回退 HTTP；一旦服务器有 HTTP 响应
（含 401）就认定该链路可用，不再回退。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rank_client as rc


class _FakeResp:
    def __init__(self, body):
        self._body = body.encode("utf-8")
        self.status = 200
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch_fn):
    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = monkeypatch_fn
    return original


def test_https_preferred_when_available():
    """HTTPS 正常时，应优先使用 https 且不回退。"""
    calls = []

    def fake(req, **kwargs):
        calls.append(req.full_url)
        return _FakeResp('{"ok": true, "day": "2026-09-14"}')

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake
    try:
        c = rc.RankClient(base="http://example.test", token="t")
        c._request("GET", "/api/health")
        assert calls == ["https://example.test/api/health"], \
            "应优先走 https，实际 %r" % (calls,)
        print("PASS 1 HTTPS 可用时优先走 https（未回退）")
    finally:
        urllib.request.urlopen = orig


def test_fallback_to_http_when_https_fails():
    """HTTPS 握手失败时，应自动回退到 HTTP 并成功。"""
    calls = []

    def fake(req, **kwargs):
        calls.append(req.full_url)
        if req.full_url.startswith("https://"):
            raise OSError("handshake timed out")
        return _FakeResp('{"ok": true, "day": "2026-09-14"}')

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake
    try:
        c = rc.RankClient(base="http://example.test", token="t")
        r = c._request("GET", "/api/health")
        assert r.get("ok") is True, "回退后应成功"
        assert calls == ["https://example.test/api/health",
                         "http://example.test/api/health"], \
            "应先试 https 再回退 http，实际 %r" % (calls,)
        print("PASS 2 HTTPS 失败自动回退 HTTP")
    finally:
        urllib.request.urlopen = orig


def test_https_failure_is_cached():
    """HTTPS 判定不可用后，后续请求不应再重复等待超时。"""
    calls = []

    def fake(req, **kwargs):
        calls.append(req.full_url)
        if req.full_url.startswith("https://"):
            raise OSError("handshake timed out")
        return _FakeResp('{"ok": true}')

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake
    try:
        c = rc.RankClient(base="http://example.test", token="t")
        c._request("GET", "/api/health")
        n_first = len(calls)
        calls.clear()
        c._request("GET", "/api/health")     # 第二次
        assert not any(u.startswith("https://") for u in calls), \
            "第二次不应再试 https（应已缓存失败），实际 %r" % (calls,)
        print("PASS 3 HTTPS 失败结果被缓存（第二次直接走 http，省掉超时等待）")
    finally:
        urllib.request.urlopen = orig


def test_no_fallback_on_http_error():
    """服务器返回 401 属"链路可用"，不应回退到 HTTP。"""
    import urllib.error
    calls = []

    def fake(req, **kwargs):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake
    try:
        c = rc.RankClient(base="http://example.test", token="t")
        try:
            c._request("GET", "/api/board")
            raise AssertionError("应抛出 RankError")
        except rc.RankError as e:
            assert e.code == "TOKEN_INVALID", "应识别为登录失效，实际 %s" % e.code
        assert calls == ["https://example.test/api/board"], \
            "有 HTTP 响应就不该回退，实际 %r" % (calls,)
        print("PASS 4 服务器有响应(401)时不回退")
    finally:
        urllib.request.urlopen = orig


def test_pure_https_base_falls_back_to_http():
    """用户显式填了 https:// 但 443 不通时，也应能回退到 http。"""
    calls = []

    def fake(req, **kwargs):
        calls.append(req.full_url)
        if req.full_url.startswith("https://"):
            raise OSError("handshake timed out")
        return _FakeResp('{"ok": true}')

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake
    try:
        c = rc.RankClient(base="https://example.test", token="t")
        r = c._request("GET", "/api/health")
        assert r.get("ok") is True
        assert calls[-1].startswith("http://"), "应回退到 http，实际 %r" % (calls,)
        print("PASS 5 https:// 地址在 443 不通时回退 http")
    finally:
        urllib.request.urlopen = orig


if __name__ == "__main__":
    test_https_preferred_when_available()
    test_fallback_to_http_when_https_fails()
    test_https_failure_is_cached()
    test_no_fallback_on_http_error()
    test_pure_https_base_falls_back_to_http()
    print("\nALL RANK HTTPS TESTS PASSED")
