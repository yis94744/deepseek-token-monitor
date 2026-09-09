# -*- coding: utf-8 -*-
"""
rank_ui.py — DeepSeekTokenMonitor 的 Token 排名 UI（全平台榜版）

打开方式：主程序调用 open_rank_dialog(root)。
- 登录/注册：邮箱 + 密码（未注册邮箱将自动注册）
- 主视图：今日全平台 Token 榜单（含我的名次高亮），每 30s 自动刷新
- 后台上报由 rank_client.start_reporter() 常驻守护线程完成（每 30s 一次）
"""
import tkinter as tk
from tkinter import messagebox, ttk

import rank_client as rc

# 与主程序一致的配色/字体（此处不 import 主程序避免副作用）
C_BG = "#faf3e7"
C_BROWN_DARK = "#572c19"
C_RED = "#d9534f"
C_TEXT = "#4a2f1d"
C_SUB = "#8a6a4d"
C_ORANGE_DEEP = "#d77522"
FONT = "Microsoft YaHei UI"


def _fmt(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return "0"


class RankDialog(tk.Toplevel):
    """登录/注册 + 全平台 Token 榜单 一体化对话框。"""

    def __init__(self, master):
        super().__init__(master)
        self.title("Token 排名")
        self.configure(bg=C_BG)
        self.resizable(False, False)
        self.transient(master)
        self._poll_job = None

        self.sess = rc.load_session()
        self.client = rc.make_client()
        self._build()
        if self.client.token and self.client.user:
            self._show_main()
        else:
            self._show_login()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---------- 构建 ----------
    def _build(self):
        self.login_frame = tk.Frame(self, bg=C_BG, padx=24, pady=18)
        tk.Label(self.login_frame, text="邮箱 + 密码登录", bg=C_BG, fg=C_BROWN_DARK,
                 font=(FONT, 11, "bold")).pack(anchor="w", pady=(0, 10))
        tk.Label(self.login_frame, text="首次使用的新邮箱将自动注册", bg=C_BG, fg=C_SUB,
                 font=(FONT, 8)).pack(anchor="w", pady=(0, 8))
        row = tk.Frame(self.login_frame, bg=C_BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text="邮箱:", bg=C_BG, fg=C_TEXT, font=(FONT, 10)).pack(side="left")
        self.e_email = ttk.Entry(row, width=30)
        self.e_email.pack(side="left", padx=6)
        row2 = tk.Frame(self.login_frame, bg=C_BG)
        row2.pack(fill="x", pady=3)
        tk.Label(row2, text="密码:", bg=C_BG, fg=C_TEXT, font=(FONT, 10)).pack(side="left")
        self.e_pass = ttk.Entry(row2, width=30, show="*")
        self.e_pass.pack(side="left", padx=6)
        row3 = tk.Frame(self.login_frame, bg=C_BG)
        row3.pack(fill="x", pady=3)
        tk.Label(row3, text="昵称:", bg=C_BG, fg=C_TEXT, font=(FONT, 10)).pack(side="left")
        self.e_nick = ttk.Entry(row3, width=30)
        self.e_nick.pack(side="left", padx=6)
        tk.Label(self.login_frame, text="昵称用于榜单展示，可留空（默认邮箱前缀）",
                 bg=C_BG, fg=C_SUB, font=(FONT, 8)).pack(anchor="w", pady=(0, 4))
        self.lbl_login_err = tk.Label(self.login_frame, text="", bg=C_BG, fg=C_RED,
                                      font=(FONT, 9))
        self.lbl_login_err.pack(anchor="w")
        btns = tk.Frame(self.login_frame, bg=C_BG)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="登录 / 注册", command=self._do_login).pack(side="left")
        ttk.Button(btns, text="关闭", command=self._close).pack(side="left", padx=8)

        self.main_frame = tk.Frame(self, bg=C_BG, padx=14, pady=12)
        top = tk.Frame(self.main_frame, bg=C_BG)
        top.pack(fill="x")
        self.lbl_me = tk.Label(top, text="", bg=C_BG, fg=C_BROWN_DARK, font=(FONT, 11, "bold"))
        self.lbl_me.pack(side="left")
        self.lbl_day = tk.Label(top, text="", bg=C_BG, fg=C_SUB, font=(FONT, 9))
        self.lbl_day.pack(side="left", padx=10)
        btns = tk.Frame(self.main_frame, bg=C_BG)
        btns.pack(fill="x", pady=(4, 6))
        ttk.Button(btns, text="刷新榜单", command=self._refresh).pack(side="left")
        ttk.Button(btns, text="退出登录", command=self._logout).pack(side="left", padx=8)
        ttk.Button(btns, text="关闭", command=self._close).pack(side="left")

        cols = ("rank", "nick", "email", "tokens")
        self.tree = ttk.Treeview(self.main_frame, columns=cols, show="headings",
                                 height=16)
        for col, text, w in (("rank", "#", 46), ("nick", "昵称", 140),
                             ("email", "邮箱", 200), ("tokens", "今日 Token", 130)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="center" if col in ("rank", "tokens")
                             else "w")
        self.tree.tag_configure("me", background="#fdf0d8", foreground=C_ORANGE_DEEP)
        self.tree.pack(fill="both", expand=True)
        self.lbl_hint = tk.Label(self.main_frame, text="", bg=C_BG, fg=C_SUB, font=(FONT, 8))
        self.lbl_hint.pack(anchor="w", pady=(6, 0))

    # ---------- 视图切换 ----------
    def _show_login(self):
        self.main_frame.pack_forget()
        self.login_frame.pack(fill="both", expand=True)
        self.geometry("360x300")
        self.e_email.focus_set()

    def _show_main(self):
        self.login_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)
        self.geometry("560x460")
        me = self.client.user or {}
        nick = me.get("nickname") or (me.get("email") or "").split("@")[0]
        self.lbl_me.config(text=f"你好，{nick}")
        self._refresh()
        self._start_poll()

    def _start_poll(self):
        self._stop_poll()

        def poll():
            self._refresh()
            self._poll_job = self.after(30000, poll)  # 半分钟自动刷新
        self._poll_job = self.after(30000, poll)

    def _stop_poll(self):
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None

    # ---------- 动作 ----------
    def _do_login(self):
        email = self.e_email.get().strip()
        password = self.e_pass.get()
        if not email or len(password) < 6:
            self.lbl_login_err.config(text="请填写邮箱与至少 6 位密码")
            return
        self.lbl_login_err.config(text="登录中...")
        self.update_idletasks()

        def work():
            try:
                try:
                    self.client.login(email, password)
                except rc.RankError:
                    # 登录 401：可能是未注册的新邮箱 → 尝试自动注册
                    if self.client.token:
                        raise
                    try:
                        self.client.register(email, password, self.e_nick.get().strip())
                    except rc.RankError as reg_exc:
                        msg = str(reg_exc)
                        if "已注册" in msg or "已存在" in msg or "409" in msg:
                            raise rc.RankError("邮箱或密码错误")  # 已注册但密码不对
                        raise
            except Exception as exc:
                def show_err():
                    self.lbl_login_err.config(text=str(exc))
                self.after(0, show_err)
                return
            sess = rc.load_session()
            sess["server"] = self.client.base
            sess["token"] = self.client.token
            sess["user"] = self.client.user
            rc.save_session(sess)
            self.after(0, self._show_main)

        import threading
        threading.Thread(target=work, daemon=True).start()

    def _refresh(self):
        st = rc.get_state()
        board = st.get("board")
        if board is None:
            # 尚无内存态：主动拉一次
            def fetch():
                try:
                    self.client.me()
                    r = self.client.board()
                    rc._state["board"] = r.get("board") or []
                    rc._state["day"] = r.get("day")
                    self.after(0, lambda: self._render(rc._state["board"],
                                                       rc._state["day"]))
                except Exception as exc:
                    self.after(0, lambda: self.lbl_hint.config(text=str(exc)))
            import threading
            threading.Thread(target=fetch, daemon=True).start()
            return
        self._render(board, st.get("day"))

    def _render(self, board, day):
        self.tree.delete(*self.tree.get_children())
        me = self.client.user or {}
        uid = me.get("user_id")
        my_rank = None
        if day:
            self.lbl_day.config(text=day)
        for b in board[:100]:
            tags = ("me",) if uid and b.get("user_id") == uid else ()
            if uid and b.get("user_id") == uid:
                my_rank = b["rank"]
            nick = b.get("nickname") or (b.get("email") or "?").split("@")[0]
            self.tree.insert("", "end", values=(b["rank"], nick, b.get("email"),
                                                _fmt(b.get("tokens", 0))), tags=tags)
        st = rc.get_state()
        if my_rank is None:
            my_rank = st.get("my_rank")
        me_txt = f"我的名次：第 {my_rank} 名" if my_rank else "我：未上榜（今日暂无 token）"
        nick = me.get("nickname") or (me.get("email") or "").split("@")[0]
        self.lbl_me.config(text=f"你好，{nick} · {me_txt}")
        err = st.get("error")
        t = st.get("last_time") or ""
        if err:
            self.lbl_hint.config(text=f"同步中（{err}）· 30s 自动更新")
        else:
            self.lbl_hint.config(
                text=f"共 {len(board)} 人上榜 · 每 30s 同步{t and ' · 上次 ' + t}")

    def _logout(self):
        rc.clear_session()
        self.client.token = ""
        self.client.user = None
        rc._state["board"] = None
        self._stop_poll()
        self._show_login()

    def _close(self):
        self._stop_poll()
        self.destroy()


def open_rank_dialog(root):
    """主程序入口：打开排名对话框。"""
    try:
        dlg = RankDialog(root)
        dlg.grab_set()
    except Exception as exc:
        messagebox.showerror("打开失败", f"排名加载失败：\n{exc}")
