# -*- coding: utf-8 -*-
"""CloudRank 服务启动器（生产版 v3 —— HTTPS 支持）

相对旧版的修复与增强：
  1. 【核心】强制 SelectorEventLoop：uvicorn 0.52 在 Windows 上硬编码
     ProactorEventLoop（IOCP），长跑后 IocpProactor.accept 会抛
     OSError[WinError 64] 导致监听套接字静默失效（2026-09-09 就是这么挂的）。
     这里覆盖 Config.get_loop_factory 改用 selector 事件循环。
  2. 【HTTPS】同时监听 HTTP(80) 与 HTTPS(443)：
     - 80 端口保留，保证旧版客户端（写死 http://）不会失联；
     - 443 端口用 certs/server.crt + server.key 提供 TLS；
     - 客户端切到 https:// 后可关掉 80（设 CLOUDRANK_HTTP_PORT=0）。
  3. 【日志】RotatingFileHandler（10MB x 5），不再无限增长。
  4. 【心跳】每 5 分钟写一行心跳，便于区分"僵活"与"真死"。
  5. 【自愈】进程内崩溃自动重启；单实例保护（端口占用则退出，交给计划任务）。

用法：python start_server.py            （前台守护，崩溃自动重启）
      python start_server.py --once     （只跑一次，不自动重启，调试用）

环境变量：
  CLOUDRANK_HTTP_PORT    HTTP 端口（默认 80；设为 0 则关闭 HTTP）
  CLOUDRANK_HTTPS_PORT   HTTPS 端口（默认 443；设为 0 则关闭 HTTPS）
"""
import asyncio
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, "server.log")
CERT_DIR = os.path.join(APP_DIR, "certs")
HEARTBEAT_SECONDS = 300
RESTART_DELAY_SECONDS = 10

HOST = "0.0.0.0"
HTTP_PORT = int(os.environ.get("CLOUDRANK_HTTP_PORT", "80"))
HTTPS_PORT = int(os.environ.get("CLOUDRANK_HTTPS_PORT", "443"))

CERT_FILE = os.path.join(CERT_DIR, "server.crt")
KEY_FILE = os.path.join(CERT_DIR, "server.key")


def setup_logging():
    """轮转日志：单文件 10MB，保留 5 份。"""
    logger = logging.getLogger("cloudrank")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(LOG_PATH, maxBytes=10 * 1024 * 1024,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logger.addHandler(console)
    return logger


def port_in_use(host: str, port: int) -> bool:
    """检测端口是否已被占用（用于单实例保护）。"""
    if port <= 0:
        return False
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        return s.connect_ex((probe_host, port)) == 0
    finally:
        s.close()


def install_selector_loop():
    """把 uvicorn 的事件循环工厂换成 SelectorEventLoop（规避 Windows Proactor 崩溃）。"""
    import uvicorn
    from uvicorn.config import Config

    def _selector_factory(_self):
        if sys.platform == "win32":
            return asyncio.SelectorEventLoop
        return None  # 非 Windows 走默认

    Config.get_loop_factory = _selector_factory
    return uvicorn


def heartbeat(logger, stop_event):
    """每 5 分钟写一行心跳（含两个端口的监听状态）。"""
    while not stop_event.wait(HEARTBEAT_SECONDS):
        http_up = HTTP_PORT > 0 and not port_in_use(HOST, HTTP_PORT)
        https_up = HTTPS_PORT > 0 and not port_in_use(HOST, HTTPS_PORT)
        logger.info("heartbeat: http=%s https=%s pid=%s", http_up, https_up, os.getpid())


def _run_server(logger, port, ssl_certfile=None, ssl_keyfile=None):
    """在【当前线程】跑一个 uvicorn 实例（阻塞）。"""
    from uvicorn.config import Config
    from uvicorn.server import Server

    kwargs = dict(host=HOST, port=port, log_level="info", access_log=True)
    if ssl_certfile:
        kwargs["ssl_certfile"] = ssl_certfile
        kwargs["ssl_keyfile"] = ssl_keyfile
    cfg = Config("app:app", **kwargs)
    server = Server(cfg)
    scheme = "https" if ssl_certfile else "http"
    logger.info("starting uvicorn on %s://%s:%s", scheme, HOST, port)
    server.run()


def serve_once(logger):
    """跑一轮：HTTPS 在主线程，HTTP 在守护线程。两者都退出才返回。"""
    install_selector_loop()
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    os.chdir(APP_DIR)

    stop_event = threading.Event()
    threading.Thread(target=heartbeat, args=(logger, stop_event), daemon=True).start()

    # 启动 HTTP 线程（若启用）
    http_thread = None
    if HTTP_PORT > 0:
        http_thread = threading.Thread(
            target=_run_server, args=(logger, HTTP_PORT), daemon=True,
            name="uvicorn-http")
        http_thread.start()

    # HTTPS 跑在主线程（若启用）；否则主线程跑 HTTP
    try:
        if HTTPS_PORT > 0:
            if not (os.path.isfile(CERT_FILE) and os.path.isfile(KEY_FILE)):
                logger.error("HTTPS 已启用但缺少证书：%s / %s", CERT_FILE, KEY_FILE)
                logger.error("请先运行 tools/gen_certs.py 生成证书，或设 CLOUDRANK_HTTPS_PORT=0")
                if HTTP_PORT <= 0:
                    raise RuntimeError("无可用监听端口")
            else:
                _run_server(logger, HTTPS_PORT, CERT_FILE, KEY_FILE)
                return True
        if HTTP_PORT <= 0:
            raise RuntimeError("HTTP 与 HTTPS 都被关闭，无端口可监听")
        # 只跑 HTTP 的情况
        if http_thread is not None:
            http_thread.join()
            return True
        _run_server(logger, HTTP_PORT)
        return True
    finally:
        stop_event.set()


def main():
    logger = setup_logging()
    once = "--once" in sys.argv

    if HTTP_PORT <= 0 and HTTPS_PORT <= 0:
        logger.error("HTTP 与 HTTPS 端口都被关闭，退出")
        return 2

    # 单实例保护：任一启用端口被占用即认为已有实例
    for port in (HTTP_PORT, HTTPS_PORT):
        if port > 0 and port_in_use(HOST, port):
            logger.warning("port %s already in use -> another instance is running, exit", port)
            return 0

    logger.info("=== CloudRank launcher start (pid=%s, python=%s, http=%s, https=%s) ===",
                os.getpid(), sys.version.split()[0], HTTP_PORT, HTTPS_PORT)
    attempt = 0
    while True:
        attempt += 1
        try:
            serve_once(logger)
            logger.info("uvicorn exited normally (attempt %s)", attempt)
            if once:
                return 0
        except KeyboardInterrupt:
            logger.info("interrupted by user, exit")
            return 0
        except Exception as exc:
            logger.exception("uvicorn crashed (attempt %s): %s", attempt, exc)
            if once:
                return 1
        if once:
            return 0
        logger.info("restarting in %s seconds...", RESTART_DELAY_SECONDS)
        time.sleep(RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
