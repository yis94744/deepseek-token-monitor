# -*- coding: utf-8 -*-
"""进程守卫：单实例互斥 + 全局异常兜底（崩溃留痕）。

解决两个实测问题：
  1. **多开无互斥**：原代码没有任何单实例检测，双击两次会起两个进程，
     第二个抢不到 8787 端口就静默显示"代理 启动失败"，用户不知道自己
     正在用一个不统计的实例。
  2. **崩溃无留痕**：原代码没有 sys.excepthook / threading.excepthook /
     Tk.report_callback_exception，后台线程抛异常时只有控制台（打包成
     windowed exe 后连控制台都没有），出问题只能靠猜。

用法（在 App 初始化之前调用）：
    import guard
    if not guard.acquire_single_instance():
        guard.activate_existing_window()
        sys.exit(0)
    guard.install_exception_hooks(data_dir, APP_VERSION)
"""
import os
import sys
import threading
import traceback
from datetime import datetime

_MUTEX_NAME = "Global\\DeepSeekTokenMonitor_SingleInstance"
_mutex_handle = None
_crash_log_path = None
_version = "unknown"

# 最近的操作轨迹（崩溃报告里带上，便于定位"崩在哪一步"）
_trail = []
_trail_lock = threading.Lock()
_TRAIL_MAX = 20


def note(text: str):
    """记一笔操作轨迹（保留最近 20 条），随崩溃报告一起写出。"""
    try:
        with _trail_lock:
            _trail.append("%s %s" % (datetime.now().strftime("%H:%M:%S"), text))
            if len(_trail) > _TRAIL_MAX:
                del _trail[:_TRAIL_MAX // 2]
    except Exception:
        pass


# ---------------- 单实例互斥 ----------------
def acquire_single_instance() -> bool:
    """尝试获取单实例锁。返回 True=拿到（可以继续启动），False=已有实例在跑。

    Windows 用命名互斥体（CreateMutexW）；其他平台用锁文件兜底。
    进程退出时由操作系统自动释放，不会残留。
    """
    global _mutex_handle
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                              wintypes.LPCWSTR]
            handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
            if not handle:
                return True  # 拿不到句柄时不要拦住用户，放行
            ERROR_ALREADY_EXISTS = 183
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            _mutex_handle = handle
            return True
        except Exception:
            return True  # 检测失败不应阻止启动

    # 非 Windows：锁文件兜底
    try:
        import tempfile
        lock = os.path.join(tempfile.gettempdir(), "deepseek_token_monitor.lock")
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return True


def activate_existing_window():
    """已有实例在跑时，尽量把它的窗口拉到最前，给用户一个"程序已经开着"的反馈。

    失败也不报错（可能权限不足/窗口已最小化到托盘）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnds = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if "水豚噜噜" in buf.value:
                    hwnds.append(hwnd)
            return True

        user32.EnumWindows(_enum, 0)
        for hwnd in hwnds:
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            break
    except Exception:
        pass


# ---------------- 异常兜底 ----------------
def install_exception_hooks(data_dir: str, version: str = "unknown"):
    """安装全局异常钩子，把未捕获异常写入 data/crash.log。"""
    global _crash_log_path, _version
    _version = version
    if data_dir:
        _crash_log_path = os.path.join(data_dir, "crash.log")

    sys.excepthook = _handle_uncaught

    # Python 3.8+：threading.excepthook 捕获后台线程异常
    try:
        threading.excepthook = _handle_thread_exception
    except Exception:
        pass

    # Tk 回调里的异常默认只打印到 stderr（windowed exe 无 stderr），单独接管
    try:
        import tkinter
        tkinter.Tk.report_callback_exception = _handle_tk_exception
    except Exception:
        pass


def _trail_text() -> str:
    with _trail_lock:
        return "\n".join("  " + t for t in _trail) or "  （无）"


def _write_crash(kind: str, exc_type, exc_value, exc_tb):
    if not _crash_log_path:
        return
    try:
        text = (
            "\n" + "=" * 70 + "\n"
            "时间   : %s\n"
            "版本   : %s\n"
            "类型   : %s\n"
            "线程   : %s\n"
            "最近操作:\n%s\n"
            "堆栈   :\n%s\n"
            % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _version, kind,
               threading.current_thread().name, _trail_text(),
               "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        )
        with open(_crash_log_path, "a", encoding="utf-8") as f:
            f.write(text)
        # crash.log 超过 1MB 轮转一份
        if os.path.getsize(_crash_log_path) > 1024 * 1024:
            os.replace(_crash_log_path, _crash_log_path + ".1")
    except Exception:
        pass


def _handle_uncaught(exc_type, exc_value, exc_tb):
    _write_crash("主线程未捕获异常", exc_type, exc_value, exc_tb)
    try:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass


def _handle_thread_exception(args):
    _write_crash("后台线程未捕获异常（%s）" % getattr(args.thread, "name", "?"),
                 args.exc_type, args.exc_value, args.exc_traceback)


def _handle_tk_exception(self, exc_type, exc_value, exc_tb):
    _write_crash("Tk 回调异常", exc_type, exc_value, exc_tb)
    try:
        import tkinter.messagebox as mb
        mb.showerror("程序错误",
                     "发生了一个错误，已记录到 crash.log：\n\n%s: %s"
                     % (exc_type.__name__, exc_value))
    except Exception:
        pass
