# -*- coding: utf-8 -*-
"""代理层集成测试：真实 HTTP 请求 → 抓 Authorization 头 → 入库 Key 指纹（临时脚本，用完即删）。"""
import json
import shutil
import tempfile
import threading
import time
from datetime import date
from http.client import HTTPConnection

import proxy_server
import storage


class FakeResponse:
    status = 200
    reason = "OK"

    def __init__(self, payload):
        self.payload = payload

    def getheaders(self):
        return [("Content-Type", "application/json")]

    def read(self, n=-1):
        if n == -1:
            data, self.payload = self.payload, b""
            return data
        data, self.payload = self.payload[:n], self.payload[n:]
        return data


class FakeConnection:
    last = None      # 记录最近一次实例，供测试检查转发头
    next_resp = None  # 若设置，下一次请求用它作为上游响应（流式 SSE 场景）

    def __init__(self, host, port, timeout=None):
        FakeConnection.last = self
        self.sent = None
        self.resp = None

    def request(self, method, path, body=None, headers=None):
        self.sent = (method, path, body, headers)
        if FakeConnection.next_resp is not None:
            self.resp = FakeConnection.next_resp
            FakeConnection.next_resp = None
            return
        usage = {"prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 200,
                 "completion_tokens": 300}
        self.resp = FakeResponse(json.dumps(
            {"id": "x", "model": "deepseek-v4-flash", "usage": usage}).encode("utf-8"))

    def getresponse(self):
        return self.resp

    def close(self):
        pass


proxy_server.HTTPSConnection = FakeConnection  # 替换模块级上游连接（不真连外网）

tmp = tempfile.mkdtemp(prefix="dsh-proxy-test-")
try:
    storage.init_db(tmp)
    config = {
        "proxy_enabled": True, "proxy_host": "127.0.0.1", "proxy_port": 8799,
        "upstream_base_url": "https://api.deepseek.com",
        "models": {"deepseek-v4-flash": {"cache_hit": 0.02, "cache_miss": 1.0, "output": 2.0}},
    }
    state = {}
    threading.Thread(target=proxy_server.start_proxy, args=(config, state), daemon=True).start()
    for _ in range(100):
        if state.get("proxy_ready"):
            break
        time.sleep(0.05)
    assert state.get("proxy_ready"), state

    def send(body, headers):
        conn = HTTPConnection("127.0.0.1", 8799, timeout=10)
        conn.request("POST", "/v1/chat/completions", body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    today = date.today().isoformat()

    # 1) 带 Authorization 的非流式请求
    body = json.dumps({"model": "deepseek-v4-flash",
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    status, _ = send(body, {"Authorization": "Bearer sk-test-key-1234abcd",
                            "Content-Type": "application/json"})
    assert status == 200
    rows = storage.key_breakdown(today, today)
    assert len(rows) == 1 and rows[0]["key_hint"] == "sk-****abcd", rows
    assert rows[0]["requests"] == 1 and rows[0]["completion"] == 300
    print("PASS 1 带 Bearer 的请求按 Key 入库")

    # 2) 转发时确实带上了 Authorization 头
    sent_headers = FakeConnection.last.sent[3]
    assert any(k.lower() == "authorization" for k in sent_headers), sent_headers
    assert sent_headers["Authorization"] == "Bearer sk-test-key-1234abcd"
    print("PASS 2 Authorization 头被转发到上游")

    # 3) 第二个 Key 分组独立
    body = json.dumps({"model": "deepseek-v4-flash",
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    send(body, {"Authorization": "sk-another-key-9999zzzz",
                "Content-Type": "application/json"})
    rows = storage.key_breakdown(today, today)
    assert len(rows) == 2, rows
    assert {r["key_hint"] for r in rows} == {"sk-****abcd", "sk-****zzzz"}
    print("PASS 3 不同 Key 分组独立")

    # 4) 无 Authorization 的请求归入"其他来源"
    send(body, {"Content-Type": "application/json"})
    rows = storage.key_breakdown(today, today)
    assert any(r["key_hint"] == "其他来源（无 Key）" and r["requests"] == 1 for r in rows), rows
    print("PASS 4 无 Key 请求归入其他来源")

    # 5) 流式请求：自动补 include_usage 并统计
    stream_body = json.dumps({"model": "deepseek-v4-flash", "stream": True,
                              "messages": [{"role": "user", "content": "hi"}]}).encode()
    FakeConnection.next_resp = FakeResponse(b"".join([
        b'data: {"id":"x","model":"deepseek-v4-flash","choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: {"id":"x","model":"deepseek-v4-flash","choices":[],'
        b'"usage":{"prompt_cache_hit_tokens":1,"prompt_cache_miss_tokens":2,'
        b'"completion_tokens":3}}\n\n',
        b"data: [DONE]\n\n",
    ]))
    status, _ = send(stream_body, {"Authorization": "Bearer sk-test-key-1234abcd",
                                   "Content-Type": "application/json"})
    assert status == 200
    sent_body = json.loads(FakeConnection.last.sent[2])
    assert sent_body.get("stream_options") == {"include_usage": True}
    rows = storage.key_breakdown(today, today)
    sk = next(r for r in rows if r["key_hint"] == "sk-****abcd")
    assert sk["requests"] == 2 and sk["completion"] == 303, sk
    print("PASS 5 流式自动补 include_usage + 按 Key 累计")

    state["proxy_server"].shutdown()
    print("\nALL PROXY TESTS PASSED")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
