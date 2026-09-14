# -*- coding: utf-8 -*-
"""test_guard.py — 单实例互斥与崩溃兜底的回归测试。"""
import os
import shutil

import utf8_output  # noqa: F401  # 中文输出兼容（CI 为英文 Windows）
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import guard


def _tmpdir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", "guard_t")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def test_trail():
    """操作轨迹应在崩溃报告里可见，且数量受上限约束。"""
    guard._trail[:] = []
    for i in range(60):
        guard.note("action %d" % i)
    assert len(guard._trail) <= 25, "轨迹未受上限约束: %d" % len(guard._trail)
    guard._trail[:] = []
    guard.note("打开排名页")
    txt = guard._trail_text()
    assert "打开排名页" in txt, "轨迹内容丢失"
    print("PASS 1 操作轨迹记录与上限约束")


def test_crash_log_written():
    """未捕获异常必须写进 crash.log，且含版本号与堆栈。"""
    d = _tmpdir()
    guard.install_exception_hooks(d, "v9.9.9-test")
    guard.note("测试轨迹")
    try:
        raise ValueError("模拟崩溃")
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        guard._handle_uncaught(exc_type, exc_value, exc_tb)
    path = os.path.join(d, "crash.log")
    assert os.path.isfile(path), "crash.log 未生成"
    content = open(path, encoding="utf-8").read()
    assert "v9.9.9-test" in content, "缺少版本号"
    assert "模拟崩溃" in content, "缺少异常信息"
    assert "ValueError" in content, "缺少异常类型"
    assert "测试轨迹" in content, "缺少操作轨迹"
    print("PASS 2 崩溃写入 crash.log（含版本/异常/轨迹）")


def test_thread_exception_captured():
    """后台线程异常必须被捕获并记录（原代码完全无留痕）。"""
    d = _tmpdir()
    guard.install_exception_hooks(d, "v9.9.9-test")

    class FakeThread:
        name = "fake-worker"

    class Args:
        thread = FakeThread()
        exc_type = RuntimeError
        exc_value = RuntimeError("后台线程崩了")
        exc_traceback = None

    guard._handle_thread_exception(Args())
    content = open(os.path.join(d, "crash.log"), encoding="utf-8").read()
    assert "后台线程崩了" in content, "线程异常未记录"
    assert "fake-worker" in content, "未记录线程名"
    print("PASS 3 后台线程异常被捕获留痕")


def test_no_data_dir_safe():
    """data_dir 为空时不应抛异常。"""
    guard.install_exception_hooks("", "v0")
    guard._handle_thread_exception(type("A", (), {
        "thread": type("T", (), {"name": "t"})(),
        "exc_type": ValueError, "exc_value": ValueError("x"), "exc_traceback": None})())
    print("PASS 4 无 data_dir 时安全")


def test_single_instance_semantics():
    """单实例：同进程内第二次 acquire 应判定为"已存在"。"""
    first = guard.acquire_single_instance()
    second = guard.acquire_single_instance()
    assert first is True, "首次获取应成功"
    if sys.platform == "win32":
        assert second is False, "Windows 上第二次获取应返回 False（已有实例）"
        print("PASS 5 单实例互斥（第二次被正确拒绝）")
    else:
        print("PASS 5 跳过（非 Windows）")


if __name__ == "__main__":
    test_trail()
    test_crash_log_written()
    test_thread_exception_captured()
    test_no_data_dir_safe()
    test_single_instance_semantics()
    print("\nALL GUARD TESTS PASSED")
