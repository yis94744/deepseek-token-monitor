# -*- coding: utf-8 -*-
"""自动更新检测模块。

通过 GitHub Releases API 查询最新版本
（https://api.github.com/repos/yis94744/deepseek-token-monitor/releases/latest），
与当前版本比较，发现新版本时由主程序弹出提示。网络失败静默跳过，
不影响正常使用。检查结果写入 data 目录的 updater.log（仅排查用）。
"""
import json
import os
import re
import urllib.request
from datetime import datetime

REPO = "yis94744/deepseek-token-monitor"
API_URL = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASE_URL = "https://github.com/%s/releases/tag/%%s" % REPO
_TIMEOUT = 8.0
_LOG = None  # 日志文件路径，由 init_log() 初始化


def init_log(data_dir: str):
    """设置日志路径（data 目录），不传则关闭日志。"""
    global _LOG
    _LOG = os.path.join(data_dir, "updater.log") if data_dir else None


def _log(text: str):
    if not _LOG:
        return
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
    except Exception:
        pass


def parse_version(text) -> tuple:
    """把 'v1.4.5' / '1.4.5' 解析为 (1, 4, 5)，解析失败返回 None。"""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def is_newer(latest_tag: str, current: tuple) -> bool:
    """latest_tag 是否比 current (元组) 新。"""
    v = parse_version(latest_tag)
    return v is not None and v > current


def check_latest() -> dict:
    """查询 GitHub 最新 release。

    返回 {"tag", "url", "name", "published"}；网络/接口失败返回 None（静默）。
    """
    try:
        req = urllib.request.Request(
            API_URL,
            headers={"User-Agent": "DeepSeekTokenMonitor",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name") or ""
        return {"tag": tag,
                "url": data.get("html_url") or (RELEASE_URL % tag),
                "name": data.get("name") or "",
                "published": data.get("published_at") or ""}
    except Exception as exc:
        _log("检查失败(静默): %s" % exc)
        return None
