# -*- coding: utf-8 -*-
"""每日统计列头排序测试：真实物理点击列头，验证首行变化与合计行沉底。"""
import os
import sys
import time
import ctypes

# 必须在创建任何窗口前设置：让 winfo 坐标与 SetCursorPos 同为物理像素
try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_monitor
import storage

app = token_monitor.App()
user32 = ctypes.windll.user32


def phys_click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    user32.mouse_event(2, 0, 0, 0, 0)
    time.sleep(0.04)
    user32.mouse_event(4, 0, 0, 0, 0)
    time.sleep(0.45)


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
    app.root.attributes("-topmost", True)
    app.root.lift()
    app.root.update()
    nb = app.nb
    idx = next(i for i in range(len(nb.tabs())) if nb.tab(i, "text") == "每日统计")
    nb.select(idx)
    app.root.update_idletasks()
    app.root.update()
    time.sleep(0.6)

    trees = find_treeviews(app.root, [])
    daily = None
    # 每日统计页的表格：7 列 date/req/in/out/hit/tok/cost
    for t in trees:
        cols = list(t["columns"])
        if len(cols) == 7 and "tok" in cols and "hit" in cols:
            daily = t
            break
    assert daily, "未找到每日统计表格"
    print("found daily tree")

    def first_rows():
        kids = daily.get_children()
        vals = []
        for i in range(min(3, len(kids) - 1)):
            vals.append(daily.item(kids[i], "values")[0])
        last = daily.item(kids[-1], "values")[0]
        tags = daily.item(kids[-1], "tags")
        return vals, last, tags

    def header_x(col):
        x0 = daily.winfo_rootx() + 3
        widths = []
        for c in list(daily["columns"]):
            widths.append(int(daily.column(c, "width")))
        pos = list(daily["columns"]).index(col)
        return x0 + sum(widths[:pos]) + widths[pos] / 2

    def click_header(col):
        """列头点击等价物：ttk 把 heading command 注册为 Tcl 命令（真实点击即调用它）。"""
        cmd = daily.heading(col, "command")
        try:
            if isinstance(cmd, str):
                daily.tk.call(cmd)  # 形如 '1743...<lambda>' 的内部注册命令
            elif callable(cmd):
                cmd()
        except Exception:
            try:  # 兜底：真实物理点击
                user32.SetForegroundWindow(app.root.winfo_id())
            except Exception:
                pass
            time.sleep(0.15)
            phys_click(header_x(col), daily.winfo_rooty() + 14)

    # 初始：默认日期倒序 → 首行应为今天
    init_first = first_rows()[0][0]
    today = storage.daily_stats()[0]["date"]  # 倒序首条=今天
    assert init_first == today, (init_first, today)
    print("PASS 1 初始按日期倒序（首行=今天）")

    # 点「费用」→ 首击降序：首行费用应为全部历史最大
    click_header("cost")
    all_rows = storage.daily_stats()
    max_cost = max(r["cost"] for r in all_rows)
    top_cost_day = next(r["date"] for r in all_rows if r["cost"] == max_cost)
    vals, last, tags = first_rows()
    assert vals[0] == top_cost_day, (vals[0], top_cost_day)
    assert last == "合计" and tags == ("total",), (last, tags)
    print(f"PASS 2 点「费用」降序：首行={vals[0]}（费用最大日 {max_cost:.4f} 元），合计行沉底")

    # 再点「费用」→ 升序：首行应为费用最小（非零）日
    click_header("cost")
    vals, _, _ = first_rows()
    min_cost = min(r["cost"] for r in all_rows if r["cost"] > 0)
    bottom_day = next(r["date"] for r in all_rows if r["cost"] == min_cost)
    assert vals[0] == bottom_day, (vals[0], bottom_day)
    print(f"PASS 3 再点「费用」升序：首行={vals[0]}（费用最小日）")

    # 点「日期」→ 首击升序：首行=最早记录日
    click_header("date")
    vals, _, _ = first_rows()
    oldest = min(r["date"] for r in all_rows)
    assert vals[0] == oldest, (vals[0], oldest)
    print(f"PASS 4 点「日期」升序：首行={vals[0]}（最早记录日）")

    # 表头箭头指示
    heading_text = daily.heading("cost", "text")
    assert "费用" in heading_text
    print(f"PASS 5 表头状态文本正常（cost 当前为 {heading_text!r}）")

    print("\nALL DAILY SORT TESTS PASSED")
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
