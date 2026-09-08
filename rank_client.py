# -*- coding: utf-8 -*-
"""
rank_client.py — DeepSeekTokenMonitor 的云端 Token 排名客户端（全平台榜版）

协议（见 cloud_rank/app.py）：
  POST /api/register   {email, password, nickname} -> {token, user}
  POST /api/login      {email, password}           -> {token, user}
  POST /api/report     {tokens: 今日累计总量}       -> {ok, day, board}
  GET  /api/board                                  -> {ok, day, board}
  GET  /api/me                                     -> {ok, user}

行为：
  - 登录态（server/token/user）保存在 %APPDATA%/DeepSeekTokenMonitor/settings.json 的 rank 段
  - start_reporter() 守护线程每 30s 上报一次【当日累计 token】（storage.today_stats 多源总量），
    上报响应中的榜单缓存在内存供 UI 读取；未登录/未启用时静默跳过
  - 只上报 token 数字；不上报任何对话内容 / API Key / 余额等敏感信息

服务器地址：settings.rank.server，默认 http://106.52.172.73（标准 80 端口，全网可达）。
"""
import json
import os
import time

_APP_DIR = os.path.join(os.environ.get("APPDATA", ""), "DeepSeekTokenMonitor")
_SETTINGS_PATH = os.path.join(_APP_DIR, "settings.json")
_RANK_PATH = os.path.join(_APP_DIR, "ranking.json")  # 独立会话文件（与 settings.json 隔离防并发覆盖）
_LEGACY_RANK_KEY = "rank"  # 旧版把会话存在 settings.json 的 rank 段
_DEFAULT_SERVER = "http://106.52.172.73"
_REPORT_INTERVAL = 30  # 秒：半分钟上报一次并同步榜单


class RankError(Exception):
    """后端返回业务错误（非 2xx）时抛出。"""


class RankClient:
    """极简 HTTP JSON 客户端（仅标准库 urllib）。"""

    def __init__(self, base=None, token=None):
        self.base = (base or _DEFAULT_SERVER).rstrip("/")
        self.token = token or ""
        self.user = None

    # ---------- HTTP 基础 ----------
    def _request(self, method, path, payload=None, with_auth=True, timeout=8):
        import urllib.error
        import urllib.request
        url = self.base + path
        data = None
        headers = {"Content-Type": "application/json", "User-Agent": "DeepSeekTokenMonitor"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        if with_auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
        except Exception as exc:
            raise RankError(f"无法连接服务器：{exc}")
        try:
            j = json.loads(raw)
        except Exception:
            raise RankError(f"服务器返回无法解析：{raw[:200]}")
        if "detail" in j and not j.get("ok"):
            raise RankError(str(j.get("detail") or "请求失败"))
        return j

    # ---------- 业务接口 ----------
    def register(self, email, password, nickname=""):
        """注册；成功即登录（返回 token）。"""
        r = self._request("POST", "/api/register",
                          {"email": email, "password": password, "nickname": nickname},
                          with_auth=False)
        self.token = r.get("token", "")
        self.user = r.get("user")
        return r

    def login(self, email, password):
        """登录；成功后保存 token 与用户信息。"""
        r = self._request("POST", "/api/login",
                          {"email": email, "password": password}, with_auth=False)
        self.token = r.get("token", "")
        self.user = r.get("user")
        return r

    def me(self):
        r = self._request("GET", "/api/me")
        self.user = r.get("user")
        return r

    def report_today(self, tokens: int):
        """上报今日累计 token；返回 {ok, day, board}（board=今日全平台榜单）。"""
        return self._request("POST", "/api/report", {"tokens": int(tokens)})

    def board(self):
        """拉取今日全平台榜单。"""
        return self._request("GET", "/api/board")


# ---------- 会话持久化（独立 ranking.json；旧版 settings.json 的 rank 段自动迁移） ----------
def load_session():
    # 优先读取独立的 ranking.json
    try:
        with open(_RANK_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    # 迁移：老版本存在 settings.json 的 rank 段
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        rank = cfg.get(_LEGACY_RANK_KEY)
        if rank:
            save_session(rank)
            return rank
    except Exception:
        pass
    return {}


def save_session(sess):
    """原子写独立 ranking.json（与主程序 settings.json 完全隔离，杜绝并发互相覆盖）。"""
    os.makedirs(_APP_DIR, exist_ok=True)
    try:
        tmp = _RANK_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sess or {}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _RANK_PATH)
    except Exception:
        try:
            with open(_RANK_PATH, "w", encoding="utf-8") as f:
                json.dump(sess or {}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def clear_session():
    s = load_session()
    s.pop("token", None)
    s.pop("user", None)
    s.pop("email", None)
    save_session(s)


def make_client():
    s = load_session()
    server = s.get("server")
    # 旧会话迁移：服务端已从 :8000 切到标准 80 端口，历史会话地址自动升级
    if server and ("106.52.172.73:8000" in server or ":8000" in str(server)):
        server = _DEFAULT_SERVER
        sess = load_session()
        sess["server"] = server
        save_session(sess)
        s = sess
    c = RankClient(base=server, token=s.get("token", ""))
    c.user = s.get("user")
    return c


def get_state():
    """最近一次榜单/我的名次（内存态，供 UI 读取）。未同步时为 None。"""
    return _state


_state = {"board": None, "day": None, "my_rank": None, "my_tokens": None,
          "last_time": None, "error": None}


# ---------- 后台上报线程（30s，常驻） ----------
def _today_tokens():
    """本机当日累计 token（全部数据源：命中+未命中+输出）。"""
    try:
        import storage
        s = storage.today_stats()
        return int((s["cache_hit"] or 0) + (s["cache_miss"] or 0) + (s["completion"] or 0))
    except Exception:
        return 0


def _sync_once(c):
    """上报一次今日累计并把榜单写入内存态。返回 True=成功。"""
    total = _today_tokens()
    try:
        r = c.report_today(total)
    except Exception as exc:
        _state["error"] = str(exc)
        return False
    board = r.get("board") or []
    me = c.user or {}
    uid = me.get("user_id")
    _state["board"] = board
    _state["day"] = r.get("day")
    _state["my_rank"] = next((b["rank"] for b in board if b["user_id"] == uid), None)
    _state["my_tokens"] = total
    _state["last_time"] = time.strftime("%H:%M:%S")
    _state["error"] = None
    return True


def start_reporter(interval_seconds=_REPORT_INTERVAL):
    """启动常驻守护线程：每 interval 秒上报今日累计并同步榜单。
    未登录时静默跳过。幂等：重复调用不重复开线程。
    """
    import threading
    if getattr(start_reporter, "_started", False):
        return
    start_reporter._started = True

    def loop():
        # 启动后先等一会，让主程序完成初始化再首报
        time.sleep(5)
        while True:
            try:
                sess = load_session()
                c = RankClient(base=sess.get("server"), token=sess.get("token", ""))
                c.user = sess.get("user")
                if c.token:
                    _sync_once(c)
            except Exception:
                pass
            time.sleep(max(5, int(interval_seconds)))

    threading.Thread(target=loop, daemon=True, name="rank-reporter").start()
