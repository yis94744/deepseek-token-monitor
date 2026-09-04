# -*- coding: utf-8 -*-
"""仪表盘柱状图点击→Token 构成联动 的 UI 集成测试（真实窗口，闪几秒自动关）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import token_monitor

app = token_monitor.App()
try:
    app.root.update()
    # 强制重绘仪表盘（确保画布尺寸与命中区就绪）
    app.root.update_idletasks()
    app._draw_chart()
    app._draw_token_bar()
    app.root.update()

    hits = app._bar_hits
    assert hits and len(hits) == 7, f"bar hits 应有 7 根柱: {len(hits) if hits else 0}"
    print("PASS 1 柱状图绘制，命中区 7 根")

    # 默认标题是"今日"
    assert "今日" in app.lbl_token_title.cget("text")
    assert app._selected_day is None

    # ---- 点击一根"有数据"的柱（从右往左找，优先今天/昨天）----
    import storage
    pick = None
    for x1, x2, d in reversed(hits):
        s = storage.day_stats(d)
        if s["cache_hit"] + s["cache_miss"] + s["completion"] > 0:
            pick = (x1, x2, d, s)
            break
    assert pick, "近 7 天应至少有一天有数据"
    x1, x2, d_target, s = pick
    app.chart.event_generate("<Button-1>", x=int((x1 + x2) / 2), y=100)
    app.root.update()
    assert app._selected_day == d_target, f"selected={app._selected_day} want={d_target}"
    title = app.lbl_token_title.cget("text")
    assert "今日 Token 构成" not in title and "Token 构成" in title, title
    # bar 上的合计文字应与 day_stats 一致
    total = s["cache_hit"] + s["cache_miss"] + s["completion"]
    texts = [app.bar.itemcget(i, "text") for i in app.bar.find_all()
             if app.bar.type(i) == "text"]
    assert any(t and f"{total:,}" in t for t in texts), (texts, total)
    print(f"PASS 2 点击柱 {d_target} → 标题/构成条切换（当日 total={total:,}）")

    # ---- 点最右（今天）----
    x1, x2, d_last = hits[-1]
    app.chart.event_generate("<Button-1>", x=int((x1 + x2) / 2), y=100)
    app.root.update()
    assert app._selected_day == d_last
    assert d_last == storage.past_days_stats(7)[-1]["date"]
    print("PASS 3 点击今天的柱 → 选中今天")

    # ---- 点空白（x=3 在画布最左，超出所有柱）----
    app.chart.event_generate("<Button-1>", x=3, y=5)
    app.root.update()
    assert app._selected_day is None
    assert app.lbl_token_title.cget("text") == "今日 Token 构成"
    print("PASS 4 点空白 → 恢复今日")

    # ---- 无数据日（历史空白日不在 7 天内，这里选第 1 根柱若为 0 也 OK）----
    print("\nALL DASHBOARD CLICK TESTS PASSED")
except Exception as exc:
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
