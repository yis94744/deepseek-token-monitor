# -*- coding: utf-8 -*-
"""水豚噜噜 · DeepSeek 用量监控 主程序。

一个完整的水豚主题桌面软件：
- 主窗口：仪表盘（今日/本周/本月、7 天费用柱状图、各模型统计、历史快照、设置）
- 悬浮窗：360 风格圆形噜噜球，可拖动、悬停显示消耗金额、单击弹出使用额度面板、右键操作，可在设置里开关
- 内置本地代理 http://127.0.0.1:8787，自动统计并计费 DeepSeek API 调用
- 每天 00:00 自动日结、每周一自动周结、每月 1 日自动月结
- 自动更新检测：启动及每 6 小时检查 GitHub Releases 最新版本，发现新版本弹窗提示并可一键跳转下载

使用前提：把调用 DeepSeek 的 base_url 改成 http://127.0.0.1:8787；
流式请求需加 "stream_options": {"include_usage": true}。
"""
import ctypes
import json
import os
import shutil
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

import cc_switch_sync
import dsh_sync
import kun_sync
import proxy_server
import scheduler
import storage
import updater
import yq_sync

# 当前版本（与 installer.iss 的 AppVersion 保持一致；用于自动更新检测）
APP_VERSION = "1.6.0"


# ================= 路径与资源 =================

def _base_dir() -> str:
    """程序所在目录：打包成 exe 后是 exe 所在目录，开发时是脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _res(name: str) -> str:
    """定位素材文件：优先取打包进 exe 的 assets，否则取脚本旁 assets。"""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        p = os.path.join(bundle, "assets", name)
        if os.path.exists(p):
            return p
    return os.path.join(_base_dir(), "assets", name)


BASE_DIR = _base_dir()
# 用户数据目录：统一放 AppData，安装到 Program Files 等只读目录也不影响读写
APPDATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                           "DeepSeekTokenMonitor")
CONFIG_PATH = os.path.join(APPDATA_DIR, "config.json")
SETTINGS_PATH = os.path.join(APPDATA_DIR, "settings.json")
DATA_DIR = os.path.join(APPDATA_DIR, "data")


def load_settings() -> dict:
    """读取运行设置（悬浮窗开关/位置等），文件不存在时用默认值。"""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings: dict):
    """保存运行设置。"""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# 内置默认配置（首次安装、AppData 下没有配置文件时使用；api_key 留空，启动后引导填写）
DEFAULT_CONFIG = {
    "api_key": "",
    "proxy_host": "127.0.0.1",
    "proxy_port": 8787,
    "proxy_enabled": True,
    "upstream_base_url": "https://api.deepseek.com",
    "balance_refresh_seconds": 300,
    "unknown_model_fallback": "deepseek-v4-flash",
    "cc_switch": {
        "enabled": True,
        "db_path": "",
        "app_types": ["codex"],
        "sync_interval_seconds": 2,
    },
    "kun": {
        "enabled": True,
        "threads_dir": "",
        "sync_interval_seconds": 3,
    },
    "dsh": {
        "enabled": True,
        "projcache_path": "",
        "sync_interval_seconds": 5,
    },
    "yq": {
        "enabled": True,
        "projcache_path": "",
        "sync_interval_seconds": 5,
    },
    "update_check": {
        "enabled": True,
        "interval_hours": 6,
    },
    "models": {
        "deepseek-v4-flash": {
            "note": "输入(缓存命中)0.02 / 输入(缓存未命中)1.00 / 输出 2.00 元每百万tokens（官方平峰价）",
            "cache_hit": 0.02,
            "cache_miss": 1.0,
            "output": 2.0,
        },
        "deepseek-v4-pro": {
            "note": "输入(缓存命中)0.025 / 输入(缓存未命中)3.00 / 输出 6.00 元每百万tokens（官方平峰价）",
            "cache_hit": 0.025,
            "cache_miss": 3.0,
            "output": 6.0,
        },
    },
}


def _ensure_runtime_dir():
    """准备 AppData 用户数据目录；首次运行时把旧版（exe 目录旁）配置和历史数据迁移过来。"""
    os.makedirs(APPDATA_DIR, exist_ok=True)
    # 迁移 / 创建配置文件
    if not os.path.exists(CONFIG_PATH):
        old_config = os.path.join(BASE_DIR, "config.json")
        if os.path.exists(old_config):
            shutil.copyfile(old_config, CONFIG_PATH)
        else:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    # 迁移历史统计数据库（不丢数据；单文件失败时跳过，避免被锁文件阻塞）
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(DATA_DIR, "usage.db")):
        old_data = os.path.join(BASE_DIR, "data")
        if os.path.isdir(old_data):
            for name in os.listdir(old_data):
                src = os.path.join(old_data, name)
                dst = os.path.join(DATA_DIR, name)
                if os.path.isfile(src) and not os.path.exists(dst):
                    try:
                        shutil.copyfile(src, dst)
                    except Exception:
                        pass  # 单个文件复制失败不影响其它文件


# ================= 水豚主题配色（取自水豚噜噜素材主色） =================
C_BG = "#faf3e7"          # 奶油背景
C_CARD = "#fffdf8"        # 卡片奶白
C_BROWN = "#8f491c"       # 深棕（标题条/强调）
C_BROWN_DARK = "#572c19"  # 深棕文字
C_BROWN_MID = "#a16e46"   # 中棕
C_BROWN_LIGHT = "#bf9674" # 浅棕
C_ORANGE = "#f1ac38"      # 主橙（高亮/按钮）
C_GOLD = "#fed15e"        # 金黄（选中）
C_ORANGE_DEEP = "#d77522" # 深橙
C_GREEN = "#6fa36b"       # 余额绿
C_GREEN_DEEP = "#00e04d"  # 悬停金额亮绿（纯正高饱和绿色，观感清晰）
C_PINK = "#ff69b4"        # +N 飘字纯粉
C_RED = "#d9534f"         # 错误红
C_TEXT = "#4a2f1d"        # 正文
C_SUB = "#8a6a4d"         # 次要文字
C_KEY = "#ff00fe"         # 透明色键（圆形悬浮窗/面板的四角变透明）
FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def fmt_money(value) -> str:
    try:
        return f"¥{float(value):,.4f}"
    except Exception:
        return "¥0.0000"


def fmt_money_short(value) -> str:
    """圆球上用的小金额格式：两位小数，过万显示 x.x万，保证不撑破小球。"""
    try:
        v = float(value)
        if v >= 10000:
            return "¥%.1f万" % (v / 10000)
        return "¥%.2f" % v
    except Exception:
        return "¥--"


def rounded_rect_points(x1, y1, x2, y2, r, steps=10):
    """圆角矩形闭合轮廓点：四角圆弧逐点采样，返回 [(x,y), ...]。

    相比 smooth=True 多边形：轮廓完全闭合、无接缝缺口，边角准确。
    """
    import math
    r = max(1, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = []

    def arc(cx, cy, a0, a1):
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    pts.append((x1 + r, y1))
    pts.append((x2 - r, y1))
    arc(x2 - r, y1 + r, -90, 0)     # 右上角
    pts.append((x2, y2 - r))
    arc(x2 - r, y2 - r, 0, 90)      # 右下角
    pts.append((x1 + r, y2))
    arc(x1 + r, y2 - r, 90, 180)    # 左下角
    pts.append((x1, y1 + r))
    arc(x1 + r, y1 + r, 180, 270)   # 左上角
    return pts


def rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """在 Canvas 上绘制圆角矩形（轮廓点采样，完全闭合、无平滑缺口）。"""
    pts = rounded_rect_points(x1, y1, x2, y2, r)
    flat = [c for pt in pts for c in pt]
    return canvas.create_polygon(flat, smooth=False, **kwargs)


class App:
    """水豚噜噜监控软件主应用。"""

    def __init__(self):
        _ensure_runtime_dir()   # 准备用户数据目录并迁移旧版数据
        self.config = self._load_config()
        self.settings = load_settings()
        storage.init_db(DATA_DIR)
        self._last_request_id = storage.max_request_id()  # 悬浮窗 +N 动效的新增记录游标
        self._popups = 0                                  # 弹窗错位计数

        self.stop_event = threading.Event()
        self._proxy_warned = False
        # 跨线程共享状态：调度线程写、UI 线程读
        self.state = {
            "balance": None,
            "balance_error": None,
            "balance_updated_at": None,
            "proxy_error": None,
            "proxy_ready": False,
        }

        # 后台线程：本地代理 + 定时任务（守护线程，随程序退出而结束）
        threading.Thread(target=proxy_server.start_proxy,
                         args=(self.config, self.state), daemon=True).start()
        threading.Thread(target=scheduler.run,
                         args=(self.config, self.state, self.stop_event), daemon=True).start()
        # CC Switch 数据同步线程：只读 CC Switch 本地数据库，不增加 API 链路
        threading.Thread(target=cc_switch_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()
        # Kun 数据同步线程：只读 Kun 本地可观测记录（observability/model-http）
        threading.Thread(target=kun_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()
        # DeepSeek Harness 数据同步线程：只读 dsh 会话用量投影缓存
        threading.Thread(target=dsh_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()
        # YQ Harness 数据同步线程：只读 YQ 会话用量投影缓存
        threading.Thread(target=yq_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()

        # 主窗口与悬浮窗
        self.root = tk.Tk()
        self._init_style()
        self._images = []           # 保存图片引用，防止被垃圾回收
        self._page_refreshers = {}  # 页签索引 -> 刷新函数
        self._update_checked = False
        self._tray_icon = None
        self._build_main_window()
        self._build_float_window()

        # 自动更新检测线程：启动即检查一次，之后按配置间隔周期检查
        updater.init_log(DATA_DIR)
        threading.Thread(target=self._update_check_loop, daemon=True).start()

    # ---------- 基础 ----------
    def _load_config(self) -> dict:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("配置错误", f"读取 config.json 失败：\n{exc}")
            root.destroy()
            raise SystemExit(1)

    def _save_config(self):
        """把当前配置写回 config.json（设置页修改后调用）。"""
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            messagebox.showerror("保存失败", f"写入 config.json 失败：\n{exc}")
            return False

    def _toggle_proxy(self):
        """设置页：开关本地代理，动态启停（无需重启程序）。"""
        enabled = self.proxy_var.get()
        self.config["proxy_enabled"] = enabled
        self._save_config()
        if enabled:
            # 重新拉起代理线程；端口被占等失败会写入 state['proxy_error']
            threading.Thread(target=proxy_server.start_proxy,
                             args=(self.config, self.state), daemon=True).start()
        else:
            server = self.state.get("proxy_server")
            if server:
                # shutdown 必须从其他线程调用且会阻塞，故另起线程执行
                threading.Thread(target=server.shutdown, daemon=True).start()
            self.state["proxy_ready"] = False
            self.state["proxy_error"] = None

    def _toggle_cc(self):
        """设置页：开关 CC Switch 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("cc_switch", {})["enabled"] = self.cc_var.get()
        self._save_config()

    def _toggle_kun(self):
        """设置页：开关 Kun 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("kun", {})["enabled"] = self.kun_var.get()
        self._save_config()

    def _toggle_dsh(self):
        """设置页：开关 DeepSeek Harness 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("dsh", {})["enabled"] = self.dsh_var.get()
        self._save_config()

    def _toggle_yq(self):
        """设置页：开关 YQ Harness 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("yq", {})["enabled"] = self.yq_var.get()
        self._save_config()

    def _init_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TNotebook", background=C_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=C_BROWN_LIGHT, foreground="#ffffff",
                        padding=(14, 6), font=(FONT, 10))
        style.map("TNotebook.Tab",
                  background=[("selected", C_ORANGE)],
                  foreground=[("selected", C_BROWN_DARK)])
        style.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                        foreground=C_TEXT, rowheight=26)
        style.configure("Treeview.Heading", background=C_BROWN_LIGHT, foreground="#ffffff",
                        font=(FONT, 9, "bold"))
        style.map("Treeview", background=[("selected", C_GOLD)])
        style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT, font=(FONT, 10))
        style.configure("TButton", font=(FONT, 9))

    def _keep_image(self, path: str, subsample: int = 1) -> tk.PhotoImage:
        """加载图片并保留引用；gif 显示第一帧。"""
        img = tk.PhotoImage(file=path)
        if subsample and subsample > 1:
            img = img.subsample(subsample)
        self._images.append(img)
        return img

    def _minimize_to_tray(self):
        """最小化到系统托盘：隐藏主窗口，托盘图标常驻（可恢复/退出）。

        托盘不可用时（pystray 缺失/启动失败）退回最小化到任务栏。
        """
        try:
            self.root.withdraw()
        except Exception:
            pass
        if self._tray_icon is not None:
            return
        try:
            import pystray
            from PIL import Image
            img = Image.open(_res("icon.ico"))
            menu = pystray.Menu(
                pystray.MenuItem("显示主界面",
                                 lambda: self.root.after(0, self._restore_from_tray)),
                pystray.MenuItem("退出程序",
                                 lambda: self.root.after(0, self.quit)),
            )
            icon = pystray.Icon("DeepSeekTokenMonitor", img,
                                "水豚噜噜 · DeepSeek 用量监控", menu)
            self._tray_icon = icon
            threading.Thread(target=icon.run, daemon=True).start()
        except Exception:
            self._tray_icon = None
            # 托盘不可用：退回最小化到任务栏
            try:
                self.root.deiconify()
                self.root.iconify()
            except Exception:
                pass

    def _restore_from_tray(self):
        """从托盘恢复主窗口（同时退出托盘图标）。"""
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def _ask_close_mode(self):
        """弹窗询问关闭方式：直接关闭 / 最小化到托盘（可记住选择）。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("退出方式")
        dlg.configure(bg=C_BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        try:
            dlg.attributes("-toolwindow", True)
        except Exception:
            pass
        tk.Label(dlg, text="关闭后：", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=18, pady=(14, 4))
        tk.Label(dlg, text="① 直接关闭程序（停止统计与代理）\n② 最小化到系统托盘（后台继续监控）",
                 bg=C_BG, fg=C_TEXT, font=(FONT, 9), justify="left").pack(
            anchor="w", padx=18, pady=(0, 10))
        btns = tk.Frame(dlg, bg=C_BG)
        btns.pack(padx=18, pady=(0, 8))
        choice = {"mode": None}

        def choose(mode):
            if remember_var.get():
                self.settings["close_behavior"] = mode
                save_settings(self.settings)
            choice["mode"] = mode
            dlg.destroy()

        ttk.Button(btns, text="直接关闭", command=lambda: choose("close"),
                   width=12).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="最小化到托盘", command=lambda: choose("tray"),
                   width=12).pack(side="left")
        remember_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(dlg, text="记住选择，下次不再询问",
                        variable=remember_var).pack(anchor="w", padx=18, pady=(0, 12))
        dlg.grab_set()
        try:
            self.root.wait_window(dlg)
        except Exception:
            return None
        return choice["mode"]

    # ================= 主窗口 =================
    def _build_main_window(self):
        root = self.root
        root.title("水豚噜噜 · DeepSeek 用量监控")
        root.geometry("900x620")
        root.minsize(820, 560)
        root.configure(bg=C_BG)
        try:
            root.iconbitmap(_res("icon.ico"))
        except Exception:
            pass
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        # 保留原生标题栏（自带最小化/最大化/关闭），任务栏正常显示本程序

        # ---- 顶部标题条 ----
        header = tk.Frame(root, bg=C_BROWN)
        header.pack(fill="x")
        logo = self._keep_image(_res("logo_round.png"), subsample=5)  # 256 -> 约51
        tk.Label(header, image=logo, bg=C_BROWN).pack(side="left", padx=(14, 10), pady=8)
        title_box = tk.Frame(header, bg=C_BROWN)
        title_box.pack(side="left", pady=6)
        tk.Label(title_box, text="水豚噜噜 · DeepSeek 用量监控", bg=C_BROWN, fg="#fff3dc",
                 font=(FONT, 14, "bold")).pack(anchor="w")
        tk.Label(title_box, text="DeepSeek Token Monitor", bg=C_BROWN, fg=C_GOLD,
                 font=(FONT, 8)).pack(anchor="w")
        self.lbl_date = tk.Label(header, text="", bg=C_BROWN, fg="#f6d9ae",
                                 font=(FONT, 10))
        self.lbl_date.pack(side="right", padx=(0, 4))
        # 发现新版本提示（点击跳转下载页）
        self.lbl_update_banner = tk.Label(header, text="", bg=C_BROWN, fg=C_GOLD,
                                          font=(FONT, 9, "bold"), cursor="hand2")
        self.lbl_update_banner.pack(side="right", padx=8)
        self.lbl_update_banner.bind("<Button-1>", lambda e: self._open_update_page())
        # 最小化/最大化/关闭按钮统一使用窗口右上角原生标题栏的一套（不再重复添加）

        # ---- 顶部指标卡 ----
        cards = tk.Frame(root, bg=C_BG)
        cards.pack(fill="x", padx=12, pady=10)
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="card")
        self.card_tokens, self.card_tokens_sub = self._make_card(cards, 0, "今日 Token")
        self.card_cost, self.card_cost_sub = self._make_card(cards, 1, "今日费用")
        self.card_balance, self.card_balance_sub = self._make_card(cards, 2, "账户余额")
        self.card_requests, self.card_requests_sub = self._make_card(cards, 3, "今日请求")

        # ---- 页签区 ----
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.nb = nb
        self._build_dashboard_page(nb)     # 0 仪表盘（由定时器实时刷新）
        self._page_refreshers[1] = self._add_summary_page(
            nb, "今日", 1, storage.today_stats, storage.today_breakdown)
        self._page_refreshers[2] = self._add_summary_page(
            nb, "本周", 2, storage.this_week_stats, storage.this_week_breakdown)
        self._page_refreshers[3] = self._add_summary_page(
            nb, "本月", 3, storage.this_month_stats, storage.this_month_breakdown)
        self._page_refreshers[4] = self._add_model_page(nb, 4)
        self._page_refreshers[5] = self._add_history_page(nb, 5)
        self._page_refreshers[6] = self._add_settings_page(nb, 6)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- 底部状态栏 ----
        status = tk.Frame(root, bg=C_BROWN)
        status.pack(fill="x", side="bottom")
        self.lbl_proxy_status = tk.Label(status, text="代理 启动中...", bg=C_BROWN,
                                         fg=C_GOLD, font=(FONT, 9))
        self.lbl_proxy_status.pack(side="left", padx=14, pady=4)
        self.lbl_balance_time = tk.Label(status, text="", bg=C_BROWN, fg="#f6d9ae",
                                         font=(FONT, 9))
        self.lbl_balance_time.pack(side="right", padx=14, pady=4)

    def _make_card(self, parent, column, title):
        card = tk.Frame(parent, bg=C_CARD, highlightbackground=C_GOLD,
                        highlightthickness=1, bd=0)
        card.grid(row=0, column=column, sticky="nsew", padx=6)
        tk.Label(card, text=title, bg=C_CARD, fg=C_SUB, font=(FONT, 9)).pack(
            anchor="w", padx=12, pady=(8, 0))
        value = tk.Label(card, text="--", bg=C_CARD, fg=C_BROWN_DARK,
                         font=(MONO, 15, "bold"))
        value.pack(anchor="w", padx=12)
        sub = tk.Label(card, text="", bg=C_CARD, fg=C_SUB, font=(FONT, 8))
        sub.pack(anchor="w", padx=12, pady=(0, 8))
        return value, sub

    # ---------- 仪表盘页 ----------
    def _build_dashboard_page(self, nb):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="仪表盘")

        left = tk.Frame(page, bg=C_BG)
        left.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=8)

        tk.Label(left, text="最近 7 天费用", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 2))
        self.chart = tk.Canvas(left, bg=C_CARD, height=210, highlightthickness=1,
                               highlightbackground=C_BROWN_LIGHT)
        self.chart.pack(fill="both", expand=True)

        # 今日 token 构成
        tk.Label(left, text="今日 Token 构成", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(10, 2))
        self.bar = tk.Canvas(left, bg=C_CARD, height=30, highlightthickness=1,
                             highlightbackground=C_BROWN_LIGHT)
        self.bar.pack(fill="x")
        legend = tk.Frame(left, bg=C_BG)
        legend.pack(fill="x", pady=(4, 0))
        for text, color in (("输入(缓存命中)", C_BROWN_MID), ("输入(缓存未命中)", C_ORANGE),
                            ("输出", C_GOLD)):
            tk.Label(legend, text="● " + text, bg=C_BG, fg=color, font=(FONT, 8)).pack(
                side="left", padx=(0, 12))

        right = tk.Frame(page, bg=C_BG)
        right.pack(side="right", fill="y", padx=(4, 10), pady=8)
        self.week_card_value = self._make_small_card(right, "本周至今")
        self.week_card_value.pack(fill="x", pady=(0, 8))
        self.month_card_value = self._make_small_card(right, "本月至今")
        self.month_card_value.pack(fill="x")
        mascot = self._keep_image(_res("mascot.png"), subsample=4)  # 300 -> 75
        tk.Label(right, image=mascot, bg=C_BG).pack(side="bottom", pady=(8, 0))

    def _make_small_card(self, parent, title):
        card = tk.Frame(parent, bg=C_CARD, highlightbackground=C_GOLD,
                        highlightthickness=1)
        tk.Label(card, text=title, bg=C_CARD, fg=C_SUB, font=(FONT, 9)).pack(
            anchor="w", padx=10, pady=(6, 0))
        value = tk.Label(card, text="--", bg=C_CARD, fg=C_BROWN_DARK,
                         font=(FONT, 10, "bold"))
        value.pack(anchor="w", padx=10, pady=(0, 6))
        return value

    def _draw_chart(self):
        """绘制最近 7 天费用柱状图。"""
        c = self.chart
        c.delete("all")
        data = storage.past_days_stats(7)
        w = c.winfo_width() or 400
        h = c.winfo_height() or 200
        pad_l, pad_r, pad_t, pad_b = 46, 10, 18, 30
        plot_w = max(w - pad_l - pad_r, 10)
        plot_h = max(h - pad_t - pad_b, 10)
        maxv = max((d["cost"] for d in data), default=0) or 1.0
        # 基线
        c.create_line(pad_l, h - pad_b, w - pad_r, h - pad_b, fill=C_BROWN_LIGHT)
        n = len(data)
        slot = plot_w / n
        bar_w = max(slot * 0.55, 6)
        for i, d in enumerate(data):
            cx = pad_l + slot * i + slot / 2
            bh = d["cost"] / maxv * plot_h
            x1, y1 = cx - bar_w / 2, h - pad_b - bh
            x2, y2 = cx + bar_w / 2, h - pad_b
            c.create_rectangle(x1, y1, x2, y2, fill=C_ORANGE, outline="")
            # 顶部费用数字
            if d["cost"] > 0:
                c.create_text(cx, y1 - 9, text=f"{d['cost']:.2f}",
                              fill=C_BROWN_DARK, font=(MONO, 7))
            # 日期 MM-DD
            c.create_text(cx, h - pad_b + 12, text=d["date"][5:],
                          fill=C_SUB, font=(FONT, 8))
        c.create_text(pad_l, 10, anchor="w", text="元/日", fill=C_SUB, font=(FONT, 8))

    def _draw_token_bar(self):
        """绘制今日 token 构成横向比例条。"""
        c = self.bar
        c.delete("all")
        s = storage.today_stats()
        w = c.winfo_width() or 300
        h = c.winfo_height() or 28
        total = s["cache_hit"] + s["cache_miss"] + s["completion"]
        if total <= 0:
            c.create_text(w / 2, h / 2, text="今日暂无调用", fill=C_SUB, font=(FONT, 9))
            return
        parts = [
            (s["cache_hit"], C_BROWN_MID),
            (s["cache_miss"], C_ORANGE),
            (s["completion"], C_GOLD),
        ]
        x = 2.0
        for count, color in parts:
            pw = (count / total) * (w - 4)
            c.create_rectangle(x, 4, x + pw, h - 4, fill=color, outline="")
            x += pw
        c.create_text(w - 6, h / 2, anchor="e", text=f"{fmt_int(total)} tokens",
                      fill=C_BROWN_DARK, font=(FONT, 8, "bold"))

    # ---------- 今日/本周/本月 汇总页 ----------
    def _add_summary_page(self, nb, title, index, agg_fn, brk_fn):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text=title)
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", padx=12, pady=8)
        lbl_requests = tk.Label(top, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        lbl_requests.grid(row=0, column=0, sticky="w", padx=(0, 16))
        lbl_input = tk.Label(top, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        lbl_input.grid(row=0, column=1, sticky="w", padx=(0, 16))
        lbl_output = tk.Label(top, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        lbl_output.grid(row=0, column=2, sticky="w", padx=(0, 16))
        lbl_cost = tk.Label(top, text="", bg=C_BG, fg=C_ORANGE_DEEP, font=(FONT, 10, "bold"))
        lbl_cost.grid(row=0, column=3, sticky="w")

        tree = self._make_model_tree(page)
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        def refresh():
            s = agg_fn()
            b = brk_fn()
            lbl_requests.config(text=f"请求数  {fmt_int(s['requests'])}")
            lbl_input.config(text=f"输入  {fmt_int(s['cache_hit'] + s['cache_miss'])}"
                                   f"（命中 {fmt_int(s['cache_hit'])}）")
            lbl_output.config(text=f"输出  {fmt_int(s['completion'])}")
            lbl_cost.config(text=f"费用  {fmt_money(s['cost'])}")
            self._fill_model_tree(tree, b)

        refresh()
        return refresh

    # ---------- 模型统计页 ----------
    def _add_model_page(self, nb, index):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="模型统计")
        tk.Label(page, text="本月各模型", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        tree_month = self._make_model_tree(page)
        tree_month.pack(fill="x", padx=12)
        tk.Label(page, text="今日各模型", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        tree_today = self._make_model_tree(page)
        tree_today.pack(fill="x", padx=12)

        def refresh():
            self._fill_model_tree(tree_month, storage.this_month_breakdown())
            self._fill_model_tree(tree_today, storage.today_breakdown())

        refresh()
        return refresh

    def _make_model_tree(self, parent):
        tree = ttk.Treeview(parent, columns=("model", "req", "input", "output", "cost"),
                            show="headings")
        for col, text, width in (("model", "模型", 220), ("req", "请求数", 90),
                                 ("input", "输入token", 120), ("output", "输出token", 120),
                                 ("cost", "费用", 120)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="center" if col != "model" else "w")
        return tree

    def _fill_model_tree(self, tree, rows):
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tree.insert("", "end", values=(
                row["model"], fmt_int(row["requests"]),
                fmt_int(row["cache_hit"] + row["cache_miss"]),
                fmt_int(row["completion"]), fmt_money(row["cost"])))
    # ---------- 历史快照页 ----------
    def _add_history_page(self, nb, index):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="历史快照")
        self._history_trees = {}
        for col, (title, key_title) in enumerate([
            ("日结", "日期"), ("周结", "周一"), ("月结", "月份"),
        ]):
            box = tk.Frame(page, bg=C_BG)
            box.grid(row=0, column=col, sticky="nsew", padx=6, pady=10)
            page.grid_columnconfigure(col, weight=1)
            tk.Label(box, text=title, bg=C_BG, fg=C_BROWN_DARK,
                     font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 4))
            tree = ttk.Treeview(box, columns=("key", "req", "input", "output", "cost"),
                                show="headings")
            for c, text in zip(("key", "req", "input", "output", "cost"),
                               (key_title, "请求数", "输入", "输出", "费用")):
                tree.heading(c, text=text)
            for c, width in zip(("key", "req", "input", "output", "cost"),
                                (110, 60, 80, 80, 90)):
                tree.column(c, width=width, anchor="center" if c != "key" else "w")
            tree.pack(fill="both", expand=True)
            self._history_trees[col] = tree

        def refresh():
            fetchers = (storage.list_daily_summaries, storage.list_weekly_summaries,
                        storage.list_monthly_summaries)
            for col, fetcher in enumerate(fetchers):
                tree = self._history_trees.get(col)
                if not tree:
                    continue
                for item in tree.get_children():
                    tree.delete(item)
                for row in fetcher():
                    tree.insert("", "end", values=(
                        row["key"], fmt_int(row["requests"]),
                        fmt_int(row["cache_hit"] + row["cache_miss"]),
                        fmt_int(row["completion"]), fmt_money(row["cost"])))

        refresh()
        return refresh

    # ---------- 设置页 ----------
    def _add_settings_page(self, nb, index):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="设置")

        left = tk.Frame(page, bg=C_BG)
        left.pack(side="left", fill="both", expand=True, padx=14, pady=12)

        # 悬浮窗开关
        self.float_var = tk.BooleanVar(value=bool(self.settings.get("float_window", True)))
        ttk.Checkbutton(left, text="显示桌面悬浮窗", variable=self.float_var,
                        command=self._toggle_float).pack(anchor="w", pady=(0, 10))

        # 余额刷新间隔
        row = tk.Frame(left, bg=C_BG)
        row.pack(anchor="w", pady=(0, 10))
        tk.Label(row, text="余额刷新间隔(秒):", bg=C_BG, fg=C_TEXT,
                 font=(FONT, 10)).pack(side="left")
        self.refresh_var = tk.StringVar(
            value=str(self.config.get("balance_refresh_seconds", 300)))
        ttk.Entry(row, textvariable=self.refresh_var, width=8).pack(side="left", padx=6)
        ttk.Button(row, text="保存", command=self._save_refresh_interval).pack(side="left")

        # API Key（可在软件内修改，无需编辑配置文件）
        key_box = tk.Frame(left, bg=C_BG)
        key_box.pack(anchor="w", pady=(0, 10))
        tk.Label(key_box, text="API Key:", bg=C_BG, fg=C_TEXT, font=(FONT, 10)).pack(side="left")
        self.key_var = tk.StringVar(value=(self.config.get("api_key") or ""))
        self.key_show_var = tk.BooleanVar(value=False)
        self.key_entry = ttk.Entry(key_box, textvariable=self.key_var, width=32, show="*")
        self.key_entry.pack(side="left", padx=(6, 4))
        self.key_show_var.trace_add("write", self._toggle_key_show)
        ttk.Checkbutton(key_box, text="显示", variable=self.key_show_var).pack(side="left")
        ttk.Button(key_box, text="保存 Key", command=self._save_api_key).pack(
            side="left", padx=(6, 0))

        # 数据源开关（五个数据源全部保留，可分别开关，修改即时生效）
        tk.Label(left, text="数据源开关:", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.proxy_var = tk.BooleanVar(value=bool(self.config.get("proxy_enabled", True)))
        ttk.Checkbutton(left, text="本地代理 (127.0.0.1:8787)", variable=self.proxy_var,
                        command=self._toggle_proxy).pack(anchor="w", pady=(0, 4))
        self.cc_var = tk.BooleanVar(
            value=bool((self.config.get("cc_switch") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="CC Switch 同步", variable=self.cc_var,
                        command=self._toggle_cc).pack(anchor="w", pady=(0, 4))
        self.kun_var = tk.BooleanVar(
            value=bool((self.config.get("kun") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="Kun 同步", variable=self.kun_var,
                        command=self._toggle_kun).pack(anchor="w", pady=(0, 4))
        self.dsh_var = tk.BooleanVar(
            value=bool((self.config.get("dsh") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="DeepSeek Harness 同步", variable=self.dsh_var,
                        command=self._toggle_dsh).pack(anchor="w", pady=(0, 4))
        self.yq_var = tk.BooleanVar(
            value=bool((self.config.get("yq") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="YQ Harness 同步", variable=self.yq_var,
                        command=self._toggle_yq).pack(anchor="w", pady=(0, 10))

        # 代理状态
        self.lbl_setting_proxy = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_setting_proxy.pack(anchor="w", pady=(0, 6))
        # CC Switch 数据同步状态
        self.lbl_ccsync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_ccsync.pack(anchor="w", pady=(0, 6))
        # Kun 数据同步状态
        self.lbl_kunsync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_kunsync.pack(anchor="w", pady=(0, 6))
        # DeepSeek Harness 数据同步状态
        self.lbl_dshsync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_dshsync.pack(anchor="w", pady=(0, 6))
        # YQ Harness 数据同步状态
        self.lbl_yqsync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_yqsync.pack(anchor="w", pady=(0, 12))

        # 操作按钮
        ttk.Button(left, text="立即刷新余额", command=self._refresh_balance_now).pack(
            anchor="w", fill="x", pady=2)
        ttk.Button(left, text="检查更新", command=self._check_update_now).pack(
            anchor="w", fill="x", pady=2)
        # 更新检查状态（点击"发现新版本"可直接下载）
        self.lbl_updcheck = tk.Label(left, text="", bg=C_BG, fg=C_SUB, font=(FONT, 9),
                                     cursor="hand2")
        self.lbl_updcheck.pack(anchor="w", pady=(0, 6))
        self.lbl_updcheck.bind("<Button-1>", lambda e: self._open_update_page())
        ttk.Button(left, text="打开配置文件 config.json",
                   command=lambda: os.startfile(CONFIG_PATH)).pack(anchor="w", fill="x", pady=2)
        ttk.Button(left, text="打开数据文件夹",
                   command=lambda: os.startfile(DATA_DIR)).pack(anchor="w", fill="x", pady=2)

        tk.Label(left, text="提示：修改各模型单价请编辑 config.json 后重启程序；"
                            "流式请求需开启 include_usage 才能统计 token。",
                 bg=C_BG, fg=C_SUB, font=(FONT, 8), wraplength=360,
                 justify="left").pack(anchor="w", pady=(12, 0))

        # 右侧：单价表 + 吉祥物
        right = tk.Frame(page, bg=C_BG)
        right.pack(side="right", fill="both", padx=(0, 14), pady=12)
        tk.Label(right, text="内置单价（元/百万 tokens）", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 4))
        tree = ttk.Treeview(right, columns=("model", "hit", "miss", "out"), show="headings")
        for col, text in zip(("model", "hit", "miss", "out"),
                             ("模型", "输入·命中", "输入·未命中", "输出")):
            tree.heading(col, text=text)
        for col, width in zip(("model", "hit", "miss", "out"), (170, 90, 100, 80)):
            tree.column(col, width=width, anchor="center" if col != "model" else "w")
        tree.pack(fill="both", expand=True)
        self._price_tree = tree
        mascot = self._keep_image(_res("deco1.gif"), subsample=7)
        tk.Label(right, image=mascot, bg=C_BG).pack(side="bottom", pady=(8, 0))

        def refresh():
            for item in tree.get_children():
                tree.delete(item)
            for model, price in (self.config.get("models") or {}).items():
                tree.insert("", "end", values=(
                    model, price.get("cache_hit", 0), price.get("cache_miss", 0),
                    price.get("output", 0)))
            # 代理状态
            if self.state.get("proxy_error"):
                self.lbl_setting_proxy.config(text="本地代理：启动失败 " + self.state["proxy_error"],
                                              fg=C_RED)
            elif self.state.get("proxy_ready"):
                port = self.config.get("proxy_port", 8787)
                self.lbl_setting_proxy.config(text=f"本地代理：运行中 http://127.0.0.1:{port}",
                                              fg=C_GREEN)
            else:
                self.lbl_setting_proxy.config(text="本地代理：启动中...", fg=C_SUB)

        refresh()
        return refresh

    # ---------- API Key 管理 ----------
    def _apply_api_key(self, key: str) -> bool:
        """写入 API Key 到配置文件并立即刷新余额。"""
        self.config["api_key"] = key
        if not self._save_config():
            return False
        self._refresh_balance_now()
        return True

    def _save_api_key(self):
        """设置页：保存 API Key。"""
        key = self.key_var.get().strip()
        if not key and not messagebox.askyesno("确认", "API Key 留空将无法显示余额，确定保存吗？"):
            return
        if self._apply_api_key(key):
            self.key_var.set(key)
            messagebox.showinfo("已保存", "API Key 已保存，正在刷新余额…")

    def _toggle_key_show(self, *args):
        """切换 API Key 明文/掩码显示。"""
        try:
            self.key_entry.config(show="" if self.key_show_var.get() else "*")
        except Exception:
            pass

    def _maybe_show_key_wizard(self):
        """首次启动时如果没有 API Key，弹出引导窗口让用户填写（可跳过）。"""
        if (self.config.get("api_key") or "").strip():
            return
        win = tk.Toplevel(self.root)
        win.title("欢迎使用水豚噜噜监控")
        win.geometry("430x340")
        win.configure(bg=C_BG)
        win.transient(self.root)
        win.grab_set()
        try:
            logo = self._keep_image(_res("logo_round.png"), subsample=3)  # 256 -> 约85
            tk.Label(win, image=logo, bg=C_BG).pack(pady=(18, 6))
        except Exception:
            pass
        tk.Label(win, text="首次使用 · 请填写 API Key", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 13, "bold")).pack()
        tk.Label(win, text="用于显示账户余额；跳过也能正常统计用量，之后可在“设置”页补填。",
                 bg=C_BG, fg=C_SUB, font=(FONT, 9), wraplength=340,
                 justify="center").pack(pady=(4, 12))
        var = tk.StringVar()
        ttk.Entry(win, textvariable=var, width=46, show="*").pack(pady=(0, 14))
        btn = tk.Frame(win, bg=C_BG)
        btn.pack()

        def save():
            key = var.get().strip()
            if not key:
                messagebox.showwarning("提示", "请输入 API Key，或选择跳过。")
                return
            if self._apply_api_key(key):
                win.destroy()
                messagebox.showinfo("已保存", "API Key 已保存，正在查询余额…")

        tk.Button(btn, text="保存", bg=C_ORANGE, fg="#ffffff", width=10,
                  relief="flat", command=save).pack(side="left", padx=8)
        tk.Button(btn, text="跳过", bg=C_BROWN_LIGHT, fg="#ffffff", width=10,
                  relief="flat", command=win.destroy).pack(side="left", padx=8)

    def _toggle_float(self):
        self.settings["float_window"] = bool(self.float_var.get())
        save_settings(self.settings)
        if self.float_var.get():
            self.float_win.deiconify()
        else:
            self.float_win.withdraw()

    def _save_refresh_interval(self):
        try:
            value = int(self.refresh_var.get())
            if value < 30:
                raise ValueError
        except Exception:
            messagebox.showwarning("参数错误", "刷新间隔必须是大于等于 30 的整数（秒）。")
            return
        self.config["balance_refresh_seconds"] = value
        self.settings["refresh_seconds"] = value
        save_settings(self.settings)
        if self._save_config():
            messagebox.showinfo("已保存", f"余额刷新间隔已改为 {value} 秒，立即生效。")
    # ================= 悬浮窗（360 风格圆形噜噜球） =================
    def _build_float_window(self):
        IMG = 96     # 噜噜头像尺寸（Pillow 精确缩放），圆圈与图片完全等大
        TEXT_H = 26  # 圆球上方的悬停金额文字区（透明，仅药丸与文字可见）
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", C_KEY)  # 圆形窗口：键色区域变透明
        except Exception:
            pass
        self.float_win = win
        self.float_size = IMG
        self._float_drag_off = (0, 0)
        self._float_drag_start = (0, 0)
        self._float_panel_open = False

        w, h = IMG, IMG + TEXT_H
        cv = tk.Canvas(win, width=w, height=h, highlightthickness=0, bg=C_KEY)
        cv.pack()
        self.float_cv = cv

        # 圆球 = 噜噜圆形头像本身：Pillow 预渲染抗锯齿合成（奶油底 + 圆形裁剪头像 + 橙圈），
        # 消除裁剪边缘的白点/锯齿；Pillow 不可用时退回 Canvas 绘制
        ball = self._make_ball_image(IMG)
        if ball is not None:
            cv.create_image(w / 2, TEXT_H + IMG / 2, image=ball)
        else:
            cv.create_oval(0, TEXT_H, IMG, h, fill=C_CARD, outline="")
            avatar = self._keep_image(_res("logo_round.png"), subsample=3)  # 256 -> 约85
            cv.create_image(w / 2, TEXT_H + IMG / 2, image=avatar)
            cv.create_oval(1, TEXT_H + 1, w - 1, h - 1, outline=C_ORANGE, width=2)
        # 圆球上方：悬停金额的深棕药丸底 + 亮绿文字。
        # 文字必须画在不透明底色上——透明键色区会把绿字的抗锯齿边缘与洋红键色
        # 混合，导致绿色观感发暗发灰（用户反馈"绿色不对"的根因）。
        pill_h = 20
        pill_pts = rounded_rect_points(0, 0, w, pill_h, 10)
        self.float_pill = cv.create_polygon(
            [c for pt in pill_pts for c in pt], smooth=False,
            fill=C_BROWN_DARK, outline=C_ORANGE, width=1, state="hidden")
        self.float_cost = cv.create_text(w / 2, pill_h / 2, text="", fill=C_GREEN_DEEP,
                                         font=(MONO, 10, "bold"), state="hidden")

        # 事件：悬停显示金额 / 单击开关面板 / 拖动 / 双击主界面 / 右键菜单
        cv.bind("<Enter>", self._float_on_enter)
        cv.bind("<Leave>", self._float_on_leave)
        cv.bind("<Button-1>", self._float_start_drag)
        cv.bind("<B1-Motion>", self._float_on_drag)
        cv.bind("<ButtonRelease-1>", self._float_save_pos)
        cv.bind("<Double-Button-1>", lambda e: self._show_main())
        cv.bind("<Button-3>", self._float_menu)

        # 点击面板：显示使用额度
        self._build_float_panel()

        # 初始位置：优先上次保存的位置，否则屏幕右上角
        x = self.settings.get("float_x")
        y = self.settings.get("float_y")
        if x is None or y is None:
            x, y = win.winfo_screenwidth() - w - 30, 90
        win.geometry(f"+{x}+{y}")
        if not self.settings.get("float_window", True):
            win.withdraw()

    def _make_ball_image(self, size: int):
        """用 Pillow 预渲染抗锯齿圆球图片（返回 tk.PhotoImage，失败返回 None）。

        8x 超采样 + LANCZOS 缩小实现抗锯齿：奶油底圆形裁剪噜噜头像（圆形遮罩），
        边缘橙色细圈；圆外区域统一为透明键色 C_KEY，消除裁剪白点与锯齿，
        圆边更圆润（更高分辨率 + 更细的阈值量化）。
        """
        try:
            from PIL import Image, ImageDraw
        except Exception:
            return None
        try:
            SS = 8                     # 超采样倍数（越高圆边越圆润）
            W = size * SS
            resample = getattr(Image, "Resampling", Image).LANCZOS
            # 透明画布 -> 奶油底圆（内接圆，四角留透明）
            img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse([0, 0, W - 1, W - 1], fill=(252, 253, 248, 255))  # C_CARD
            # 噜噜头像：放大 + 圆形遮罩（遮罩略小于圆，边缘落在奶油底上，避免白边）
            avatar = Image.open(_res("logo_round.png")).convert("RGBA")
            avatar = avatar.resize((W, W), resample)
            mask = Image.new("L", (W, W), 0)
            ImageDraw.Draw(mask).ellipse([3, 3, W - 4, W - 4], fill=255)
            img = Image.alpha_composite(
                img, Image.composite(avatar, Image.new("RGBA", (W, W), (0, 0, 0, 0)), mask))
            # 橙色细圈（画在奶油底上，超采样天然抗锯齿）
            d = ImageDraw.Draw(img)
            d.ellipse([2, 2, W - 3, W - 3], outline=(241, 172, 56, 255), width=SS * 2)
            small = img.resize((size, size), resample)
            # 展平：半透明像素按阈值取舍，圆外像素统一为透明键色（消除粉色/白色杂边）
            out = Image.new("RGB", (size, size), C_KEY)
            px_out = out.load()
            px_in = small.load()
            for y in range(size):
                for x in range(size):
                    r, g, b, a = px_in[x, y]
                    px_out[x, y] = (r, g, b) if a >= 110 else (0xFF, 0x00, 0xFE)
            import io
            buf = io.BytesIO()
            out.save(buf, format="PNG")
            photo = tk.PhotoImage(data=buf.getvalue())
            self._images.append(photo)
            return photo
        except Exception:
            return None

    def _build_float_panel(self):
        """点击圆球弹出的「使用额度」面板：圆角卡片，展示今日/本月用量与余额。"""
        w, h = 236, 176
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        try:
            panel.attributes("-transparentcolor", C_KEY)
        except Exception:
            pass
        self.float_panel = panel
        cv = tk.Canvas(panel, width=w, height=h, highlightthickness=0, bg=C_KEY)
        cv.pack()
        self.float_panel_cv = cv
        self.float_panel_size = (w, h)

        rounded_rect(cv, 2, 2, w - 2, h - 2, 14, fill="#fff8ec", outline=C_ORANGE, width=2)
        cv.create_text(16, 18, anchor="w", text="噜噜 · 使用额度", fill=C_BROWN_DARK,
                       font=(FONT, 10, "bold"))
        cv.create_text(w - 14, 18, text="✕", fill=C_SUB, font=(FONT, 10, "bold"), tags="close")
        cv.tag_bind("close", "<Button-1>",
                    lambda e: self._float_toggle_panel(force_close=True))

        rows = [
            ("今日 Token", "float_p_tokens", C_BROWN_DARK),
            ("今日费用", "float_p_cost", C_ORANGE_DEEP),
            ("本月费用", "float_p_month", C_ORANGE_DEEP),
            ("余额 / 额度", "float_p_balance", C_GREEN),
        ]
        y = 48
        for label, attr, color in rows:
            cv.create_text(18, y, anchor="w", text=label, fill=C_SUB, font=(FONT, 9))
            item = cv.create_text(w - 18, y, anchor="e", text="--", fill=color,
                                  font=(MONO, 9, "bold"))
            setattr(self, attr, item)
            y += 31
        panel.withdraw()

    def _float_place_panel(self):
        """把面板放到圆球右侧（贴边防溢出屏幕）并显示。"""
        panel = self.float_panel
        w, h = self.float_panel_size
        bx, by = self.float_win.winfo_x(), self.float_win.winfo_y()
        sw, sh = panel.winfo_screenwidth(), panel.winfo_screenheight()
        x = bx + self.float_size + 6
        if x + w > sw:
            x = bx - w - 6
        y = min(max(by - 24, 0), sh - h - 40)
        panel.geometry(f"+{int(x)}+{int(y)}")
        panel.deiconify()
        panel.lift()
        self._float_panel_open = True
        self._refresh_float_panel()

    def _float_toggle_panel(self, force_close=False):
        if force_close or self._float_panel_open:
            self.float_panel.withdraw()
            self._float_panel_open = False
            return
        self._float_place_panel()

    def _refresh_float_panel(self):
        """刷新面板内容（今日/本月/余额），面板隐藏时不做事。"""
        try:
            if not self._float_panel_open:
                return
            cv = self.float_panel_cv
            s = storage.today_stats()
            total = s["cache_hit"] + s["cache_miss"] + s["completion"]
            cv.itemconfigure(self.float_p_tokens, text=fmt_int(total))
            cv.itemconfigure(self.float_p_cost, text=fmt_money(s["cost"]))
            cv.itemconfigure(self.float_p_month,
                             text=fmt_money(storage.this_month_stats()["cost"]))
            if self.state.get("balance") is not None:
                cv.itemconfigure(self.float_p_balance,
                                 text=fmt_money(self.state["balance"]), fill=C_GREEN)
            elif self.state.get("balance_error"):
                cv.itemconfigure(self.float_p_balance, text="获取失败", fill=C_RED)
            else:
                cv.itemconfigure(self.float_p_balance, text="加载中...", fill=C_SUB)
        except Exception:
            pass

    def _float_on_enter(self, event):
        """鼠标悬停：圆球上方药丸内显示当前消耗金额（今日费用，亮绿）。"""
        try:
            s = storage.today_stats()
            self.float_cv.itemconfigure(self.float_pill, state="normal")
            self.float_cv.itemconfigure(self.float_cost, state="normal")
            self._set_ball_cost(fmt_money_short(s["cost"]))
        except Exception:
            pass

    def _float_on_leave(self, event):
        """鼠标离开：隐藏金额文字与药丸底。"""
        try:
            self.float_cv.itemconfigure(self.float_pill, state="hidden")
            self.float_cv.itemconfigure(self.float_cost, state="hidden")
        except Exception:
            pass

    def _set_ball_cost(self, text: str):
        """设置药丸内金额文字：字号自动缩小、必要时截断，保证不超出药丸。"""
        cv = self.float_cv
        max_w = self.float_size - 16  # 药丸内宽，两侧留白防溢出
        f = tkfont.Font(font=cv.itemcget(self.float_cost, "font"))
        while f.measure(text) > max_w and f.cget("size") > 7:  # 最小 7pt，保证可读
            f.configure(size=f.cget("size") - 1)
            cv.itemconfigure(self.float_cost, font=f)
        while f.measure(text) > max_w and len(text) > 3:
            text = text[:-1]
        cv.itemconfigure(self.float_cost, text=text)

    def _float_start_drag(self, event):
        self._float_drag_off = (event.x_root - self.float_win.winfo_x(),
                                event.y_root - self.float_win.winfo_y())
        self._float_drag_start = (event.x_root, event.y_root)

    def _float_on_drag(self, event):
        self.float_win.geometry(
            f"+{event.x_root - self._float_drag_off[0]}"
            f"+{event.y_root - self._float_drag_off[1]}")

    def _float_save_pos(self, event=None):
        # 按下后几乎没移动 => 视为单击：开关使用额度面板
        if event is not None and self._float_drag_start:
            dx = abs(event.x_root - self._float_drag_start[0])
            dy = abs(event.y_root - self._float_drag_start[1])
            if dx + dy < 6:
                self._float_toggle_panel()
        self.settings["float_x"] = self.float_win.winfo_x()
        self.settings["float_y"] = self.float_win.winfo_y()
        save_settings(self.settings)
        # 圆球移动后，把已打开的面板挪回旁边
        if self._float_panel_open:
            self._float_place_panel()

    def _float_popup(self, total: int):
        """在圆球上方弹出一个 +N，并上浮淡出。"""
        cv = self.float_cv
        try:
            if not self.float_win.winfo_viewable():
                return  # 悬浮窗隐藏时不弹
        except Exception:
            return
        text = "+" + fmt_int(total)
        # 连续多个弹窗略微右移错位，避免完全重叠
        self._popups = (self._popups + 1) % 3
        x = 22 + self._popups * 8
        item = cv.create_text(x, 30, anchor="w", text=text, fill=C_PINK,
                              font=(MONO, 10, "bold"))
        # 渐隐色阶：纯粉 -> 悬浮窗奶白底色，模拟淡出（12 帧约 0.6 秒）
        steps = 12
        p0 = (0xFF, 0x69, 0xB4)  # 纯粉
        p1 = (0xFF, 0xF8, 0xEC)  # 奶白
        colors = ["#%02x%02x%02x" % tuple(
            int(p0[i] + (p1[i] - p0[i]) * (s / (steps - 1))) for i in range(3))
            for s in range(steps)]

        def animate(step):
            try:
                if step >= steps:
                    cv.delete(item)
                    return
                cv.itemconfigure(item, fill=colors[step])
                cv.move(item, 0, -2)  # 每步上浮 2 像素
                self.root.after(50, lambda: animate(step + 1))
            except Exception:
                pass

        self.root.after(50, lambda: animate(0))

    def _float_menu(self, event):
        menu = tk.Menu(self.float_win, tearoff=0)
        menu.add_command(label="打开主界面", command=self._show_main)
        menu.add_command(label="立即刷新余额", command=self._refresh_balance_now)
        menu.add_command(label="隐藏悬浮窗", command=self._hide_float)
        menu.add_separator()
        menu.add_command(label="退出程序", command=self.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_main(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

    def _hide_float(self):
        self.float_var.set(False)
        self.settings["float_window"] = False
        save_settings(self.settings)
        self.float_win.withdraw()

    # ---------- 定时刷新 ----------
    def _tick(self):
        # 1) 指标卡与悬浮窗
        try:
            s = storage.today_stats()
            total = s["cache_hit"] + s["cache_miss"] + s["completion"]
            self.card_tokens.config(text=fmt_int(total))
            self.card_tokens_sub.config(
                text=f"输入 {fmt_int(s['cache_hit'] + s['cache_miss'])} · "
                     f"输出 {fmt_int(s['completion'])} · 命中 {fmt_int(s['cache_hit'])}")
            self.card_cost.config(text=fmt_money(s["cost"]))
            self.card_cost_sub.config(text=f"共 {fmt_int(s['requests'])} 次调用")
            self.card_requests.config(text=fmt_int(s["requests"]))
            self.card_requests_sub.config(text="经本地代理统计")
            # 圆球悬停中（药丸可见）：刷新金额文字
            if self.float_cv.itemcget(self.float_cost, "state") == "normal":
                self._set_ball_cost(fmt_money_short(s["cost"]))
            # 面板打开中：刷新使用额度
            if self._float_panel_open:
                self._refresh_float_panel()
            # token 有新增时，在悬浮窗弹出 "+N" 上浮淡出动画
            new_rows = storage.new_requests_since(self._last_request_id, seconds=8)
            if new_rows:
                self._last_request_id = new_rows[-1][0]
                for _rid, added in new_rows[-6:]:  # 最多弹最近 6 条，避免刷屏
                    if added > 0:
                        self._float_popup(added)
        except Exception:
            pass

        # 2) 余额
        if self.state.get("balance") is not None:
            text = fmt_money(self.state["balance"])
            self.card_balance.config(text=text, fg=C_GREEN)
            self.card_balance_sub.config(text="官方余额接口")
        elif self.state.get("balance_error"):
            self.card_balance.config(text="获取失败", fg=C_RED)
            self.card_balance_sub.config(text="检查 api_key / 网络")
        else:
            self.card_balance.config(text="加载中...", fg=C_SUB)
            self.card_balance_sub.config(text="")
        # 面板余额行跟随余额状态
        if self._float_panel_open:
            self._refresh_float_panel()

        # 2.5) 更新检测状态（标题条横幅 + 设置页标签）
        up = self.state.get("update")
        if hasattr(self, "lbl_update_banner"):
            if up and up.get("available"):
                self.lbl_update_banner.config(text=f"发现新版本 {up['tag']} · 点此下载")
            else:
                self.lbl_update_banner.config(text="")
        if hasattr(self, "lbl_updcheck"):
            if not up:
                self.lbl_updcheck.config(text="更新检查：未检查", fg=C_SUB)
            elif up.get("available"):
                self.lbl_updcheck.config(
                    text=f"发现新版本 {up['tag']}（当前 {APP_VERSION}）· 点击下载",
                    fg=C_RED)
            elif up.get("checked_at") == "检查中...":
                self.lbl_updcheck.config(text="更新检查：检查中...", fg=C_SUB)
            else:
                self.lbl_updcheck.config(
                    text=f"已是最新版本 {APP_VERSION} · {up.get('checked_at', '')}",
                    fg=C_GREEN)

        if self.state.get("balance_updated_at"):
            try:
                t = datetime.fromisoformat(self.state["balance_updated_at"]).strftime("%H:%M:%S")
                self.lbl_balance_time.config(text=f"余额更新 {t}")
            except Exception:
                pass

        # 3) 代理状态
        if not self.config.get("proxy_enabled", True):
            self.lbl_proxy_status.config(text="代理 已关闭（设置页可重新开启）", fg=C_SUB)
        elif self.state.get("proxy_error"):
            self.lbl_proxy_status.config(text="代理 启动失败（端口被占用?）", fg=C_RED)
        elif self.state.get("proxy_ready"):
            port = self.config.get("proxy_port", 8787)
            self.lbl_proxy_status.config(text=f"代理 运行中  http://127.0.0.1:{port}", fg=C_GOLD)
        else:
            self.lbl_proxy_status.config(text="代理 启动中...", fg=C_GOLD)

        # 4) CC Switch 数据同步状态
        if hasattr(self, "lbl_ccsync"):
            cc = self.state.get("cc_sync")
            if not cc or not cc.get("enabled"):
                self.lbl_ccsync.config(text="CC Switch 同步：未启用", fg=C_SUB)
            elif cc.get("error"):
                self.lbl_ccsync.config(
                    text="CC Switch 同步：读取失败 " + str(cc["error"]), fg=C_RED)
            else:
                self.lbl_ccsync.config(
                    text=f"CC Switch 同步：运行中 · 累计导入 {fmt_int(cc.get('total_added', 0))} 条"
                         f" · {cc.get('last_time', '')}", fg=C_GREEN)

        # 4.5) Kun 数据同步状态
        if hasattr(self, "lbl_kunsync"):
            kun = self.state.get("kun_sync")
            if not kun or not kun.get("enabled"):
                self.lbl_kunsync.config(text="Kun 同步：未启用", fg=C_SUB)
            elif kun.get("error"):
                self.lbl_kunsync.config(
                    text="Kun 同步：读取失败 " + str(kun["error"]), fg=C_RED)
            else:
                self.lbl_kunsync.config(
                    text=f"Kun 同步：运行中 · 累计导入 {fmt_int(kun.get('total_added', 0))} 条"
                         f" · {kun.get('last_time', '')}", fg=C_GREEN)

        # 4.6) DeepSeek Harness 数据同步状态
        if hasattr(self, "lbl_dshsync"):
            dsh = self.state.get("dsh_sync")
            if not dsh or not dsh.get("enabled"):
                self.lbl_dshsync.config(text="Harness 同步：未启用", fg=C_SUB)
            elif dsh.get("error"):
                self.lbl_dshsync.config(
                    text="Harness 同步：读取失败 " + str(dsh["error"]), fg=C_RED)
            else:
                self.lbl_dshsync.config(
                    text=f"Harness 同步：运行中 · 累计导入 {fmt_int(dsh.get('total_added', 0))} 条"
                         f" · {dsh.get('last_time', '')}", fg=C_GREEN)

        # 4.7) YQ Harness 数据同步状态
        if hasattr(self, "lbl_yqsync"):
            yq = self.state.get("yq_sync")
            if not yq or not yq.get("enabled"):
                self.lbl_yqsync.config(text="YQ Harness 同步：未启用", fg=C_SUB)
            elif yq.get("error"):
                self.lbl_yqsync.config(
                    text="YQ Harness 同步：读取失败 " + str(yq["error"]), fg=C_RED)
            else:
                self.lbl_yqsync.config(
                    text=f"YQ Harness 同步：运行中 · 累计导入 {fmt_int(yq.get('total_added', 0))} 条"
                         f" · {yq.get('last_time', '')}", fg=C_GREEN)

        # 5) 日期与仪表盘图表
        self.lbl_date.config(text=datetime.now().strftime("%Y年%m月%d日"))
        try:
            if self.nb.index("current") == 0:
                self._draw_chart()
                self._draw_token_bar()
                self.week_card_value.config(
                    text=f"费用 {fmt_money(storage.this_week_stats()['cost'])}")
                self.month_card_value.config(
                    text=f"费用 {fmt_money(storage.this_month_stats()['cost'])}")
        except Exception:
            pass

        self.root.after(1500, self._tick)

    def _on_tab_changed(self, event=None):
        """切换页签时刷新该页数据。"""
        try:
            refresh = self._page_refreshers.get(self.nb.index("current"))
            if refresh:
                refresh()
        except Exception:
            pass

    def _refresh_balance_now(self):
        def work():
            try:
                self.state["balance"] = scheduler.fetch_balance(self.config)
                self.state["balance_error"] = None
            except Exception as exc:
                self.state["balance"] = None
                self.state["balance_error"] = str(exc)
            self.state["balance_updated_at"] = datetime.now().isoformat(timespec="seconds")
        threading.Thread(target=work, daemon=True).start()

    # ---------- 自动更新检测 ----------
    def _update_check_loop(self):
        """后台线程：启动即检查一次，之后按 config.update_check.interval_hours 周期检查。"""
        first = True
        while True:
            cfg = self.config.get("update_check") or {}
            if not cfg.get("enabled", True):
                self.stop_event.wait(3600)  # 被关闭时每小时看一眼配置
                continue
            interval = max(1, int(cfg.get("interval_hours", 6))) * 3600
            if self.stop_event.wait(0 if first else interval):
                return
            first = False
            self._run_update_check()

    def _run_update_check(self):
        """执行一次更新检查：查询 GitHub 最新 release 并写入 state（失败静默）。"""
        try:
            info = updater.check_latest()
            if not info or not info.get("tag"):
                return
            self.state["update"] = {"tag": info["tag"], "url": info["url"],
                                    "name": info.get("name") or "",
                                    "checked_at": datetime.now().strftime("%H:%M:%S")}
            if updater.is_newer(info["tag"], updater.parse_version(APP_VERSION)):
                self.state["update"]["available"] = True
                updater._log("发现新版本 %s（当前 %s）" % (info["tag"], APP_VERSION))
                self.root.after(0, self._notify_update, info)
            else:
                updater._log("已是最新版本 %s" % info["tag"])
        except Exception as exc:
            updater._log("检查异常: %s" % exc)

    def _notify_update(self, info: dict):
        """弹出新版本提示：是=前往下载；否=本次版本不再提醒。"""
        try:
            if self.settings.get("update_ignore") == info.get("tag"):
                return
            if not self.root.winfo_exists():
                return
            go = messagebox.askyesno(
                "发现新版本",
                f"发现新版本 {info.get('tag')}（当前 {APP_VERSION}）\n\n"
                f"{info.get('name') or ''}\n\n"
                "是否前往 GitHub 下载最新安装包？")
            if go:
                webbrowser.open(info.get("url") or (updater.RELEASE_URL % info.get("tag")))
            else:
                self.settings["update_ignore"] = info.get("tag")
                save_settings(self.settings)
        except Exception:
            pass

    def _check_update_now(self):
        """设置页按钮：立即检查更新。"""
        self.state["update"] = {"checked_at": "检查中..."}
        threading.Thread(target=self._run_update_check, daemon=True).start()

    def _open_update_page(self):
        """打开最新 release 下载页。"""
        info = self.state.get("update") or {}
        url = info.get("url") or (updater.RELEASE_URL % (info.get("tag") or ""))
        if url:
            webbrowser.open(url)

    def _on_close(self):
        """点关闭（×）：弹窗选择 直接关闭 或 最小化到托盘（可记住选择）。"""
        mode = self.settings.get("close_behavior")
        if mode not in ("close", "tray"):
            mode = self._ask_close_mode()
        if mode == "close":
            self.quit()
        elif mode == "tray":
            self._minimize_to_tray()

    def quit(self):
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        self._float_save_pos()
        self.stop_event.set()
        self.root.destroy()


def main():
    app = App()
    app.root.after(1000, app._tick)
    app.root.after(600, app._maybe_show_key_wizard)  # 首次无 Key 时引导填写（可跳过）
    app.root.mainloop()


if __name__ == "__main__":
    main()