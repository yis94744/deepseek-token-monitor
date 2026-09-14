# -*- coding: utf-8 -*-
"""测试/脚本的中文输出兼容层。

背景（CI 实测）：GitHub Actions 的 windows-latest 默认代码页是 cp1252
（英文 Windows）。测试脚本里的中文 print 会直接抛
    UnicodeEncodeError: 'charmap' codec can't encode characters ...
导致 9 个测试套件在 CI 上"全军覆没"，而本地（中文 Windows，cp936）完全正常。

解决：在任何输出之前把 stdout/stderr 切到 UTF-8。所有测试文件导入本模块
即可获得可移植的中文输出。

用法（放在测试文件最顶部，其他 import 之前）：
    import utf8_output  # noqa: F401
"""
import sys


def enable():
    """把 stdout/stderr 切到 UTF-8（errors=replace 兜底），可重复调用。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


enable()
