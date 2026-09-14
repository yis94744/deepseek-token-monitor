# -*- coding: utf-8 -*-
r"""统一日志工具：单文件轮转 + 状态去重 + 孤儿清理。

背景（2026-09-14 实测）：各 sync 模块的 _log() 每 2~10 秒无条件追加一行，
且没有任何轮转，导致 %APPDATA%\DeepSeekTokenMonitor\data\ 膨胀到 145 MB
（cc_sync.log 单个 74.83 MB）。本模块统一解决：

  1. **按大小轮转**：单文件超过 MAX_BYTES（默认 5 MB）时改名为 .1 并重开，
     只保留 1 份历史，所以任一模块的日志总量上限约 10 MB。
  2. **状态去重**：同一模块连续写入内容相同的行（例如每 2 秒一次的
     "本轮新增=0 累计=123"）只在状态变化时落盘，其余情况静默丢弃，
     把写入量从"每 2 秒一行"降到"有变化才写"。
  3. **孤儿清理**：启动时删掉已下线模块遗留的日志（kun/yq 等）。

用法：
    from logutil import SyncLogger
    logger = SyncLogger(data_dir, "cc_sync.log")   # 文件名以 .log 结尾
    logger.write("本轮新增=0 累计=5")               # 重复内容自动跳过
    logger.write("ERROR: ...", force=True)         # 错误总是写
"""
import os
import threading
from datetime import datetime

MAX_BYTES = 5 * 1024 * 1024   # 单文件上限 5 MB
KEEP_BACKUPS = 1              # 保留 1 份 .1 历史
MAX_AGE_DAYS = 30             # .1 历史超过 30 天自动删除

# 已下线模块遗留的日志文件名（v1.11.0 移除 Kun、YQ 数据源后遗留）
ORPHAN_LOGS = ("kun_sync.log", "yq_sync.log")


def cleanup_orphan_logs(data_dir: str):
    """删除已下线模块的孤儿日志文件。返回删除数量。"""
    removed = 0
    if not data_dir or not os.path.isdir(data_dir):
        return 0
    for name in ORPHAN_LOGS:
        for suffix in ("", ".1"):
            path = os.path.join(data_dir, name + suffix)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
            except Exception:
                pass
    return removed


class SyncLogger:
    """轻量级轮转日志器：线程安全、按大小轮转、重复内容去重。"""

    def __init__(self, data_dir: str, filename: str, max_bytes: int = MAX_BYTES,
                 dedupe: bool = True, keep_backups: int = KEEP_BACKUPS):
        self.path = os.path.join(data_dir, filename) if data_dir else None
        self.max_bytes = max_bytes
        self.keep_backups = keep_backups
        self.dedupe = dedupe
        self._last_text = None
        self._lock = threading.Lock()
        if self.path:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
            except Exception:
                self.path = None

    # ---------- 内部 ----------
    def _rotate_if_needed(self):
        """超过上限则轮转：删掉最旧备份，把当前文件改名 .1。"""
        try:
            if not os.path.isfile(self.path):
                return
            if os.path.getsize(self.path) <= self.max_bytes:
                return
            oldest = "%s.%d" % (self.path, self.keep_backups)
            if os.path.isfile(oldest):
                os.remove(oldest)
            for i in range(self.keep_backups - 1, 0, -1):
                src = "%s.%d" % (self.path, i)
                if os.path.isfile(src):
                    os.replace(src, "%s.%d" % (self.path, i + 1))
            os.replace(self.path, "%s.1" % self.path)
        except Exception:
            pass

    def _prune_old_backups(self):
        """删除过期的轮转备份（避免 .1 长期堆积）。"""
        try:
            backup = "%s.1" % self.path
            if not os.path.isfile(backup):
                return
            age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(backup))).days
            if age > MAX_AGE_DAYS:
                os.remove(backup)
        except Exception:
            pass

    # ---------- 对外 ----------
    def write(self, text: str, force: bool = False):
        """写一行日志。dedupe=True 且内容与上次相同（且非 force）时跳过。"""
        if not self.path:
            return
        with self._lock:
            if self.dedupe and not force and text == self._last_text:
                return
            self._last_text = text
            try:
                self._rotate_if_needed()
                self._prune_old_backups()
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
            except Exception:
                pass

    def error(self, text: str):
        """错误总是写入（不参与去重），且带上 ERROR 前缀便于检索。"""
        self.write("ERROR: " + str(text), force=True)

    def reset_dedupe(self):
        """状态重置时调用，让下一行相同内容仍能落盘。"""
        with self._lock:
            self._last_text = None
