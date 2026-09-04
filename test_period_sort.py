# -*- coding: utf-8 -*-
"""时段统计列头排序测试：与每日统计同款共享组件。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_monitor
import storage

app = token_monitor.App()


def find_treeviews(w, acc):
    try:
        if w.winfo_class() == "Treeview":
            acc.append(w)
    except Exception:
        pass
    try:
        for ch in w.winfo_children():
            find_treeviews(ch, acc)
    except Exception:
        pass
    return acc


try:
    app.root.update()
    nb = app.nb
    idx = next(i for i in range(len(nb.tabs())) if nb.tab(i, "text") == "时段统计")
    nb.select(idx)
    app.root.update_idletasks()
    app.root.update()
    time.sleep(0.6)

    tree = None
    for t in find_treeviews(app.root, []):
        cols = list(t["columns"])
        if len(cols) == 7 and "pr" in cols and "oc" in cols:
            tree = t
            break
    assert tree, "未找到时段统计表格"
    print("found period tree")

    def click_header(col):
        cmd = tree.heading(col, "command")
        if isinstance(cmd, str):
            tree.tk.call(cmd)
        elif callable(cmd):
            cmd()

    def first_dates():
        kids = tree.get_children()
        return [tree.item(kids[i], "values")[0] for i in range(min(3, len(kids) - 1))]

    # 参考数据：App 真实 config 的 period_stats
    ref = storage.period_stats(config=app.config)

    # PASS 1 初始按日期倒序
    assert first_dates()[0] == ref[0]["date"], first_dates()[0]
    print("PASS 1 初始按日期倒序")

    # PASS 2 点「高峰费用 pc」→ 降序：首行 peak_cost 最大日
    click_header("pc")
    mx = max(r["peak_cost"] for r in ref)
    expect = next(r["date"] for r in ref if r["peak_cost"] == mx)
    vals, last = first_dates()[0], None
    kids = tree.get_children()
    last_v = tree.item(kids[-1], "values")
    assert vals == expect, (vals, expect)
    assert last_v[0] == "合计" and tree.item(kids[-1], "tags") == ("total",)
    print(f"PASS 2 点「高峰费用」降序：首行={vals}（peak_cost 最大 {mx:.4f}），合计沉底")

    # PASS 3 再点 pc → 升序（0 也参与排序，无高峰调用的日排最前）
    click_header("pc")
    mn = min(r["peak_cost"] for r in ref)
    expect = next(r["date"] for r in ref if r["peak_cost"] == mn)
    assert first_dates()[0] == expect, (first_dates()[0], expect)
    print(f"PASS 3 再点「高峰费用」升序：首行={expect}（peak_cost={mn}）")

    # PASS 4 点「非高峰费用 oc」→ 降序
    click_header("oc")
    mxo = max(r["off_cost"] for r in ref)
    expect = next(r["date"] for r in ref if r["off_cost"] == mxo)
    assert first_dates()[0] == expect, (first_dates()[0], expect)
    print(f"PASS 4 点「非高峰费用」降序：首行={expect}")

    # PASS 5 点「日期」→ 升序最早
    click_header("date")
    oldest = min(r["date"] for r in ref)
    assert first_dates()[0] == oldest, (first_dates()[0], oldest)
    print(f"PASS 5 点「日期」升序：首行={oldest}")

    # PASS 6 点「高峰Token pt」→ 降序
    click_header("pt")
    mxp = max(r["peak_tokens"] for r in ref)
    expect = next(r["date"] for r in ref if r["peak_tokens"] == mxp)
    assert first_dates()[0] == expect, (first_dates()[0], expect)
    print(f"PASS 6 点「高峰Token」降序：首行={expect}")

    print("\nALL PERIOD SORT TESTS PASSED")
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
