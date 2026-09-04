# -*- coding: utf-8 -*-
"""水豚噜噜 · DeepSeek 用量监控 主程序。

一个完整的水豚主题桌面软件：
- 主窗口：仪表盘（今日/本周/本月、7 天费用柱状图、各模型统计、按 API Key 统计、历史快照、设置）
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
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

import cc_switch_sync
import codebuddy_sync
import dsh_sync
import pricing
import proxy_server
import scheduler
import storage
import updater
import workbuddy_sync
import yq_sync

# 当前版本（与 installer.iss 的 AppVersion 保持一致；用于自动更新检测）
APP_VERSION = "1.13.6"


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
    "peak_hours": [[9, 12], [14, 18]],
    "weekend_offpeak_since": "2026-08-23",
    "legacy_until": "2026-08-17",
    "cc_switch": {
        "enabled": True,
        "db_path": "",
        "app_types": ["codex"],
        "sync_interval_seconds": 2,
    },
    "yq": {
        "enabled": True,
        "projcache_path": "",
        "sync_interval_seconds": 5,
    },
    "dsh": {
        "enabled": True,
        "home": "",               # 留空自动定位：DSH_HOME 环境变量 > ~/.dsh
        "projcache_path": "",     # 留空自动探测：分片目录 session_projcache/ 或旧单文件
        "sync_interval_seconds": 5,
    },
    "codebuddy": {
        "enabled": True,
        "logs_dir": "",
        "sync_interval_seconds": 10,
        "model": "",
    },
    "workbuddy": {
        "enabled": True,
        "projects_dir": "",
        "sync_interval_seconds": 10,
    },
    "update_check": {
        "enabled": True,
        "interval_hours": 6,
    },
    "models": {
        "deepseek-v4-flash": {
            "note": "官网峰谷价（2026-08-17 生效）：空闲 命中0.05/未命中1.5/输出4.5；高峰 命中0.10/未命中3.0/输出9.0 元每百万tokens；8-17 之前按 legacy 平峰价",
            "cache_hit": 0.05,
            "cache_miss": 1.5,
            "output": 4.5,
            "peak": {
                "cache_hit": 0.10,
                "cache_miss": 3.0,
                "output": 9.0
            },
            "legacy": {
                "cache_hit": 0.02,
                "cache_miss": 1.0,
                "output": 2.0
            }
        },
        "deepseek-v4-pro": {
            "note": "官网峰谷价（2026-08-17 生效）：空闲 命中0.15/未命中4.5/输出13.5；高峰 命中0.30/未命中9.0/输出27.0 元每百万tokens；8-17 之前按 legacy 平峰价",
            "cache_hit": 0.15,
            "cache_miss": 4.5,
            "output": 13.5,
            "peak": {
                "cache_hit": 0.30,
                "cache_miss": 9.0,
                "output": 27.0
            },
            "legacy": {
                "cache_hit": 0.025,
                "cache_miss": 3.0,
                "output": 6.0
            }
        },
    },
}


def _merge_default_config(config: dict) -> bool:
    """把缺失的默认配置补进现有 config（老配置文件自动升级用）。

    核心：价目表。老格式条目（无 peak/legacy，即 2026-08-17 官网涨价前
    创建/生成）整体替换为新峰谷价默认值；已含 peak/legacy 的条目视为用户
    已更新过，仅补缺失子键，绝不覆盖用户自定义价格。
    返回是否发生了修改（调用方负责写回）。
    """
    changed = False
    default_models = DEFAULT_CONFIG.get("models") or {}
    models = config.setdefault("models", {})
    for model, entry in default_models.items():
        old = models.get(model)
        if old is None:
            models[model] = dict(entry)  # 全新模型：用新默认峰谷价
            changed = True
        elif not old.get("peak") and not old.get("legacy"):
            # 老格式价目（无峰谷/legacy）→ 整体替换为新默认价
            models[model] = dict(entry)
            changed = True
        else:
            # 已是新格式：只补缺失子键，保留用户自定义
            for key in ("peak", "legacy"):
                if not old.get(key) and entry.get(key):
                    old[key] = entry[key]
                    changed = True
    # 高峰窗口迁移：旧格式单段 {start_hour,end_hour}（错误窗口 9-14）→ 官方双段 [[9,12],[14,18]]
    ph = config.get("peak_hours")
    if isinstance(ph, dict):
        config["peak_hours"] = list(DEFAULT_CONFIG.get("peak_hours") or [[9, 12], [14, 18]])
        changed = True
    elif not ph:
        config["peak_hours"] = list(DEFAULT_CONFIG.get("peak_hours") or [[9, 12], [14, 18]])
        changed = True
    # 周末低谷规则补齐
    if "weekend_offpeak_since" not in config:
        config["weekend_offpeak_since"] = DEFAULT_CONFIG.get("weekend_offpeak_since")
        changed = True
    return changed


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


def _make_tree_sorter(tree: ttk.Treeview, base_titles: dict, cur_rows: list,
                      get_value, build_values, total_values=None):
    """给明细表绑定"点击列头排序"，供每日统计 / 时段统计等页共用。

    base_titles : {列ID: 列名}（插入顺序即列显示顺序）
    cur_rows    : 外部维护的原始行列表（list，用 cur_rows[:] = 新行 更新）
    get_value(col, row) : 该列可比较的原始值（数字用 int/float，日期用字符串）
    build_values(row)   : 该行显示的 values 元组（已格式化的字符串）
    total_values(rows)  : 可选；返回底部合计行的 values（合计始终沉底不参与排序）

    排序规则：日期列首次点击升序；数值列首次点击降序（先看最大）；再点反向。
    当前排序列头带方向箭头（▲ 升序 / ▼ 降序）。返回 (state, fill)：
    state 记录 {col, desc} 供外部读取；fill() 按当前排序重填表格，数据更新后调用。
    """
    state = {"col": None, "desc": False}

    def refresh_headings():
        for col in base_titles:
            arrow = ""
            if state["col"] == col:
                arrow = " ▼" if state["desc"] else " ▲"
            tree.heading(col, text=base_titles[col] + arrow)

    def fill():
        rows = cur_rows
        if state["col"]:
            key = state["col"]
            rows = sorted(cur_rows, key=lambda r: get_value(key, r),
                          reverse=state["desc"])
        for item in tree.get_children():
            tree.delete(item)
        for r in rows:
            tree.insert("", "end", values=build_values(r))
        if total_values is not None and rows:
            tree.insert("", "end", values=total_values(rows), tags=("total",))

    def sort_by(col):
        if state["col"] == col:
            state["desc"] = not state["desc"]
        else:
            state["col"] = col
            state["desc"] = col != "date"  # 数值列首击降序（先看最大）；日期列首击升序
        refresh_headings()
        fill()

    for col in base_titles:
        tree.heading(col, command=lambda c=col: sort_by(c))
    return state, fill


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
        storage.rebuild_balance_history()  # 按当前口径重算历史余额扣款/充值（幂等，升级后对齐余额走势）
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
        # 组织 Token 排名：每分钟把本机 token 增量上报到所属组织（登录后常驻，无需界面打开）
        try:
            import rank_client
            rank_client.start_reporter()
        except Exception:
            pass
        # CC Switch 数据同步线程：只读 CC Switch 本地数据库，不增加 API 链路
        threading.Thread(target=cc_switch_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()
        # YQ Harness 数据同步线程：只读 YQ 会话用量投影缓存
        threading.Thread(target=yq_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()
        # DSH Harness 数据同步线程：只读 DSH/DSH Desktop 会话用量投影缓存（分片目录或旧单文件）
        threading.Thread(target=dsh_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()
        # CodeBuddy 数据同步线程：只读 CodeBuddy 日志中的 Agent 回合用量
        threading.Thread(target=codebuddy_sync.run,
                         args=(self.config, self.settings, self.state, self.stop_event),
                         daemon=True).start()
        # WorkBuddy 数据同步线程：只读 WorkBuddy 会话 jsonl 的调用用量
        threading.Thread(target=workbuddy_sync.run,
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
        self._build_pet_window()        # 桌宠形态（设置页可切换 悬浮窗/桌宠）
        self._apply_form(self.settings.get("display_form", "float"), save=False)

        # 自动更新检测线程：启动即检查一次，之后按配置间隔周期检查
        updater.init_log(DATA_DIR)
        threading.Thread(target=self._update_check_loop, daemon=True).start()

    # ---------- 基础 ----------
    def _load_config(self) -> dict:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            # 老配置文件自动升级（价目表迁移等），有修改则写回
            try:
                if _merge_default_config(config):
                    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                        json.dump(config, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return config
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

    def _toggle_yq(self):
        """设置页：开关 YQ Harness 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("yq", {})["enabled"] = self.yq_var.get()
        self._save_config()

    def _toggle_dsh(self):
        """设置页：开关 DSH Harness 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("dsh", {})["enabled"] = self.dsh_var.get()
        self._save_config()

    def _toggle_codebuddy(self):
        """设置页：开关 CodeBuddy 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("codebuddy", {})["enabled"] = self.codebuddy_var.get()
        self._save_config()

    def _toggle_workbuddy(self):
        """设置页：开关 WorkBuddy 同步（同步线程常驻，读到配置变化后自动生效）。"""
        self.config.setdefault("workbuddy", {})["enabled"] = self.workbuddy_var.get()
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
                pystray.MenuItem("检查更新",
                                 lambda: self.root.after(0, self._tray_check_update)),
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
        # 注意：日期必须在每次刷新时重新计算（不能用启动时的 today 快照），
        # 否则程序长期运行跨过零点后，今日/本周/本月页会一直按旧日期过滤数据。
        self._page_refreshers[1] = self._add_summary_page(
            nb, "今日", 1, storage.today_stats, storage.today_breakdown,
            lambda: storage.source_breakdown(date.today(), date.today()))
        self._page_refreshers[2] = self._add_summary_page(
            nb, "本周", 2, storage.this_week_stats, storage.this_week_breakdown,
            lambda: storage.source_breakdown(storage.week_range_start(), date.today()))
        self._page_refreshers[3] = self._add_summary_page(
            nb, "本月", 3, storage.this_month_stats, storage.this_month_breakdown,
            lambda: storage.source_breakdown(date.today().replace(day=1), date.today()))
        self._page_refreshers[4] = self._add_daily_page(nb, 4)
        self._page_refreshers[5] = self._add_balance_page(nb, 5)
        self._page_refreshers[6] = self._add_period_page(nb, 6)
        self._page_refreshers[7] = self._add_key_page(nb, 7)
        self._page_refreshers[8] = self._add_model_page(nb, 8)
        self._page_refreshers[9] = self._add_history_page(nb, 9)
        self._page_refreshers[10] = self._add_settings_page(nb, 10)
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- 底部状态栏 ----
        status = tk.Frame(root, bg=C_BROWN)
        status.pack(fill="x", side="bottom")
        self.lbl_proxy_status = tk.Label(status, text="代理 启动中...", bg=C_BROWN,
                                         fg=C_GOLD, font=(FONT, 9))
        self.lbl_proxy_status.pack(side="left", padx=14, pady=4)
        # 更新检查状态（常驻可见：已是最新 / 发现新版本可点击 / 检查失败）
        self.lbl_update_status = tk.Label(status, text="更新检查中...", bg=C_BROWN,
                                          fg="#f6d9ae", font=(FONT, 9), cursor="hand2")
        self.lbl_update_status.pack(side="left", padx=(0, 14))
        self.lbl_update_status.bind("<Button-1>", lambda e: self._on_update_click())
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
        # 点击费用柱状图：查看该天的 Token 构成（点击空白处返回今日）
        self._selected_day = None
        self._bar_hits = []  # [(x1, x2, date)]：最近一次绘制的柱子命中区
        self.chart.bind("<Button-1>", self._chart_click)

        # Token 构成（今日；点击上方费用柱后切换为对应日期）
        self.lbl_token_title = tk.Label(left, text="今日 Token 构成", bg=C_BG,
                                        fg=C_BROWN_DARK, font=(FONT, 10, "bold"))
        self.lbl_token_title.pack(anchor="w", pady=(10, 2))
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

        # 近 7 天每日总金额（首页一眼看到每天花了多少钱，今天高亮）
        tk.Label(right, text="近 7 天每日总金额", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 9, "bold")).pack(anchor="w", pady=(8, 2))
        daily_card = tk.Frame(right, bg=C_CARD, highlightbackground=C_GOLD,
                              highlightthickness=1)
        daily_card.pack(fill="x", pady=(0, 8))
        self.daily_rows = []
        for _ in range(7):
            row = tk.Frame(daily_card, bg=C_CARD)
            row.pack(fill="x")
            d = tk.Label(row, text="--", bg=C_CARD, fg=C_SUB, font=(FONT, 8))
            d.pack(side="left", padx=(10, 8))
            c = tk.Label(row, text="--", bg=C_CARD, fg=C_BROWN_DARK,
                         font=(MONO, 8, "bold"))
            c.pack(side="right", padx=10)
            self.daily_rows.append((d, c))

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
        """绘制最近 7 天费用柱状图，并记录每根柱的命中区供点击查询。"""
        self._bar_hits = []
        self._draw_cost_bars(self.chart, storage.past_days_stats(7),
                             hit_cb=lambda cx, bw, d: self._bar_hits.append(
                                 (cx - bw / 2, cx + bw / 2, d)))

    @staticmethod
    def _draw_cost_bars(c, data, hit_cb=None):
        """在画布上绘制每日费用柱状图（仪表盘 7 天 / 每日统计 30 天共用）。

        hit_cb(cx, bar_w, date)：每画一根柱回调一次，供调用方记录点击命中区。
        """
        c.delete("all")
        w = c.winfo_width() or 400
        h = c.winfo_height() or 200
        pad_l, pad_r, pad_t, pad_b = 46, 10, 18, 30
        plot_w = max(w - pad_l - pad_r, 10)
        plot_h = max(h - pad_t - pad_b, 10)
        maxv = max((d["cost"] for d in data), default=0) or 1.0
        c.create_line(pad_l, h - pad_b, w - pad_r, h - pad_b, fill=C_BROWN_LIGHT)
        n = len(data)
        if n <= 0:
            c.create_text(w / 2, h / 2, text="暂无数据", fill=C_SUB, font=(FONT, 9))
            return
        slot = plot_w / n
        bar_w = max(slot * 0.55, 3)
        for i, d in enumerate(data):
            cx = pad_l + slot * i + slot / 2
            bh = d["cost"] / maxv * plot_h
            x1, y1 = cx - bar_w / 2, h - pad_b - bh
            x2, y2 = cx + bar_w / 2, h - pad_b
            c.create_rectangle(x1, y1, x2, y2, fill=C_ORANGE, outline="")
            if hit_cb:
                hit_cb(cx, bar_w, d["date"])
            # 顶部费用数字
            if d["cost"] > 0:
                c.create_text(cx, y1 - 9, text=f"{d['cost']:.2f}",
                              fill=C_BROWN_DARK, font=(MONO, 7))
            # 日期 MM-DD（柱多时隔一个标一个，避免重叠）
            if n <= 20 or i % 2 == 0:
                c.create_text(cx, h - pad_b + 12, text=d["date"][5:],
                              fill=C_SUB, font=(FONT, 8))
        c.create_text(pad_l, 10, anchor="w", text="元/日", fill=C_SUB, font=(FONT, 8))

    def _chart_click(self, event):
        """点击费用柱状图：命中某根柱子→下方 Token 构成切换为该天；点空白→返回今日。"""
        try:
            x = event.x
            target = None
            for x1, x2, d in getattr(self, "_bar_hits", []) or []:
                if x1 <= x <= x2:
                    target = d
                    break
            self._select_day(target)
        except Exception:
            pass

    def _select_day(self, day):
        """把下方 Token 构成区切到指定日期（None=今日），并更新标题。"""
        self._selected_day = day
        self._draw_token_bar()
        if day:
            try:
                m, dd = int(day[5:7]), int(day[8:10])
                label = f"{m}月{dd}日"
            except Exception:
                label = day
            self.lbl_token_title.config(text=f"{label} Token 构成（点击空白返回今日）",
                                        fg=C_ORANGE_DEEP)
        else:
            self.lbl_token_title.config(text="今日 Token 构成", fg=C_BROWN_DARK)

    def _refresh_daily_list(self):
        """刷新仪表盘右侧"近 7 天每日总金额"列表（最新在顶，今天高亮）。"""
        try:
            data = list(reversed(storage.past_days_stats(7)))
            for i, (d_lbl, c_lbl) in enumerate(self.daily_rows):
                if i < len(data):
                    d = data[i]
                    d_lbl.config(text=d["date"][5:])
                    c_lbl.config(text=fmt_money(d["cost"]))
                    if i == 0:
                        c_lbl.config(fg=C_ORANGE_DEEP)
                        d_lbl.config(fg=C_BROWN_DARK, font=(FONT, 8, "bold"))
                    else:
                        c_lbl.config(fg=C_BROWN_DARK)
                        d_lbl.config(fg=C_SUB, font=(FONT, 8))
                else:
                    d_lbl.config(text="--")
                    c_lbl.config(text="--")
        except Exception:
            pass

    def _draw_token_bar(self):
        """绘制 Token 构成横向比例条：默认今日，点击费用柱后显示所选日期。"""
        c = self.bar
        c.delete("all")
        day = getattr(self, "_selected_day", None)
        if day:
            s = storage.day_stats(day)
            empty_text = "该日暂无调用"
        else:
            s = storage.today_stats()
            empty_text = "今日暂无调用"
        w = c.winfo_width() or 300
        h = c.winfo_height() or 28
        total = s["cache_hit"] + s["cache_miss"] + s["completion"]
        if total <= 0:
            c.create_text(w / 2, h / 2, text=empty_text, fill=C_SUB, font=(FONT, 9))
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
    def _add_summary_page(self, nb, title, index, agg_fn, brk_fn, src_fn):
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

        # 左右两栏：左=按模型，右=按客户端来源
        mid = tk.Frame(page, bg=C_BG)
        mid.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        mid.grid_columnconfigure(0, weight=3)
        mid.grid_columnconfigure(1, weight=2)
        tk.Label(mid, text="按模型", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        tk.Label(mid, text="按客户端", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).grid(row=0, column=1, sticky="w", pady=(0, 4))
        tree = self._make_model_tree(mid)
        tree.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        stree = self._make_source_tree(mid)
        stree.grid(row=1, column=1, sticky="nsew")

        def refresh():
            s = agg_fn()
            b = brk_fn()
            sb = src_fn()
            lbl_requests.config(text=f"请求数  {fmt_int(s['requests'])}")
            lbl_input.config(text=f"输入  {fmt_int(s['cache_hit'] + s['cache_miss'])}"
                                   f"（命中 {fmt_int(s['cache_hit'])}）")
            lbl_output.config(text=f"输出  {fmt_int(s['completion'])}")
            lbl_cost.config(text=f"费用  {fmt_money(s['cost'])}")
            self._fill_model_tree(tree, b)
            self._fill_source_tree(stree, sb)

        refresh()
        return refresh

    # ---------- 每日统计页 ----------
    def _add_daily_page(self, nb, index):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="每日统计")

        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", padx=12, pady=8)
        lbl_days = tk.Label(top, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        lbl_days.grid(row=0, column=0, sticky="w", padx=(0, 16))
        lbl_req = tk.Label(top, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        lbl_req.grid(row=0, column=1, sticky="w", padx=(0, 16))
        lbl_tok = tk.Label(top, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        lbl_tok.grid(row=0, column=2, sticky="w", padx=(0, 16))
        lbl_cost = tk.Label(top, text="", bg=C_BG, fg=C_ORANGE_DEEP, font=(FONT, 10, "bold"))
        lbl_cost.grid(row=0, column=3, sticky="w")

        tk.Label(page, text="近 30 天每日费用", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        chart = tk.Canvas(page, height=170, bg=C_CARD, highlightthickness=1,
                          highlightbackground=C_BROWN_LIGHT)
        chart.pack(fill="x", padx=12, pady=(0, 8))

        tree = ttk.Treeview(page, columns=("date", "req", "in", "out", "hit", "tok", "cost"),
                            show="headings")
        base_titles = {"date": "日期", "req": "请求数", "in": "输入", "out": "输出",
                       "hit": "命中", "tok": "Token 合计", "cost": "费用"}
        for col, text, width in (("date", "日期", 100), ("req", "请求数", 70),
                                 ("in", "输入", 90), ("out", "输出", 90),
                                 ("hit", "命中", 90), ("tok", "Token 合计", 110),
                                 ("cost", "费用", 100)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="center" if col != "date" else "w")
        tree.tag_configure("total", background="#fdf0d8", foreground=C_ORANGE_DEEP)
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # ---- 点击列头排序（共享组件，见 _make_tree_sorter）----
        cur_rows = []  # 最近一次查询的原始行（排序只作用于表格展示）

        def col_value(col, r):
            if col == "date":
                return r["date"]
            if col == "req":
                return r["requests"]
            if col == "in":
                return r["cache_hit"] + r["cache_miss"]
            if col == "out":
                return r["completion"]
            if col == "hit":
                return r["cache_hit"]
            if col == "tok":
                return r["tokens"]
            return r["cost"]  # cost

        def build_values(r):
            return (r["date"], fmt_int(r["requests"]),
                    fmt_int(r["cache_hit"] + r["cache_miss"]),
                    fmt_int(r["completion"]), fmt_int(r["cache_hit"]),
                    fmt_int(r["tokens"]), fmt_money(r["cost"]))

        def total_values(rows):
            return ("合计", fmt_int(sum(r["requests"] for r in rows)),
                    fmt_int(sum(r["cache_hit"] + r["cache_miss"] for r in rows)),
                    fmt_int(sum(r["completion"] for r in rows)),
                    fmt_int(sum(r["cache_hit"] for r in rows)),
                    fmt_int(sum(r["tokens"] for r in rows)),
                    fmt_money(sum(r["cost"] for r in rows)))

        sort_state, fill_tree = _make_tree_sorter(
            tree, base_titles, cur_rows, col_value, build_values, total_values)

        def refresh():
            cur_rows[:] = storage.daily_stats()
            lbl_days.config(text=f"记录天数  {fmt_int(len(cur_rows))}")
            lbl_req.config(text=f"累计请求  {fmt_int(sum(r['requests'] for r in cur_rows))}")
            lbl_tok.config(text=f"累计 Token  {fmt_int(sum(r['tokens'] for r in cur_rows))}")
            lbl_cost.config(text=f"累计费用  {fmt_money(sum(r['cost'] for r in cur_rows))}")
            self._draw_cost_bars(chart, storage.past_days_stats(30))
            fill_tree()

        refresh()
        return refresh

    # ---------- 余额统计页 ----------
    def _add_balance_page(self, nb, index):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="余额统计")

        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", padx=12, pady=8)
        self.lbl_bal_cur = tk.Label(top, text="", bg=C_BG, fg=C_GREEN,
                                    font=(FONT, 10, "bold"))
        self.lbl_bal_cur.grid(row=0, column=0, sticky="w", padx=(0, 16))
        lbl_days = tk.Label(top, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        lbl_days.grid(row=0, column=1, sticky="w", padx=(0, 16))
        tk.Button(top, text="记录充值", command=self._record_recharge_dialog,
                  bg=C_CARD, fg=C_TEXT, font=(FONT, 9), relief="groove", bd=1,
                  takefocus=0).grid(row=0, column=3, sticky="e")
        tk.Label(top, text="余额每日快照：刷新成功自动记录，一天一条；充值=当日到账(可手动补记)，扣款=当日余额实际减少", bg=C_BG,
                 fg=C_SUB, font=(FONT, 8)).grid(row=1, column=0, columnspan=4, sticky="w")

        tk.Label(page, text="近 30 天余额走势", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        chart = tk.Canvas(page, height=180, bg=C_CARD, highlightthickness=1,
                          highlightbackground=C_BROWN_LIGHT)
        chart.pack(fill="x", padx=12, pady=(0, 8))

        tree = ttk.Treeview(page, columns=("date", "bal", "recharge", "cost", "upd"),
                            show="headings")
        for col, text, width in (("date", "日期", 110), ("bal", "余额(元)", 130),
                                 ("recharge", "充值(元)", 130), ("cost", "扣款(元)", 130),
                                 ("upd", "更新时间", 170)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="center" if col != "date" else "w")
        # 充值列绿色（+），扣款列红色（-）
        # ttk.Treeview 单元格着色：tag 形如 "<列名>:<tag名>"
        tree.tag_configure("recharge_g", foreground=C_GREEN)
        tree.tag_configure("cost_r", foreground=C_RED)
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        def refresh():
            rows = storage.balance_history()  # 一次取全量（倒序），图表复用前 30 天切片
            self._update_balance_cur_label()
            lbl_days.config(text=f"记录天数  {fmt_int(len(rows))}")
            self._draw_balance_chart(chart, list(reversed(rows[:30])))
            for item in tree.get_children():
                tree.delete(item)
            for i, r in enumerate(rows):
                rc = r["recharge"] or 0.0
                cost = r["usage_cost"] or 0.0
                tags = []
                if rc > 1e-9:
                    tags.append("recharge:recharge_g")
                if cost > 1e-9:
                    tags.append("cost:cost_r")
                tree.insert("", "end", iid="row%d" % i, values=(
                    r["date"], "%.4f" % r["balance"],
                    ("+%.4f" % rc) if rc > 1e-9 else "--",
                    ("-%.4f" % cost) if cost > 1e-9 else "--",
                    r["updated_at"][11:16] if len(r["updated_at"]) >= 16 else r["updated_at"]),
                    tags=tuple(tags))

        refresh()
        return refresh

    def _record_recharge_dialog(self):
        """弹出"记录充值"输入框：日期（默认今天）+ 金额，写入 balance_history 并刷新本页。"""
        dialog = tk.Toplevel(self.root)
        dialog.title("记录充值")
        dialog.configure(bg=C_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        frm = tk.Frame(dialog, bg=C_BG, padx=16, pady=12)
        frm.pack()
        tk.Label(frm, text="充值日期 (YYYY-MM-DD)", bg=C_BG, fg=C_TEXT,
                 font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        ent_date = tk.Entry(frm, width=16, font=(FONT, 9))
        ent_date.grid(row=0, column=1, padx=(8, 0))
        ent_date.insert(0, date.today().isoformat())
        tk.Label(frm, text="充值金额 (元，正数=入账，负数=冲正)",
                 bg=C_BG, fg=C_TEXT, font=(FONT, 9)).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ent_amt = tk.Entry(frm, width=16, font=(FONT, 9))
        ent_amt.grid(row=1, column=1, padx=(8, 0), pady=(8, 0))
        tk.Label(frm, text="提示：官方无免登录充值接口，充值只能手动补记；\n保存后会按「扣款=昨日余额-今日余额+充值」重算当日扣款。",
                 bg=C_BG, fg=C_SUB, font=(FONT, 8), justify="left").grid(
                 row=2, column=0, columnspan=2, sticky="w", pady=(10, 4))

        def submit():
            try:
                d = ent_date.get().strip()
                datetime.strptime(d, "%Y-%m-%d")  # 校验日期格式
                amt = float(ent_amt.get().strip())
            except Exception:
                messagebox.showwarning("参数错误", "请填写正确的日期(YYYY-MM-DD)与金额(数字)。",
                                       parent=dialog)
                return
            try:
                storage.record_recharge(d, amt)
            except Exception as exc:
                messagebox.showerror("保存失败", f"写入失败：\n{exc}", parent=dialog)
                return
            dialog.destroy()
            # 刷新本页
            refresh = self._page_refreshers.get(self.nb.index("current"))
            if refresh:
                refresh()

        btns = tk.Frame(frm, bg=C_BG)
        btns.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        tk.Button(btns, text="取消", command=dialog.destroy, bg=C_CARD, fg=C_TEXT,
                  font=(FONT, 9), relief="groove", bd=1).pack(side="left", padx=(0, 8))
        tk.Button(btns, text="保存", command=submit, bg=C_CARD, fg=C_TEXT,
                  font=(FONT, 9), relief="groove", bd=1).pack(side="left")
        ent_amt.focus_set()

    def _update_balance_cur_label(self):
        """刷新余额统计页"当前余额"（设置页/悬浮窗余额变化时同步）。"""
        try:
            if not hasattr(self, "lbl_bal_cur"):
                return
            if self.state.get("balance") is not None:
                self.lbl_bal_cur.config(text=f"当前余额  {fmt_money(self.state['balance'])}",
                                        fg=C_GREEN)
            elif self.state.get("balance_error"):
                self.lbl_bal_cur.config(text="当前余额  获取失败", fg=C_RED)
            else:
                self.lbl_bal_cur.config(text="当前余额  加载中...", fg=C_SUB)
        except Exception:
            pass

    @staticmethod
    def _draw_balance_chart(c, data):
        """近 30 天余额走势折线图（data 为按日期升序的余额列表）。"""
        c.delete("all")
        w = c.winfo_width() or 400
        h = c.winfo_height() or 180
        pad_l, pad_r, pad_t, pad_b = 46, 10, 18, 26
        plot_w = max(w - pad_l - pad_r, 10)
        plot_h = max(h - pad_t - pad_b, 10)
        n = len(data)
        if n == 0:
            c.create_text(w / 2, h / 2,
                          text="暂无余额记录（配置 api_key 并刷新余额后自动记录）",
                          fill=C_SUB, font=(FONT, 9))
            return
        vals = [d["balance"] for d in data]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            hi = lo + 1.0
        c.create_line(pad_l, h - pad_b, w - pad_r, h - pad_b, fill=C_BROWN_LIGHT)
        pts = []
        for i, d in enumerate(data):
            x = pad_l + plot_w * i / (n - 1) if n > 1 else pad_l + plot_w / 2
            y = h - pad_b - (d["balance"] - lo) / (hi - lo) * plot_h
            pts.append((x, y))
        if len(pts) >= 2:  # 单点时不画线（画布要求至少 4 个坐标）
            c.create_line([v for pt in pts for v in pt], fill=C_GREEN, width=2,
                          smooth=True)
        label_every = max(1, n // 6)
        for i, (x, y) in enumerate(pts):
            c.create_oval(x - 2, y - 2, x + 2, y + 2, fill=C_GREEN, outline="")
            if i % label_every == 0 or i == n - 1:
                c.create_text(x, y - 9, text=f"{data[i]['balance']:.2f}",
                              fill=C_BROWN_DARK, font=(MONO, 7))
            c.create_text(x, h - pad_b + 12, text=data[i]["date"][5:],
                          fill=C_SUB, font=(FONT, 8))
        c.create_text(pad_l, 10, anchor="w", text="余额/元", fill=C_SUB, font=(FONT, 8))

    # ---------- 时段统计页（高峰/非高峰） ----------
    def _add_period_page(self, nb, index):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="时段统计")

        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", padx=12, pady=8)
        self.lbl_peak_today = tk.Label(top, text="", bg=C_BG, fg=C_ORANGE_DEEP,
                                       font=(FONT, 9))
        self.lbl_peak_today.grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.lbl_off_today = tk.Label(top, text="", bg=C_BG, fg=C_GREEN, font=(FONT, 9))
        self.lbl_off_today.grid(row=0, column=1, sticky="w", padx=(0, 14))
        self.lbl_peak_month = tk.Label(top, text="", bg=C_BG, fg=C_ORANGE_DEEP,
                                       font=(FONT, 9))
        self.lbl_peak_month.grid(row=0, column=2, sticky="w", padx=(0, 14))
        self.lbl_off_month = tk.Label(top, text="", bg=C_BG, fg=C_GREEN, font=(FONT, 9))
        self.lbl_off_month.grid(row=0, column=3, sticky="w")

        tk.Label(page, text="近 30 天 高峰/非高峰 费用（工作日高峰 9:00-12:00 与 14:00-18:00，周末全天低谷价）",
                 bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        chart = tk.Canvas(page, height=180, bg=C_CARD, highlightthickness=1,
                          highlightbackground=C_BROWN_LIGHT)
        chart.pack(fill="x", padx=12, pady=(0, 4))
        legend = tk.Frame(page, bg=C_BG)
        legend.pack(fill="x", padx=12)
        tk.Label(legend, text="● 高峰费用", bg=C_BG, fg=C_ORANGE_DEEP,
                 font=(FONT, 8)).pack(side="left", padx=(0, 12))
        tk.Label(legend, text="● 非高峰费用", bg=C_BG, fg=C_GREEN,
                 font=(FONT, 8)).pack(side="left")

        tree = ttk.Treeview(page, columns=("date", "pr", "pt", "pc", "or", "ot", "oc"),
                            show="headings")
        base_titles = {"date": "日期", "pr": "高峰请求", "pt": "高峰Token",
                       "pc": "高峰费用", "or": "非高峰请求", "ot": "非高峰Token",
                       "oc": "非高峰费用"}
        for col, text, width in (("date", "日期", 95), ("pr", "高峰请求", 80),
                                 ("pt", "高峰Token", 95), ("pc", "高峰费用", 90),
                                 ("or", "非高峰请求", 80), ("ot", "非高峰Token", 95),
                                 ("oc", "非高峰费用", 95)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="center" if col != "date" else "w")
        tree.tag_configure("total", background="#fdf0d8", foreground=C_ORANGE_DEEP)
        tree.pack(fill="both", expand=True, padx=12, pady=(8, 12))

        # ---- 点击列头排序（共享组件，见 _make_tree_sorter）----
        cur_rows = []

        def col_value(col, r):
            return r["date"] if col == "date" else r[{
                "pr": "peak_requests", "pt": "peak_tokens", "pc": "peak_cost",
                "or": "off_requests", "ot": "off_tokens", "oc": "off_cost"}[col]]

        def build_values(r):
            return (r["date"], fmt_int(r["peak_requests"]), fmt_int(r["peak_tokens"]),
                    fmt_money(r["peak_cost"]), fmt_int(r["off_requests"]),
                    fmt_int(r["off_tokens"]), fmt_money(r["off_cost"]))

        def total_values(rows):
            return ("合计", fmt_int(sum(r["peak_requests"] for r in rows)),
                    fmt_int(sum(r["peak_tokens"] for r in rows)),
                    fmt_money(sum(r["peak_cost"] for r in rows)),
                    fmt_int(sum(r["off_requests"] for r in rows)),
                    fmt_int(sum(r["off_tokens"] for r in rows)),
                    fmt_money(sum(r["off_cost"] for r in rows)))

        sort_state, fill_tree = _make_tree_sorter(
            tree, base_titles, cur_rows, col_value, build_values, total_values)

        def refresh():
            cur_rows[:] = storage.period_stats(config=self.config)  # 全量（倒序），图表复用近 30 天切片
            today = date.today().isoformat()
            month = date.today().strftime("%Y-%m")
            p_today = next((r for r in cur_rows if r["date"] == today), None)
            mrows = [r for r in cur_rows if r["date"].startswith(month)]

            def msum(key):
                return sum(r[key] for r in mrows)

            self.lbl_peak_today.config(
                text=f"今日高峰  {fmt_money(p_today['peak_cost'] if p_today else 0)}")
            self.lbl_off_today.config(
                text=f"今日非高峰  {fmt_money(p_today['off_cost'] if p_today else 0)}")
            self.lbl_peak_month.config(text=f"本月高峰  {fmt_money(msum('peak_cost'))}")
            self.lbl_off_month.config(text=f"本月非高峰  {fmt_money(msum('off_cost'))}")
            # 图表只需升序的最近 30 天：直接切片，避免对全表再做一次分时段统计
            start30 = (date.today() - timedelta(days=29)).isoformat()
            chart_rows = [r for r in cur_rows if r["date"] >= start30]
            self._draw_period_bars(chart, list(reversed(chart_rows)))
            fill_tree()

        refresh()
        return refresh

    @staticmethod
    def _draw_period_bars(c, data):
        """近 30 天 高峰/非高峰 费用堆叠柱状图（data 升序，上=高峰 下=非高峰）。"""
        c.delete("all")
        w = c.winfo_width() or 400
        h = c.winfo_height() or 180
        pad_l, pad_r, pad_t, pad_b = 46, 10, 18, 26
        plot_w = max(w - pad_l - pad_r, 10)
        plot_h = max(h - pad_t - pad_b, 10)
        n = len(data)
        if n <= 0:
            c.create_text(w / 2, h / 2, text="暂无数据", fill=C_SUB, font=(FONT, 9))
            return
        maxv = max((d["peak_cost"] + d["off_cost"] for d in data), default=0) or 1.0
        c.create_line(pad_l, h - pad_b, w - pad_r, h - pad_b, fill=C_BROWN_LIGHT)
        slot = plot_w / n
        bar_w = max(slot * 0.55, 3)
        for i, d in enumerate(data):
            cx = pad_l + slot * i + slot / 2
            y_base = h - pad_b
            bh_p = d["peak_cost"] / maxv * plot_h
            bh_o = d["off_cost"] / maxv * plot_h
            c.create_rectangle(cx - bar_w / 2, y_base - bh_p - bh_o,
                               cx + bar_w / 2, y_base - bh_o, fill=C_ORANGE_DEEP, outline="")
            c.create_rectangle(cx - bar_w / 2, y_base - bh_o,
                               cx + bar_w / 2, y_base, fill=C_GREEN, outline="")
            total = d["peak_cost"] + d["off_cost"]
            if total > 0:
                c.create_text(cx, y_base - bh_p - bh_o - 8, text=f"{total:.2f}",
                              fill=C_BROWN_DARK, font=(MONO, 7))
            if n <= 20 or i % 2 == 0:
                c.create_text(cx, h - pad_b + 12, text=d["date"][5:],
                              fill=C_SUB, font=(FONT, 8))
        c.create_text(pad_l, 10, anchor="w", text="元/日（上=高峰 下=非高峰）",
                      fill=C_SUB, font=(FONT, 8))

    # ---------- API Key 统计页 ----------
    def _add_key_page(self, nb, index):
        page = tk.Frame(nb, bg=C_BG)
        nb.add(page, text="Key 统计")

        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", padx=12, pady=8)
        self.lbl_key_today = tk.Label(top, text="", bg=C_BG, fg=C_ORANGE_DEEP,
                                      font=(FONT, 9))
        self.lbl_key_today.grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.lbl_key_month = tk.Label(top, text="", bg=C_BG, fg=C_GREEN, font=(FONT, 9))
        self.lbl_key_month.grid(row=0, column=1, sticky="w", padx=(0, 14))
        tk.Label(top, text="仅统计经本地代理 127.0.0.1:8787 的流量（按 Authorization 头识别 Key，"
                           "只存指纹不存明文）", bg=C_BG, fg=C_SUB,
                 font=(FONT, 8)).grid(row=0, column=2, sticky="w")

        tk.Label(page, text="近 30 天 各 Key 费用", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        chart = tk.Canvas(page, height=180, bg=C_CARD, highlightthickness=1,
                          highlightbackground=C_BROWN_LIGHT)
        chart.pack(fill="x", padx=12, pady=(0, 8))

        tk.Label(page, text="本月各 Key", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        tree_month = self._make_key_tree(page)
        tree_month.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        tk.Label(page, text="今日各 Key", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        tree_today = self._make_key_tree(page)
        tree_today.pack(fill="x", padx=12, pady=(0, 12))

        def refresh():
            today = date.today().isoformat()
            month_start = date.today().replace(day=1).isoformat()
            b_today = storage.key_breakdown(today, today)
            b_month = storage.key_breakdown(month_start, today)
            self.lbl_key_today.config(
                text=f"今日代理费用  {fmt_money(sum(r['cost'] for r in b_today))}")
            self.lbl_key_month.config(
                text=f"本月代理费用  {fmt_money(sum(r['cost'] for r in b_month))}")
            self._draw_key_bars(chart, storage.key_daily_stats(30))
            self._fill_key_tree(tree_month, b_month)
            self._fill_key_tree(tree_today, b_today)

        refresh()
        return refresh

    def _make_key_tree(self, parent):
        tree = ttk.Treeview(parent, columns=("key", "req", "in", "out", "cost"),
                            show="headings")
        for col, text, width in (("key", "API Key", 210), ("req", "请求数", 90),
                                 ("in", "输入token", 110), ("out", "输出token", 110),
                                 ("cost", "费用", 110)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="center" if col != "key" else "w")
        tree.tag_configure("total", background="#fdf0d8", foreground=C_ORANGE_DEEP)
        return tree

    @staticmethod
    def _fill_key_tree(tree, rows):
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tree.insert("", "end", values=(
                row["key_hint"], fmt_int(row["requests"]),
                fmt_int(row["cache_hit"] + row["cache_miss"]),
                fmt_int(row["completion"]), fmt_money(row["cost"])))
        if rows:
            tree.insert("", "end", values=(
                "合计", fmt_int(sum(r["requests"] for r in rows)),
                fmt_int(sum(r["cache_hit"] + r["cache_miss"] for r in rows)),
                fmt_int(sum(r["completion"] for r in rows)),
                fmt_money(sum(r["cost"] for r in rows))), tags=("total",))

    @staticmethod
    def _draw_key_bars(c, data):
        """近 30 天各 Key 费用堆叠柱状图（data 升序；Key 颜色按 30 天累计费用降序分配）。"""
        c.delete("all")
        w = c.winfo_width() or 400
        h = c.winfo_height() or 180
        pad_l, pad_r, pad_t, pad_b = 46, 10, 18, 26
        plot_w = max(w - pad_l - pad_r, 10)
        plot_h = max(h - pad_t - pad_b, 10)
        n = len(data)
        if n <= 0:
            c.create_text(w / 2, h / 2, text="暂无数据", fill=C_SUB, font=(FONT, 9))
            return
        totals = {}
        for d in data:
            for hint, cost in d["keys"]:
                totals[hint] = totals.get(hint, 0) + cost
        palette = ("#b5651d", "#e67e22", "#f1c40f", "#27ae60", "#5b8fc9",
                   "#9b59b6", "#e74c3c", "#16a085", "#8a6a4d", "#d35400")
        colors = {hint: palette[i % len(palette)]
                  for i, hint in enumerate(sorted(totals, key=totals.get, reverse=True))}
        maxv = max((sum(cost for _, cost in d["keys"]) for d in data), default=0) or 1.0
        c.create_line(pad_l, h - pad_b, w - pad_r, h - pad_b, fill=C_BROWN_LIGHT)
        slot = plot_w / n
        bar_w = max(slot * 0.55, 3)
        for i, d in enumerate(data):
            cx = pad_l + slot * i + slot / 2
            y = h - pad_b
            for hint, cost in d["keys"]:
                bh = cost / maxv * plot_h
                c.create_rectangle(cx - bar_w / 2, y - bh, cx + bar_w / 2, y,
                                   fill=colors.get(hint, C_BROWN_LIGHT), outline="")
                y -= bh
            total = sum(cost for _, cost in d["keys"])
            if total > 0:
                c.create_text(cx, y - 8, text=f"{total:.2f}", fill=C_BROWN_DARK,
                              font=(MONO, 7))
            if n <= 20 or i % 2 == 0:
                c.create_text(cx, h - pad_b + 12, text=d["date"][5:],
                              fill=C_SUB, font=(FONT, 8))
        c.create_text(pad_l, 10, anchor="w", text="元/日（各 Key 堆叠）",
                      fill=C_SUB, font=(FONT, 8))

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

    def _make_source_tree(self, parent):
        tree = ttk.Treeview(parent, columns=("source", "req", "input", "output", "cost"),
                            show="headings")
        for col, text, width in (("source", "客户端", 150), ("req", "请求数", 80),
                                 ("input", "输入token", 110), ("output", "输出token", 110),
                                 ("cost", "费用", 110)):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="center" if col != "source" else "w")
        return tree

    def _fill_source_tree(self, tree, rows):
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tree.insert("", "end", values=(
                row["source"], fmt_int(row["requests"]),
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

        # 桌面挂件：总开关 + 形态（悬浮窗 / 桌宠）二选一
        self.float_var = tk.BooleanVar(value=bool(self.settings.get("float_window", True)))
        ttk.Checkbutton(left, text="显示桌面挂件", variable=self.float_var,
                        command=self._toggle_float).pack(anchor="w", pady=(0, 4))
        self.form_var = tk.StringVar(value=self.settings.get("display_form", "float"))
        form_row = tk.Frame(left, bg=C_BG)
        form_row.pack(anchor="w", pady=(0, 10))
        ttk.Radiobutton(form_row, text="悬浮窗", value="float", variable=self.form_var,
                        command=lambda: self._toggle_form("float")).pack(side="left", padx=(0, 8))
        ttk.Radiobutton(form_row, text="桌宠", value="pet", variable=self.form_var,
                        command=lambda: self._toggle_form("pet")).pack(side="left")

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

        # 数据源开关（六个数据源全部保留，可分别开关，修改即时生效）
        tk.Label(left, text="数据源开关:", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", pady=(0, 4))
        self.proxy_var = tk.BooleanVar(value=bool(self.config.get("proxy_enabled", True)))
        ttk.Checkbutton(left, text="本地代理 (127.0.0.1:8787)", variable=self.proxy_var,
                        command=self._toggle_proxy).pack(anchor="w", pady=(0, 4))
        self.cc_var = tk.BooleanVar(
            value=bool((self.config.get("cc_switch") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="CC Switch 同步", variable=self.cc_var,
                        command=self._toggle_cc).pack(anchor="w", pady=(0, 4))
        self.yq_var = tk.BooleanVar(
            value=bool((self.config.get("yq") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="YQ Harness 同步", variable=self.yq_var,
                        command=self._toggle_yq).pack(anchor="w", pady=(0, 4))
        self.dsh_var = tk.BooleanVar(
            value=bool((self.config.get("dsh") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="DSH Harness 同步", variable=self.dsh_var,
                        command=self._toggle_dsh).pack(anchor="w", pady=(0, 4))
        self.codebuddy_var = tk.BooleanVar(
            value=bool((self.config.get("codebuddy") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="CodeBuddy 同步", variable=self.codebuddy_var,
                        command=self._toggle_codebuddy).pack(anchor="w", pady=(0, 4))
        self.workbuddy_var = tk.BooleanVar(
            value=bool((self.config.get("workbuddy") or {}).get("enabled", True)))
        ttk.Checkbutton(left, text="WorkBuddy 同步", variable=self.workbuddy_var,
                        command=self._toggle_workbuddy).pack(anchor="w", pady=(0, 10))

        # 代理状态
        self.lbl_setting_proxy = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_setting_proxy.pack(anchor="w", pady=(0, 6))
        # CC Switch 数据同步状态
        self.lbl_ccsync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_ccsync.pack(anchor="w", pady=(0, 6))
        # YQ Harness 数据同步状态
        self.lbl_yqsync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_yqsync.pack(anchor="w", pady=(0, 6))
        # DSH Harness 数据同步状态
        self.lbl_dshsync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_dshsync.pack(anchor="w", pady=(0, 6))
        # CodeBuddy 数据同步状态
        self.lbl_codebuddysync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_codebuddysync.pack(anchor="w", pady=(0, 6))
        # WorkBuddy 数据同步状态
        self.lbl_workbuddysync = tk.Label(left, text="", bg=C_BG, fg=C_TEXT, font=(FONT, 10))
        self.lbl_workbuddysync.pack(anchor="w", pady=(0, 12))

        # 操作按钮
        ttk.Button(left, text="立即刷新余额", command=self._refresh_balance_now).pack(
            anchor="w", fill="x", pady=2)
        ttk.Button(left, text="检查更新", command=self._check_update_now).pack(
            anchor="w", fill="x", pady=2)
        # 更新检查状态（点击"发现新版本"可直接下载）
        self.lbl_updcheck = tk.Label(left, text="", bg=C_BG, fg=C_SUB, font=(FONT, 9),
                                     cursor="hand2")
        self.lbl_updcheck.pack(anchor="w", pady=(0, 6))
        self.lbl_updcheck.bind("<Button-1>", lambda e: self._on_update_click())
        ttk.Button(left, text="按最新峰谷价重算历史费用", command=self._rebill_all).pack(
            anchor="w", fill="x", pady=2)
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
        """总开关：显示/隐藏当前形态的桌面挂件（悬浮窗或桌宠）。"""
        self.settings["float_window"] = bool(self.float_var.get())
        save_settings(self.settings)
        self._apply_form(self.settings.get("display_form", "float"), save=False)

    def _toggle_form(self, form):
        """设置页：切换 悬浮窗 / 桌宠 形态（互斥，只显示选中的一种）。"""
        self.settings["display_form"] = form
        save_settings(self.settings)
        self._apply_form(form)

    def _apply_form(self, form, save=True):
        """按形态与总开关显示/隐藏悬浮窗与桌宠窗口。form ∈ {"float","pet"}。"""
        if save:
            self.settings["display_form"] = form
            save_settings(self.settings)
        show = bool(self.settings.get("float_window", True))
        # 隐藏未选中的形态，关闭其面板
        if form == "pet":
            if getattr(self, "float_win", None):
                self.float_win.withdraw()
            self._pet_set_visible(show)
        else:
            if getattr(self, "pet_win", None):
                self.pet_win.withdraw()
            if getattr(self, "float_win", None):
                if show:
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
        IMG = 48     # 噜噜头像尺寸（Pillow 精确缩放），圆圈与图片完全等大
        TEXT_H = 15  # 圆球上方的悬停金额文字区（透明，仅药丸与文字可见）
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
        pill_h = 14
        pill_pts = rounded_rect_points(0, 0, w, pill_h, 7)
        self.float_pill = cv.create_polygon(
            [c for pt in pill_pts for c in pt], smooth=False,
            fill=C_BROWN_DARK, outline=C_ORANGE, width=1, state="hidden")
        self.float_cost = cv.create_text(w / 2, pill_h / 2, text="", fill=C_GREEN_DEEP,
                                         font=(MONO, 8, "bold"), state="hidden")

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
        max_w = self.float_size - 8  # 药丸内宽，两侧留白防溢出
        f = tkfont.Font(font=cv.itemcget(self.float_cost, "font"))
        while f.measure(text) > max_w and f.cget("size") > 6:  # 最小 6pt，保证可读
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
        """在悬浮球正上方弹出一个独立的 +N 气泡，上浮淡出。

        之前在悬浮球 48x48 画布上画文字，数字稍长（几万 token）就被画布裁断
        且和噜噜头像叠在一起，用户完全看不到。现改为独立 Toplevel 弹窗：
        宽 110px 足够显示任何长度的 +N，定位在悬浮球正上方居中，深蓝描边 +
        浅蓝主文字清晰可读，整体上浮淡出。
        """
        try:
            if not self.float_win.winfo_viewable():
                return  # 悬浮窗隐藏时不弹
        except Exception:
            return
        text = "+" + fmt_int(total)
        # 连续多个弹窗略微错位（垂直错开），避免完全重叠
        self._popups = (self._popups + 1) % 3
        bx, by = self.float_win.winfo_rootx(), self.float_win.winfo_rooty()
        bw = self.float_size  # 48，圆球宽
        font = (MONO, 11, "bold")
        # 弹窗宽度随文本自适应，文字在弹窗内水平居中，整体以悬浮球中心为锚点向两边扩展
        pad = 12  # 左右留白
        pop_w = len(text) * 9 + pad  # 11px 加粗约每字符 9px
        pop_h = 22
        # 水平严格居中悬浮球中心；垂直方向上沿之上 6px（多个弹窗垂直错开）
        px = bx + bw // 2 - pop_w // 2
        py = by - pop_h - 6 - (self._popups - 1) * 6

        pop = tk.Toplevel(self.float_win)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        try:
            pop.attributes("-transparentcolor", C_KEY)
        except Exception:
            pass
        cv = tk.Canvas(pop, width=pop_w, height=pop_h, highlightthickness=0, bg=C_KEY)
        cv.pack()
        cx, cy = pop_w // 2, pop_h // 2
        # 浅蓝主文字 + 深蓝描边（错位 1px），在桌面背景上数字清晰
        outline = cv.create_text(cx + 1, cy + 1, text=text, fill="#1d5f8a", font=font)
        item = cv.create_text(cx, cy, text=text, fill="#4db6ff", font=font)
        pop.geometry(f"+{px}+{py}")

        # 渐隐色阶：浅蓝 -> 桌面淡出(奶白)，模拟淡出（12 帧约 0.6 秒）
        steps = 12
        p0 = (0x4D, 0xB6, 0xFF)  # 浅蓝
        p1 = (0xFF, 0xF8, 0xEC)  # 奶白
        colors = ["#%02x%02x%02x" % tuple(
            int(p0[i] + (p1[i] - p0[i]) * (s / (steps - 1))) for i in range(3))
            for s in range(steps)]

        def animate(step):
            try:
                if step >= steps:
                    pop.destroy()
                    return
                cv.itemconfigure(item, fill=colors[step])  # 主文字淡出
                cv.move(outline, 0, -3)  # 每步上浮 3 像素
                cv.move(item, 0, -3)
                self.root.after(50, lambda: animate(step + 1))
            except Exception:
                pass

        self.root.after(50, lambda: animate(0))

    def _float_menu(self, event):
        menu = tk.Menu(self.float_win, tearoff=0)
        menu.add_command(label="打开主界面", command=self._show_main)
        menu.add_command(label="立即刷新余额", command=self._refresh_balance_now)
        menu.add_command(label="组织排名", command=lambda: self._open_rank())
        menu.add_command(label="隐藏悬浮窗", command=self._hide_float)
        menu.add_separator()
        menu.add_command(label="退出程序", command=self.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_rank(self):
        """打开"组织 Token 排名"登录/排名对话框。"""
        try:
            import rank_ui
            rank_ui.open_rank_dialog(self.root)
        except Exception as exc:
            messagebox.showerror("打开失败", f"组织排名功能加载失败：\n{exc}")

    def _show_main(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

    def _hide_float(self):
        """隐藏当前形态的桌面挂件（悬浮窗右键/桌宠右键菜单共用）。"""
        self.float_var.set(False)
        self.settings["float_window"] = False
        save_settings(self.settings)
        self._apply_form(self.settings.get("display_form", "float"), save=False)

    # ================= 桌宠形态（点击弹四种信息面板） =================
    def _build_pet_window(self):
        """桌宠窗口：透明底本体（assets/pet.png，用户提供的无背景图），可拖动，点击弹出信息面板。"""
        IMG = 96  # 桌宠本体尺寸
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", C_KEY)
        except Exception:
            pass
        self.pet_win = win
        self.pet_size = IMG
        self._pet_drag_off = (0, 0)
        self._pet_drag_start = (0, 0)
        self._pet_panel_open = False
        self._pet_squish = False  # 按压 Q 弹状态

        cv = tk.Canvas(win, width=IMG, height=IMG, highlightthickness=0, bg=C_KEY)
        cv.pack()
        self.pet_cv = cv
        # 桌宠本体：透明底水豚。Pillow 精确缩放到 IMG 并展平透明键色，
        # 消除缩放的毛边并让圆角/抗锯齿边缘干净。
        img = self._make_pet_image(IMG)
        if img is not None:
            cv.create_image(IMG / 2, IMG / 2, image=img, tags="petbody")
        else:  # Pillow 不可用：直接贴原图（透明底 PNG）
            av = self._keep_image(_res("pet.png"), subsample=10)  # 1000 -> 100
            cv.create_image(IMG / 2, IMG / 2, image=av, tags="petbody")

        # 事件：点击弹面板 / 拖动 / 双击主界面 / 右键菜单 / 按压回弹
        cv.bind("<Enter>", self._pet_on_hover)
        cv.bind("<Leave>", self._pet_on_leave)
        cv.bind("<ButtonPress-1>", self._pet_press)
        cv.bind("<B1-Motion>", self._pet_on_drag)
        cv.bind("<ButtonRelease-1>", self._pet_release)
        cv.bind("<Double-Button-1>", lambda e: self._show_main())
        cv.bind("<Button-3>", self._pet_menu)

        # 信息面板
        self._build_pet_panel()

        # 初始位置：优先保存的位置，否则屏幕右下角
        x = self.settings.get("pet_x")
        y = self.settings.get("pet_y")
        if x is None or y is None:
            x = win.winfo_screenwidth() - IMG - 40
            y = win.winfo_screenheight() - IMG - 80
        win.geometry(f"+{x}+{y}")
        win.withdraw()

    def _make_pet_image(self, size: int):
        """Pillow 把水豚本体精确缩放到 size，展平半透明为透明键色（返回 tk.PhotoImage）。"""
        try:
            from PIL import Image
        except Exception:
            return None
        try:
            SS = 6
            W = size * SS
            resample = getattr(Image, "Resampling", Image).LANCZOS
            img = Image.open(_res("pet.png")).convert("RGBA").resize((W, W), resample)
            small = img.resize((size, size), resample)
            out = Image.new("RGB", (size, size), C_KEY)
            pxo = out.load()
            pxi = small.load()
            for yy in range(size):
                for xx in range(size):
                    r, g, b, a = pxi[xx, yy]
                    pxo[xx, yy] = (r, g, b) if a >= 90 else (0xFF, 0x00, 0xFE)
            import io
            buf = io.BytesIO()
            out.save(buf, format="PNG")
            photo = tk.PhotoImage(data=buf.getvalue())
            self._images.append(photo)
            return photo
        except Exception:
            return None

    def _pet_set_visible(self, show: bool):
        """桌宠窗口显示/隐藏（同时收起面板）。"""
        if getattr(self, "pet_win", None) is None:
            return
        if show:
            self.pet_win.deiconify()
            self.pet_win.lift()
        else:
            self.pet_win.withdraw()
            self._pet_panel_open = False
            try:
                self.pet_panel.withdraw()
            except Exception:
                pass

    def _build_pet_panel(self):
        """桌宠信息面板：每次只显示一条（点一下切到下一条，循环）。

        五条信息依次为：当前余额 / 今日消耗 / 今日当前Key / 今日Token / 当前计价时段。
        每条统一居中显示（标签在上、数值在下），上下排布、互不重叠。
        """
        self._pet_item = 0  # 0=余额 1=今日消耗 2=今日Key 3=今日Token 4=峰谷
        w, h = 236, 96
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        try:
            panel.attributes("-transparentcolor", C_KEY)
        except Exception:
            pass
        self.pet_panel = panel
        self.pet_panel_size = (w, h)
        cv = tk.Canvas(panel, width=w, height=h, highlightthickness=0, bg=C_KEY)
        cv.pack()
        self.pet_panel_cv = cv
        rounded_rect(cv, 2, 2, w - 2, h - 2, 14, fill="#fff8ec", outline=C_ORANGE, width=2)
        # 统一布局：标题在上、数值在下，均居中，互不重叠
        self.pet_p_title = cv.create_text(w / 2, 30, anchor="n", text="",
                                          fill=C_SUB, font=(FONT, 9))
        self.pet_p_value = cv.create_text(w / 2, 56, anchor="n", text="--",
                                          fill=C_ORANGE_DEEP, font=(MONO, 12, "bold"))
        # 右上角 ✕：关闭面板（不切换下一条）
        cv.create_text(w - 14, 18, text="✕", fill=C_SUB, font=(FONT, 10, "bold"), tags="close")
        cv.tag_bind("close", "<Button-1>", lambda e: self._pet_toggle_panel(force_close=True))
        panel.withdraw()

    def _pet_place_panel(self):
        """把信息面板放到桌宠右侧（贴边防溢出屏幕）并显示、刷新内容。"""
        panel = self.pet_panel
        w, h = self.pet_panel_size
        bx, by = self.pet_win.winfo_x(), self.pet_win.winfo_y()
        sw, sh = panel.winfo_screenwidth(), panel.winfo_screenheight()
        x = bx + self.pet_size + 8
        if x + w > sw:
            x = bx - w - 8
        y = min(max(by - 20, 0), sh - h - 40)
        panel.geometry(f"+{int(x)}+{int(y)}")
        panel.deiconify()
        panel.lift()
        self._pet_panel_open = True
        self._refresh_pet_panel()

    def _pet_toggle_panel(self, force_close=False):
        """点击桌宠：面板隐藏则显示当前条；已显示则切到下一条（循环）。"""
        if force_close or not self._pet_panel_open:
            if force_close:
                self.pet_panel.withdraw()
                self._pet_panel_open = False
            else:
                self._pet_place_panel()
            return
        # 面板已显示：切到下一条并刷新
        self._pet_item = (self._pet_item + 1) % 5
        self._refresh_pet_panel()

    def _pet_key_info(self):
        """今日经本地代理消耗最高的 Key：(金额, token数, 提示名)。无 Key 数据时给 其他来源。"""
        today = date.today().isoformat()
        rows = storage.key_breakdown(today, today)
        if not rows:
            return 0.0, 0, "其他来源"
        r = rows[0]  # 已按费用倒序
        return r["cost"], r["cache_hit"] + r["cache_miss"] + r["completion"], r["key_hint"]

    def _pet_peak_label(self):
        """当前计价时段：高峰 -> 梁文峰（红），低谷 -> 梁文谷（绿）。"""
        try:
            peak = pricing.is_peak_hour(datetime.now(), self.config)
        except Exception:
            peak = False
        if peak:
            return "梁文峰（高峰）", C_RED
        return "梁文谷（低谷）", C_GREEN

    def _refresh_pet_panel(self):
        """按当前项渲染单条信息；面板未打开时跳过。"""
        try:
            if not self._pet_panel_open:
                return
            cv = self.pet_panel_cv
            item = self._pet_item
            title = ""
            value = ""
            color = C_ORANGE_DEEP
            if item == 0:      # 当前余额
                title = "当前余额"
                if self.state.get("balance") is not None:
                    value = fmt_money(self.state["balance"])
                    color = C_GREEN
                elif self.state.get("balance_error"):
                    value = "获取失败"
                    color = C_RED
                else:
                    value = "加载中..."
                    color = C_SUB
            elif item == 1:    # 今日消耗
                title = "今日消耗"
                value = fmt_money(storage.today_stats()["cost"])
            elif item == 2:    # 今日当前Key：只显示 Key 提示名（不含金额/Token）
                _kcost, _ktok, khint = self._pet_key_info()
                title = "今日当前Key"
                value = khint or "其他来源"
                color = C_BROWN_DARK
            elif item == 3:    # 今日Token：数量
                title = "今日 Token 消耗"
                s = storage.today_stats()
                value = "%s tok" % fmt_int(s["cache_hit"] + s["cache_miss"] + s["completion"])
            else:              # 当前计价时段（梁文峰/梁文谷）
                title = "当前计价时段"
                value, color = self._pet_peak_label()
            cv.itemconfigure(self.pet_p_title, text=title)
            cv.itemconfigure(self.pet_p_value, text=value, fill=color)
        except Exception:
            pass

    # ---- 事件 ----
    def _pet_on_hover(self, event):
        pass  # 预留：悬停可显示提示

    def _pet_on_leave(self, event):
        pass

    def _pet_press(self, event):
        self._pet_drag_off = (event.x_root - self.pet_win.winfo_x(),
                              event.y_root - self.pet_win.winfo_y())
        self._pet_drag_start = (event.x_root, event.y_root)
        # 按压 Q 弹：按下压扁（本体微缩），松手回弹，玩偶手感
        if not self._pet_squish:
            self._pet_squish = True
            self._pet_redraw(int(self.pet_size * 0.9))

    def _pet_redraw(self, size: int):
        """重绘桌宠本体到指定尺寸（按压压扁/松手回弹用）。"""
        try:
            img = self._make_pet_image(max(size, 40))
            if img is not None:
                self.pet_cv.delete("petbody")
                self.pet_cv.create_image(self.pet_size / 2, self.pet_size / 2,
                                         image=img, tags="petbody")
        except Exception:
            pass

    def _pet_on_drag(self, event):
        self.pet_win.geometry(
            f"+{event.x_root - self._pet_drag_off[0]}"
            f"+{event.y_root - self._pet_drag_off[1]}")

    def _pet_release(self, event=None):
        # 按压结束：回弹到原尺寸
        if self._pet_squish:
            self._pet_squish = False
            self._pet_redraw(self.pet_size)
        # 按下后几乎没移动 => 视为点击：开关信息面板
        if event is not None and self._pet_drag_start:
            dx = abs(event.x_root - self._pet_drag_start[0])
            dy = abs(event.y_root - self._pet_drag_start[1])
            if dx + dy < 6:
                self._pet_toggle_panel()
        self.settings["pet_x"] = self.pet_win.winfo_x()
        self.settings["pet_y"] = self.pet_win.winfo_y()
        save_settings(self.settings)
        if self._pet_panel_open:
            self._pet_place_panel()  # 桌宠移动后面板跟着挪

    def _pet_menu(self, event):
        menu = tk.Menu(self.pet_win, tearoff=0)
        menu.add_command(label="打开主界面", command=self._show_main)
        menu.add_command(label="立即刷新余额", command=self._refresh_balance_now)
        menu.add_command(label="组织排名", command=lambda: self._open_rank())
        menu.add_command(label="隐藏桌宠", command=self._hide_float)
        menu.add_separator()
        menu.add_command(label="退出程序", command=self.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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
            # 面板打开中：刷新使用额度（悬浮窗面板 / 桌宠面板）
            if self._float_panel_open:
                self._refresh_float_panel()
            if getattr(self, "_pet_panel_open", False):
                self._refresh_pet_panel()
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
        if getattr(self, "_pet_panel_open", False):
            self._refresh_pet_panel()
        # 余额统计页"当前余额"跟随刷新
        self._update_balance_cur_label()

        # 2.5) 更新检测状态（标题条横幅 + 设置页标签 + 底部状态栏）
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
            elif up.get("error"):
                self.lbl_updcheck.config(
                    text=f"更新检查失败（{up.get('checked_at', '')}）· 点击重试",
                    fg=C_RED)
            elif up.get("checked_at") == "检查中...":
                self.lbl_updcheck.config(text="更新检查：检查中...", fg=C_SUB)
            else:
                self.lbl_updcheck.config(
                    text=f"已是最新版本 {APP_VERSION} · {up.get('checked_at', '')}",
                    fg=C_GREEN)
        if hasattr(self, "lbl_update_status"):
            if not up:
                self.lbl_update_status.config(text="更新检查中...", fg="#f6d9ae")
            elif up.get("available"):
                self.lbl_update_status.config(
                    text=f"发现新版本 {up['tag']} · 点击下载", fg=C_GOLD)
            elif up.get("error"):
                self.lbl_update_status.config(text="更新检查失败 · 点击重试", fg=C_RED)
            elif up.get("checked_at") == "检查中...":
                self.lbl_update_status.config(text="更新检查中...", fg="#f6d9ae")
            else:
                self.lbl_update_status.config(
                    text=f"已是最新版本 {APP_VERSION} · {up.get('checked_at', '')}",
                    fg="#9fe8a8")

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

        # 4.75) DSH Harness 数据同步状态
        if hasattr(self, "lbl_dshsync"):
            dsh = self.state.get("dsh_sync")
            if not dsh or not dsh.get("enabled"):
                self.lbl_dshsync.config(text="DSH Harness 同步：未启用", fg=C_SUB)
            elif dsh.get("error"):
                self.lbl_dshsync.config(
                    text="DSH Harness 同步：读取失败 " + str(dsh["error"]), fg=C_RED)
            else:
                self.lbl_dshsync.config(
                    text=f"DSH Harness 同步：运行中 · 累计导入 {fmt_int(dsh.get('total_added', 0))} 条"
                         f" · {dsh.get('last_time', '')}", fg=C_GREEN)

        # 4.8) CodeBuddy 数据同步状态
        if hasattr(self, "lbl_codebuddysync"):
            cb = self.state.get("codebuddy_sync")
            if not cb or not cb.get("enabled"):
                self.lbl_codebuddysync.config(text="CodeBuddy 同步：未启用", fg=C_SUB)
            elif cb.get("error"):
                self.lbl_codebuddysync.config(
                    text="CodeBuddy 同步：读取失败 " + str(cb["error"]), fg=C_RED)
            else:
                self.lbl_codebuddysync.config(
                    text=f"CodeBuddy 同步：运行中 · 累计导入 {fmt_int(cb.get('total_added', 0))} 条"
                         f" · {cb.get('last_time', '')}", fg=C_GREEN)

        # 4.9) WorkBuddy 数据同步状态
        if hasattr(self, "lbl_workbuddysync"):
            wb = self.state.get("workbuddy_sync")
            if not wb or not wb.get("enabled"):
                self.lbl_workbuddysync.config(text="WorkBuddy 同步：未启用", fg=C_SUB)
            elif wb.get("error"):
                self.lbl_workbuddysync.config(
                    text="WorkBuddy 同步：读取失败 " + str(wb["error"]), fg=C_RED)
            else:
                self.lbl_workbuddysync.config(
                    text=f"WorkBuddy 同步：运行中 · 累计导入 {fmt_int(wb.get('total_added', 0))} 条"
                         f" · {wb.get('last_time', '')}", fg=C_GREEN)

        # 5) 日期与仪表盘图表
        self.lbl_date.config(text=datetime.now().strftime("%Y年%m月%d日"))
        try:
            if self.nb.index("current") == 0:
                self._draw_chart()
                self._draw_token_bar()
                self._refresh_daily_list()
                self.week_card_value.config(
                    text=f"费用 {fmt_money(storage.this_week_stats()['cost'])}")
                self.month_card_value.config(
                    text=f"费用 {fmt_money(storage.this_month_stats()['cost'])}")
        except Exception:
            pass

        self.root.after(1500, self._tick)

    def _on_tab_changed(self, event=None):
        """切换页签时刷新该页数据；并防止个别 Windows/主题下切页导致窗口意外收缩。

        切换前记住窗口状态，切页布局完成后检查：若窗口被意外缩小（宽或高变小
        超过阈值）或从最大化掉出，立即还原——保持 UI 原本大小。
        """
        try:
            state = self.root.state()
            before = None
            if state == "normal":
                before = self.root.geometry()
            elif state == "zoomed":
                before = "zoomed"
            refresh = self._page_refreshers.get(self.nb.index("current"))
            if refresh:
                refresh()

            if before:
                # 会话令牌：只有最近一次切页的 guard 链有效，避免旧链误还原
                seq = self._tab_guard_seq = (getattr(self, "_tab_guard_seq", 0) + 1)

                def guard(seq, tries=5):
                    if seq != self._tab_guard_seq:
                        return  # 已有更新的切页，本链作废
                    try:
                        cur_state = self.root.state()
                        if cur_state != "normal":
                            if cur_state == "zoomed":
                                return  # 已处于最大化，无需处理
                            return  # 最小化/其它状态一律不干预
                        if before == "zoomed":
                            self.root.state("zoomed")  # 切页导致掉出最大化：恢复
                            return
                        geo = self.root.geometry()  # 形如 WxH+X+Y
                        try:
                            w0, h0 = (int(v) for v in before.split("+")[0].split("x"))
                            w1, h1 = (int(v) for v in geo.split("+")[0].split("x"))
                        except Exception:
                            return
                        # 只拦"明显缩小"（≥40px），避免误伤用户主动拖拽
                        if w1 <= w0 - 40 or h1 <= h0 - 40:
                            self.root.geometry(before)
                            return  # 已还原
                        # 切页引发的收缩可能晚于本检查（WM 尺寸更新滞后），多探几次
                        if tries > 1:
                            self.root.after(250, lambda: guard(seq, tries - 1))
                    except Exception:
                        pass
                self.root.after(150, lambda: guard(seq))
        except Exception:
            pass

    def _refresh_balance_now(self):
        def work():
            try:
                self.state["balance"] = scheduler.fetch_balance(self.config)
                self.state["balance_error"] = None
                storage.save_balance_snapshot(self.state["balance"])  # 余额每日快照
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
        """执行一次更新检查：查询 GitHub 最新 release 并写入 state（失败也记录，供界面显示）。"""
        try:
            info = updater.check_latest()
            if not info or not info.get("tag"):
                raise RuntimeError("接口无响应")
            self.state["update"] = {"tag": info["tag"], "url": info["url"],
                                    "name": info.get("name") or "",
                                    "setup_url": info.get("setup_url") or "",
                                    "checked_at": datetime.now().strftime("%H:%M:%S")}
            if updater.is_newer(info["tag"], updater.parse_version(APP_VERSION)):
                self.state["update"]["available"] = True
                updater._log("发现新版本 %s（当前 %s）" % (info["tag"], APP_VERSION))
                self.root.after(0, self._notify_update, info)
            else:
                updater._log("已是最新版本 %s" % info["tag"])
        except Exception as exc:
            # 检查失败不静默：写入 state 供状态栏/设置页显示
            info = self.state.get("update") or {}
            info["error"] = str(exc)
            info["checked_at"] = datetime.now().strftime("%H:%M:%S")
            self.state["update"] = info
            updater._log("检查失败: %s" % exc)

    def _notify_update(self, info: dict):
        """弹出新版本提示：是=自动下载安装；否=本次版本不再提醒。"""
        try:
            if self.settings.get("update_ignore") == info.get("tag"):
                return
            if not self.root.winfo_exists():
                return
            go = messagebox.askyesno(
                "发现新版本",
                f"发现新版本 {info.get('tag')}（当前 {APP_VERSION}）\n\n"
                f"{info.get('name') or ''}\n\n"
                "是否立即下载并安装更新？\n（自动覆盖当前版本，安装完成后自动启动）")
            if go:
                self._download_update(info)
            else:
                self.settings["update_ignore"] = info.get("tag")
                save_settings(self.settings)
        except Exception:
            pass

    def _download_update(self, info: dict):
        """下载新版本安装包（带进度条），完成后自动退出并安装。"""
        setup_url = (info or {}).get("setup_url")
        tag = (info or {}).get("tag", "")
        if not setup_url:
            self._open_update_page()  # 拿不到直链时退回打开下载页
            return
        try:
            base = os.environ.get("LOCALAPPDATA") or APPDATA_DIR
            update_dir = os.path.join(base, "DeepSeekTokenMonitor", "_update")
            os.makedirs(update_dir, exist_ok=True)
        except Exception:
            update_dir = os.path.join(DATA_DIR, "_update")
        dest = os.path.join(update_dir, "DeepSeekTokenMonitor-Setup-%s.exe" % tag.replace("v", ""))
        cancel_ev = threading.Event()

        dlg = tk.Toplevel(self.root)
        dlg.title("更新")
        dlg.configure(bg=C_BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        tk.Label(dlg, text="正在更新到 %s ..." % tag, bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=18, pady=(14, 4))
        self._upd_lbl = tk.Label(dlg, text="准备下载...", bg=C_BG, fg=C_TEXT, font=(FONT, 9))
        self._upd_lbl.pack(anchor="w", padx=18, pady=(0, 6))
        bar = ttk.Progressbar(dlg, length=320, maximum=100)
        bar.pack(padx=18, pady=(0, 10))
        btn_cancel = ttk.Button(dlg, text="取消", command=cancel_ev.set)
        btn_cancel.pack(pady=(0, 12))

        def on_progress(done, total):
            pct = (done * 100 // total) if total else 0
            def apply():
                try:
                    bar.configure(value=pct)
                    self._upd_lbl.config(
                        text="下载中 %d%%（%.1f / %.1f MB）" % (
                            pct, done / 1048576.0, (total or 0) / 1048576.0))
                except Exception:
                    pass
            self.root.after(0, apply)

        def on_error(msg):
            def apply():
                try:
                    dlg.destroy()
                except Exception:
                    pass
                messagebox.showerror(
                    "更新失败", "%s\n\n可点击「打开下载页」前往 GitHub 手动下载。" % msg)
            self.root.after(0, apply)

        def work():
            try:
                updater.download(setup_url, dest, on_progress, cancel_ev)
                if cancel_ev.is_set():
                    return
                def ready():
                    try:
                        self._upd_lbl.config(text="下载完成，正在安装（程序将自动重启）...")
                        btn_cancel.config(state="disabled")
                    except Exception:
                        pass
                self.root.after(0, ready)
                self.root.after(800, lambda: self._install_update(dest))
            except Exception as exc:
                try:
                    os.remove(dest)
                except Exception:
                    pass
                on_error(str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _install_update(self, setup_path: str):
        """退出当前程序，由升级脚本静默安装新版本（安装完成后自动启动新版覆盖旧版）。"""
        try:
            update_dir = os.path.dirname(setup_path)
            script = os.path.join(update_dir, "run_update.ps1")
            with open(script, "w", encoding="utf-8") as f:
                f.write("$ErrorActionPreference = 'SilentlyContinue'\n")
                f.write("# 等待旧程序完全退出，释放 exe 占用，再执行静默安装\n")
                f.write("for ($i = 0; $i -lt 60; $i++) {\n")
                f.write("  if (-not (Get-Process DeepSeekTokenMonitor -ErrorAction "
                        "SilentlyContinue)) { break }\n")
                f.write("  Start-Sleep -Seconds 1\n}\n")
                f.write("Start-Process -FilePath '%s' "
                        "-ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait\n"
                        % setup_path)
                f.write("Remove-Item -Path '%s' -Recurse -Force\n" % update_dir)
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        self.quit()

    def _check_update_now(self):
        """设置页按钮：立即检查更新。"""
        self.state["update"] = {"checked_at": "检查中..."}
        threading.Thread(target=self._run_update_check, daemon=True).start()

    def _tray_check_update(self):
        """托盘菜单「检查更新」：复用常规检查；结果以弹窗反馈（主窗口藏匿也能看到）。

        发现新版时 _run_update_check 会自行弹出下载询问，这里不再重复提示；
        已是最新 / 检查失败 则用弹窗告知。
        """
        self._check_update_now()

        def poll():
            try:
                up = self.state.get("update") or {}
                if up.get("checked_at") == "检查中...":
                    self.root.after(500, poll)
                    return
                if up.get("available"):
                    return  # 新版：_run_update_check 已弹更新询问
                if up.get("error"):
                    messagebox.showerror("检查更新失败", str(up["error"]), parent=self.root)
                else:
                    messagebox.showinfo(
                        "检查更新", "已是最新版本 " + str(up.get("tag") or APP_VERSION),
                        parent=self.root)
            except Exception:
                pass

        self.root.after(600, poll)

    def _rebill_all(self):
        """设置页按钮：按当前峰谷价重算全部历史费用（对账用）。"""
        if not messagebox.askyesno(
                "重算历史费用",
                "将按 config.json 当前价目与峰谷规则（工作日高峰 9:00-12:00 与 "
                "14:00-18:00、周末全天低谷价）重新计算全部历史记录的费用，"
                "覆盖原有费用。\n\n是否继续？"):
            return

        def work():
            try:
                n = storage.rebill_all(self.config)
                self.root.after(0, lambda: messagebox.showinfo(
                    "重算完成", f"已按最新峰谷价重算 {fmt_int(n)} 条记录的费用。"))
            except Exception as exc:
                self.root.after(0, lambda: messagebox.showerror("重算失败", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _open_update_page(self):
        """打开最新 release 下载页。"""
        info = self.state.get("update") or {}
        url = info.get("url") or (updater.RELEASE_URL % (info.get("tag") or ""))
        if url:
            webbrowser.open(url)

    def _on_update_click(self):
        """点击更新状态：有新版本→确认后自动下载安装；检查失败/未检查→立即重试。"""
        up = self.state.get("update") or {}
        if up.get("available"):
            self._notify_update(up)
        else:
            self._check_update_now()

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
        try:
            self._float_save_pos()  # 保存悬浮球位置（同时把同步游标等一并写入 settings.json）
        except Exception:
            pass
        try:  # 保存桌宠位置
            if getattr(self, "pet_win", None) is not None:
                self.settings["pet_x"] = self.pet_win.winfo_x()
                self.settings["pet_y"] = self.pet_win.winfo_y()
        except Exception:
            pass
        save_settings(self.settings)  # 显式持久化运行设置（含各数据源同步游标）
        self.stop_event.set()
        self.root.destroy()


def main():
    app = App()
    app.root.after(1000, app._tick)
    app.root.after(600, app._maybe_show_key_wizard)  # 首次无 Key 时引导填写（可跳过）
    app.root.mainloop()


if __name__ == "__main__":
    main()