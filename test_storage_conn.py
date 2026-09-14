# -*- coding: utf-8 -*-
"""test_storage_conn.py — 连接复用与热路径性能回归测试。

背景：原来每次 storage 操作都新建 SQLite 连接，_tick 每 1.5 秒要跑 3~6 次
查询。改为 thread-local 长连接后必须保证：
  1. 功能不变（既有 finally: conn.close() 不会真的关掉长连接）
  2. 连接真的被复用（新建次数显著下降）
  3. 跨线程各自独立（不共享连接对象）
  4. close_thread_conn() 能真正关闭
"""
import os
import shutil

import utf8_output  # noqa: F401  # 中文输出兼容（CI 为英文 Windows）
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import storage


def _tmpdir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", "conn_t")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def _count_connects(fn):
    """统计 fn 执行期间 sqlite3.connect 的调用次数。"""
    import sqlite3
    orig = sqlite3.connect
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    sqlite3.connect = counting
    try:
        fn()
    finally:
        sqlite3.connect = orig
    return calls["n"]


def test_functional_unchanged():
    """复用连接后写入/读取仍然正确。"""
    storage.init_db(_tmpdir())
    from datetime import datetime
    for i in range(5):
        storage.add_request(datetime.now(), "deepseek-v4-flash", 10, 20, 30, 0.01)
    s = storage.today_stats()
    assert s["requests"] == 5, "写入丢失: %s" % s
    assert s["cache_hit"] == 50 and s["cache_miss"] == 100 and s["completion"] == 150
    print("PASS 1 功能不变（写入 5 条，读回一致）")


def test_conn_is_reused():
    """稳态下重复查询不应再新建连接。"""
    storage.init_db(_tmpdir())
    from datetime import datetime
    storage.add_request(datetime.now(), "m", 1, 1, 1, 0.1)
    storage.today_stats()          # 预热：建好线程本地连接

    def hammer():
        for _ in range(20):
            storage.today_stats()

    n = _count_connects(hammer)
    assert n == 0, "20 次查询仍新建了 %d 个连接（复用失效）" % n
    print("PASS 2 连接复用生效（20 次查询新建连接数 = 0）")


def test_close_is_noop_then_real_close():
    """finally: conn.close() 不应关掉长连接；close_thread_conn 才真正关闭。"""
    storage.init_db(_tmpdir())
    storage.today_stats()
    raw = storage._local.conn
    assert raw is not None

    storage.today_stats()
    assert storage._local.conn is raw, "close() 把长连接关掉了（复用失效）"

    storage.close_thread_conn()
    assert getattr(storage._local, "conn", None) is None, "close_thread_conn 未清理"
    # 关闭后仍能自动重连
    storage.today_stats()
    assert getattr(storage._local, "conn", None) is not None, "关闭后未能自动重连"
    print("PASS 3 close() 为 no-op，close_thread_conn 真正关闭并可自动重连")


def test_thread_isolation():
    """不同线程必须各自持有独立连接。"""
    storage.init_db(_tmpdir())
    storage.today_stats()
    main_conn = storage._local.conn
    captured = {}

    def worker():
        storage.today_stats()
        captured["conn"] = storage._local.conn

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert captured.get("conn") is not None, "子线程未建立连接"
    assert captured["conn"] is not main_conn, "两个线程共用了同一个连接对象"
    print("PASS 4 线程间连接互相独立")


def test_tick_hotpath_is_fast():
    """热路径性能：模拟 _tick 一轮的查询，应在合理时间内完成。"""
    storage.init_db(_tmpdir())
    from datetime import datetime
    for i in range(200):
        storage.add_request(datetime.now(), "deepseek-v4-flash", 10, 20, 30, 0.001, "k%d" % i)

    def one_tick():
        storage.today_stats()
        storage.max_request_id()
        storage.new_requests_since(0, seconds=8)

    one_tick()  # 预热
    start = time.time()
    for _ in range(50):
        one_tick()
    elapsed = time.time() - start
    per = elapsed / 50 * 1000
    assert per < 50, "单轮耗时 %.1f ms 过慢（应 < 50ms）" % per
    print("PASS 5 热路径 50 轮平均耗时 %.2f ms/轮" % per)


if __name__ == "__main__":
    test_functional_unchanged()
    test_conn_is_reused()
    test_close_is_noop_then_real_close()
    test_thread_isolation()
    test_tick_hotpath_is_fast()
    print("\nALL STORAGE-CONN TESTS PASSED")
