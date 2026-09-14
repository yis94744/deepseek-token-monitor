# -*- coding: utf-8 -*-
"""CloudRank 服务启动器（生产版 v2）

相对旧版 start_server.py 的修复：
  1. 【核心】强制 SelectorEventLoop：uvicorn 0.52 在 Windows 上硬编码
     ProactorEventLoop（IOCP），长跑后 IocpProactor.accept 会抛
     OSError[WinError 64] 导致监听套接字静默失效（2026-09-09 就是这么挂的）。
     这里覆盖 Config.get_loop_factory 改用 selector 事件循环。
  2. 【日志】改用 RotatingFileHandler（10MB x 5），不再无限增长。
  3. 【心跳】每 5 分钟写一行心跳，便于区分"僵活"与"真死"。
  4. 【自愈】进程内崩溃自动重启；单实例保护（端口占用则退出，交给计划任务）。
  5. 【干净】日志不再混入 schtasks 输出。

用法：python start_server.py            （前台守护，崩溃自动重启）
      python start_server.py --once     （只跑一次，不自动重启，调试用）
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
HEARTBEAT_SECONDS = 300
RESTART_DELAY_SECONDS = 10
HOST = "0.0.0.0"
PORT = int(os.environ.get("CLOUDRANK_PORT", "80"))


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
    # 同时打到控制台（前台运行时可见）
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logger.addHandler(console)
    return logger


def port_in_use(host: str, port: int) -> bool:
    """检测端口是否已被占用（用于单实例保护）。"""
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
    """每 5 分钟写一行心跳，便于外部/人工判断进程是否还健康。"""
    while not stop_event.wait(HEARTBEAT_SECONDS):
        alive = not port_in_use(HOST, PORT)
        logger.info("heartbeat: listening=%s pid=%s", (not alive), os.getpid())


def serve_once(logger):
    """跑一轮 uvicorn（阻塞直到退出）。返回 True 表示正常结束。"""
    install_selector_loop()
    from uvicorn.config import Config
    from uvicorn.server import Server

    # uvicorn 0.52 的 Config 不再接受 app_dir，改为自己把应用目录放进 sys.path
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    os.chdir(APP_DIR)

    cfg = Config("app:app", host=HOST, port=PORT,
                 log_level="info", access_log=True)
    server = Server(cfg)

    stop_event = threading.Event()
    hb = threading.Thread(target=heartbeat, args=(logger, stop_event), daemon=True)
    hb.start()
    logger.info("starting uvicorn on %s:%s (loop=%s, app_dir=%s)",
                HOST, PORT, cfg.get_loop_factory(), APP_DIR)
    try:
        server.run()
        return True
    finally:
        stop_event.set()


def main():
    logger = setup_logging()
    once = "--once" in sys.argv

    if port_in_use(HOST, PORT):
        logger.warning("port %s already in use -> another instance is running, exit", PORT)
        return 0

    logger.info("=== CloudRank launcher start (pid=%s, python=%s) ===",
                os.getpid(), sys.version.split()[0])
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
