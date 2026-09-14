# -*- coding: utf-8 -*-
"""托盘「检查更新」菜单动作测试：已是最新/发现新版/检查失败 三分支。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_monitor
import updater as updater_mod

app = token_monitor.App()
called = {"info": [], "error": [], "notify": []}
try:
    token_monitor.messagebox.showinfo = lambda *a, **k: called["info"].append(a)
    token_monitor.messagebox.showerror = lambda *a, **k: called["error"].append(a)
    orig_notify = token_monitor.App._notify_update
    token_monitor.App._notify_update = lambda self, info: called["notify"].append(info)

    def pump(sec):
        """真实 mainloop 驱动（跨线程 root.after 只在主线程 mainloop 时生效）。"""
        app.root.after(int(sec * 1000), app.root.quit)
        app.root.mainloop()

    pump(2)  # 等启动首轮真实更新检查结束，避免干扰

    # ---- 分支 1：已是最新 → showinfo ----
    updater_mod.check_latest = lambda: {"tag": "v1.13.3", "url": "u", "setup_url": "s", "name": "n"}
    app._tray_check_update()
    pump(4)
    assert called["info"] and "已是最新版本" in called["info"][0][1], called
    assert not called["error"] and not called["notify"]
    print("PASS 1 已是最新 → 弹窗提示", file=sys.stderr)

    # ---- 分支 2：发现新版 → 走更新询问（_notify_update），不重复弹提示 ----
    for k in called:
        called[k].clear()
    updater_mod.check_latest = lambda: {"tag": "v9.9.9", "url": "u", "setup_url": "s", "name": "n"}
    updater_mod.is_newer = lambda a, b: True
    app._tray_check_update()
    pump(4)
    assert len(called["notify"]) == 1 and not called["info"], called
    assert not called["error"]
    print("PASS 2 发现新版 → 走更新询问（无重复提示）", file=sys.stderr)

    # ---- 分支 3：检查失败 → showerror ----
    for k in called:
        called[k].clear()
    def boom():
        raise RuntimeError("接口无响应")
    updater_mod.check_latest = boom
    app._tray_check_update()
    pump(4)
    assert called["error"] and not called["info"] and not called["notify"], called
    print("PASS 3 检查失败 → 弹错误提示", file=sys.stderr)

    print("\nALL TRAY UPDATE TESTS PASSED", file=sys.stderr)
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
