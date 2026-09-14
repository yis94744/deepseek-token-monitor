# -*- coding: utf-8 -*-
"""test_logutil.py — 日志轮转/去重/孤儿清理的回归测试。

覆盖 2026-09-14 实测问题的三个修复点：
  1. 超过上限必须轮转（此前无任何轮转，单文件涨到 74 MB）
  2. 重复内容不落盘（此前每 2 秒无条件写一行）
  3. 孤儿日志清理（已下线模块遗留的 kun/yq 日志）
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logutil


def _tmpdir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", "logutil_t")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def test_rotation():
    """写入量超过上限时，必须产生 .1 备份且当前文件被截断。"""
    d = _tmpdir()
    lg = logutil.SyncLogger(d, "t.log", max_bytes=2048, dedupe=False)
    for i in range(200):          # 每行约 40 字节，远超 2048
        lg.write("line %03d padding padding padding padding" % i)
    cur = os.path.join(d, "t.log")
    bak = cur + ".1"
    assert os.path.isfile(bak), "未生成 .1 备份 -> 轮转失效"
    assert os.path.getsize(cur) <= 4096, "轮转后当前文件仍超限: %s" % os.path.getsize(cur)
    print("PASS 1 超限轮转（.1 已生成，当前文件 %s 字节）" % os.path.getsize(cur))


def test_rotation_keeps_single_backup():
    """多轮轮转后只保留 1 份备份，不会无限堆积。"""
    d = _tmpdir()
    lg = logutil.SyncLogger(d, "t.log", max_bytes=1024, dedupe=False, keep_backups=1)
    for i in range(500):
        lg.write("x" * 80 + str(i))
    files = sorted(f for f in os.listdir(d) if f.startswith("t.log"))
    assert files == ["t.log", "t.log.1"], "备份文件数量异常: %s" % files
    print("PASS 2 只保留 1 份备份: %s" % files)


def test_dedupe():
    """相同内容连续写入只落一行；force=True 强制写入。"""
    d = _tmpdir()
    lg = logutil.SyncLogger(d, "t.log", max_bytes=10 ** 6)
    for _ in range(50):
        lg.write("本轮新增=0 累计=5")
    lines = open(os.path.join(d, "t.log"), encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 1, "去重失效，写入了 %d 行" % len(lines)

    lg.write("本轮新增=1 累计=6")
    lg.write("本轮新增=0 累计=6")
    lines = open(os.path.join(d, "t.log"), encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 3, "状态变化应各写一行，实际 %d 行" % len(lines)

    lg.write("boom", force=True)
    lg.write("boom", force=True)
    lines = open(os.path.join(d, "t.log"), encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 5, "force 应逐条写入，实际 %d 行" % len(lines)
    print("PASS 3 内容去重（50 次重复 -> 1 行）与 force 强制写入")


def test_orphan_cleanup():
    """孤儿日志必须被删除，无关文件不受影响。"""
    d = _tmpdir()
    for name in ("kun_sync.log", "yq_sync.log", "yq_sync.log.1", "cc_sync.log", "keep.txt"):
        open(os.path.join(d, name), "w", encoding="utf-8").write("x")
    removed = logutil.cleanup_orphan_logs(d)
    assert removed == 3, "应删除 3 个孤儿，实际 %d" % removed
    assert not os.path.exists(os.path.join(d, "kun_sync.log"))
    assert not os.path.exists(os.path.join(d, "yq_sync.log"))
    assert os.path.exists(os.path.join(d, "cc_sync.log")), "误删了在用日志"
    assert os.path.exists(os.path.join(d, "keep.txt")), "误删了无关文件"
    print("PASS 4 孤儿日志清理（删除 3 个，保留在用日志）")


def test_no_data_dir_is_safe():
    """data_dir 为空时不应抛异常（未初始化场景）。"""
    lg = logutil.SyncLogger("", "t.log")
    lg.write("anything")           # 不应抛错
    lg.error("boom")
    assert logutil.cleanup_orphan_logs("") == 0
    print("PASS 5 未初始化时安全空转")


if __name__ == "__main__":
    test_rotation()
    test_rotation_keeps_single_backup()
    test_dedupe()
    test_orphan_cleanup()
    test_no_data_dir_is_safe()
    print("\nALL LOGUTIL TESTS PASSED")
