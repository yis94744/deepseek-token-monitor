# -*- coding: utf-8 -*-
"""本地 HTTP 代理模块。

监听 http://127.0.0.1:8787，把所有请求原样转发到 DeepSeek 官方接口；
当请求是聊天补全接口（/chat/completions）时，解析响应中的 usage 字段并自动入库计费。

知识点：DeepSeek 流式接口默认不返回 usage，需要在请求体里加
"stream_options": {"include_usage": true}，最后一个数据块才会携带 token 数；
否则本次流式调用无法统计到 token（计费为 0，但仍会记录一条请求）。
"""
import json
import threading
from datetime import datetime
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pricing
import storage

# 转发到上游时需去掉的"逐跳"请求头（只对 客户端↔代理 这一段有意义）
_SKIP_REQUEST_HEADERS = {
    "host", "content-length", "connection", "accept-encoding", "transfer-encoding",
    "keep-alive", "proxy-connection",
}
# 回写客户端时需去掉的"逐跳"响应头
_SKIP_RESPONSE_HEADERS = {
    "connection", "transfer-encoding", "keep-alive", "content-encoding",
}


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # 每个请求处理完即关闭连接，实现简单可靠

    def log_message(self, *args):
        pass  # 关闭默认访问日志，避免刷屏

    # ---- 统一入口：所有 HTTP 方法都走同一段转发逻辑 ----
    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_OPTIONS(self):
        self._handle()

    def _handle(self):
        try:
            self._forward()
        except Exception:
            # 代理自身出错时尽量给调用方一个明确响应，避免客户端一直等待
            try:
                self.send_error(502, "Proxy error")
            except Exception:
                pass
        finally:
            self.close_connection = True

    def _forward(self):
        config = self.server.config  # 由代理服务器对象在启动时注入

        # 1) 读取请求体
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""

        # 2) 解析上游地址
        upstream = urlsplit(config["upstream_base_url"])
        host = upstream.hostname
        port = upstream.port or (443 if upstream.scheme == "https" else 80)
        if upstream.scheme != "https":
            raise ValueError("上游仅支持 https")

        # 3) 组装转发请求头（保留 Authorization 等业务头）
        req_headers = {}
        for key, value in self.headers.items():
            if key.lower() not in _SKIP_REQUEST_HEADERS:
                req_headers[key] = value
        req_headers["Host"] = host

        path = self.path

        # 3.5) 聊天接口预处理：解析请求体，给流式请求自动补 include_usage
        #      （先去掉查询串再匹配路径，兼容 /chat/completions?api-version=... 等）
        is_chat = urlsplit(path).path.endswith("/chat/completions")
        is_stream = False
        if is_chat and body:
            try:
                req_json = json.loads(body.decode("utf-8"))
            except Exception:
                req_json = None
            if isinstance(req_json, dict):
                is_stream = bool(req_json.get("stream"))
                # 自动补 include_usage：流式响应只有开启它才携带 usage，否则无法统计 token
                if is_stream and not (req_json.get("stream_options") or {}).get("include_usage"):
                    req_json.setdefault("stream_options", {})["include_usage"] = True
                    body = json.dumps(req_json, ensure_ascii=False).encode("utf-8")

        # 3.6) 按 API Key 统计：抓 Authorization / X-API-Key 头，只存指纹不存明文
        #      （仅本地代理流量带 Key 指纹；CC/YQ/CodeBuddy/WorkBuddy 等外部导入无 Key）
        api_key_hash = api_key_hint = None
        if is_chat:
            auth = self.headers.get("Authorization") or self.headers.get("X-API-Key") or ""
            auth = auth.strip()
            if auth.lower().startswith("bearer "):
                auth = auth[7:].strip()
            api_key_hash, api_key_hint = storage.fingerprint_key(auth)

        # 4) 转发到上游（超时放宽到 600 秒，兼容长推理场景）
        conn = HTTPSConnection(host, port, timeout=600)
        try:
            conn.request(self.command, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            status = resp.status
            reason = resp.reason

            # 5) 收集响应头
            resp_headers = []
            for key, value in resp.getheaders():
                if key.lower() not in _SKIP_RESPONSE_HEADERS:
                    resp_headers.append((key, value))

            raw = b""
            if is_stream:
                # 流式(SSE)：逐块实时转发给客户端，同时缓存全文用于最后解析 usage
                self.send_response_only(status, reason)
                for key, value in resp_headers:
                    self.send_header(key, value)
                self.end_headers()
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    raw += chunk
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                # 非流式：一次读完整响应
                raw = resp.read()

            # 6) 聊天接口：解析 usage 并入库计费
            if is_chat:
                model, usage = self._extract(raw, is_stream)
                if model and usage is not None:
                    price = pricing.get_price(model, config, datetime.now())
                    cost = pricing.calc_cost(usage, price)
                    hit, miss, completion = pricing.calc_usage(usage)
                    storage.add_request(datetime.now(), model, hit, miss, completion, cost,
                                        api_key_hash, api_key_hint)

            # 7) 非流式：统一回写响应
            if not is_stream:
                self.send_response_only(status, reason)
                for key, value in resp_headers:
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                self.wfile.flush()
        finally:
            conn.close()

    @staticmethod
    def _extract(raw: bytes, is_stream: bool):
        """从响应中提取 (model, usage)。"""
        if is_stream:
            return _extract_usage_from_sse(raw)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, None
        return obj.get("model"), obj.get("usage")


def _extract_usage_from_sse(raw: bytes):
    """从 SSE 流文本中提取最后一个非空 usage 与 model。"""
    text = raw.decode("utf-8", errors="replace")
    model = None
    usage = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        if obj.get("model"):
            model = obj["model"]
        if obj.get("usage"):
            usage = obj["usage"]
    return model, usage


def start_proxy(config: dict, state: dict):
    """后台线程入口：启动代理服务器；失败原因写入 state['proxy_error']。"""
    host = config.get("proxy_host", "127.0.0.1")
    port = int(config.get("proxy_port", 8787))
    if not config.get("proxy_enabled", True):
        state["proxy_ready"] = False  # 设置页已关闭代理，不监听端口
        return
    try:
        server = ThreadingHTTPServer((host, port), _ProxyHandler)
    except OSError as exc:
        state["proxy_error"] = f"端口 {port} 被占用或无法监听: {exc}"
        state["proxy_ready"] = False
        return
    server.config = config  # 注入配置供处理逻辑使用
    state["proxy_server"] = server  # 保存引用，供设置页动态停止（server.shutdown）
    state["proxy_ready"] = True
    server.serve_forever()
    state["proxy_ready"] = False  # 被 shutdown() 停止后标记为已关闭