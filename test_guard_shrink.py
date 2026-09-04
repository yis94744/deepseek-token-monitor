# -*- coding: utf-8 -*-
"""防缩保护 guard 单测：切页后窗口被意外缩小，guard 应自动还原（独立脚本）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_monitor

app = token_monitor.App()
try:
    app.root.update()
    root = app.root
    nb = app.nb

    def size():
        return (root.winfo_width(), root.winfo_height())

    # 干净 normal 状态：拉大到 1400x900
    root.state("normal")
    root.geometry("1400x900")
    app.root.update_idletasks()
    app.root.update()
    time.sleep(0.4)
    print("clean normal:", size())
    assert size() == (1400, 900), size()

    # 场景 A：切页后窗口被意外缩小 40px+ → guard 还原
    nb.select(1)          # 触发 _on_tab_changed，记录 before=1400x900
    app.root.update()
    time.sleep(0.2)
    root.geometry("860x540")   # 模拟切页引发的意外收缩
    app.root.update()
    print("A fake shrink:", size())
    time.sleep(0.6)            # 等 after(150) guard
    app.root.update()
    restored = size()
    print("A after guard:", restored)
    assert restored == (1400, 900), f"guard A failed: {restored}"

    # 场景 B：切页后窗口只是小幅度变化（用户拖动场景）→ 不还原
    nb.select(2)
    app.root.update()
    time.sleep(0.2)
    root.geometry("1380x880")  # 只小 20px
    app.root.update()
    time.sleep(0.6)
    app.root.update()
    sB = size()
    print("B after small change:", sB)
    assert sB == (1380, 880), f"guard B 不应还原小幅变化: {sB}"

    # 场景 C：窗口没变 → guard 无操作
    nb.select(3)
    app.root.update()
    time.sleep(0.6)
    app.root.update()
    sC = size()
    print("C unchanged:", sC)
    assert sC == (1380, 880)

    print("\nALL GUARD TESTS PASSED")
except Exception:
    import traceback
    traceback.print_exc()
finally:
    try:
        app.quit()
    except Exception:
        pass
    try:
        app.root.destroy()
    except Exception:
        pass
    sys.stdout.flush()
    os._exit(0)
