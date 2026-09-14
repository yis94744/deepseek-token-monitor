# -*- coding: utf-8 -*-
"""
rank_client.py — DeepSeekTokenMonitor 的 Token 排名客户端（全平台榜版）

协议（见 cloud_rank/app.py）：
  POST /api/register   {email, password, nickname} -> {token, user}
  POST /api/login      {email, password}           -> {token, user}
  POST /api/report     {tokens: 今日累计总量}       -> {ok, day, board}
  GET  /api/board                                  -> {ok, day, board}
  GET  /api/me                                     -> {ok, user}

行为：
  - 登录态（server/token/user）保存在 %APPDATA%/DeepSeekTokenMonitor/settings.json 的 rank 段
  - start_reporter() 守护线程上报【当日累计 token】（storage.today_stats 多源总量），
    上报响应中的榜单缓存在内存供 UI 读取；未登录/未启用时静默跳过
  - 网络持续失败自动退避（30s→60s→120s→…→300s 封顶），恢复后立即回到 30s
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
_DEFAULT_SERVER = "https://106.52.172.73"   # 已启用 TLS（私有 CA，见 tools/gen_certs.py）
_REPORT_INTERVAL = 30  # 秒：基础间隔（失败自动退避，上限 300s）


def _friendly_net_error(exc) -> str:
    """把 urllib/socket 底层异常转成普通用户能看懂的中文提示。

    例：<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>
      -> 无法连接服务器（目标计算机积极拒绝）
    """
    msg = str(exc)
    # 去掉 <urlopen error ...> / [WinError NNNN] 机器包装
    m = msg
    if ">" in m:
        m = m.rsplit(">", 1)[-1]
    m = m.strip().strip(".。")
    # 归类常见错误
    low = msg.lower()
    if "timed out" in low or "timeout" in low or "超时" in m:
        return "连接服务器超时，请检查网络后重试"
    if "refused" in low or "积极拒绝" in m or "拒绝" in m:
        return "无法连接服务器（服务可能维护中或网络不通）"
    if "resolve" in low or "getaddrinfo" in low or "11001" in msg or "解析" in m or "dns" in low:
        return "无法解析服务器地址，请检查网络"
    if "certificate_verify_failed" in low or "certificate verify failed" in low:
        return "服务器证书未受信任：请在设置页点击「信任服务器证书」后重试"
    if "ssl" in low or "证书" in m:
        return "服务器安全连接异常，请稍后重试"
    if "10060" in msg or "10061" in msg or "10065" in msg:
        return "无法连接服务器（服务可能维护中或网络不通）"
    if not m:
        return "网络异常，请稍后重试"
    return "网络异常：" + m[:80]


class RankError(Exception):
    """后端返回业务错误（非 2xx）时抛出。code 标识错误类型便于 UI 处理。"""
    def __init__(self, msg, code=""):
        super().__init__(msg)
        self.code = code


class RankClient:
    """极简 HTTP JSON 客户端（仅标准库 urllib）。"""

    def __init__(self, base=None, token=None):
        self.base = (base or _DEFAULT_SERVER).rstrip("/")
        self.token = token or ""
        self.user = None
        self._https_failed = False   # 缓存：HTTPS 不可用时直接走 HTTP，避免每次都等超时

    # ---------- HTTPS 优先 + 自动回退 ----------
    def _https_alternative(self):
        """返回同一主机的 https 地址；当前已是 https 或已判定不可用则返回 None。"""
        if self._https_failed or not self.base.startswith("http://"):
            return None
        return "https://" + self.base[len("http://"):]

    def _http_fallback(self):
        """返回同一主机的 http 地址（当前是 https 时用于回退）。"""
        if not self.base.startswith("https://"):
            return None
        return "http://" + self.base[len("https://"):]

    # ---------- HTTPS 支持 ----------
    @staticmethod
    def _ssl_context():
        """为 https 请求构造 SSL 上下文（可选：信任私有 CA）。

        服务器当前用私有 CA 签发的证书（因为裸 IP 拿不到公信证书）。
        用户在设置页选择"信任服务器证书"后，CA 会被安装到系统受信任根，
        这里即可用系统信任链正常校验；未安装时给出明确提示而不是晦涩的
        CERTIFICATE_VERIFY_FAILED。
        """
        import ssl
        try:
            return ssl.create_default_context()
        except Exception:
            return None

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
        attempts = [url]
        alt_https = self._https_alternative()
        if alt_https:
            # HTTPS 优先：明文 HTTP 只是过渡，能加密就加密
            attempts = [alt_https + path, url]
        elif self.base.startswith("https://"):
            http_alt = self._http_fallback()
            if http_alt:
                attempts.append(http_alt + path)

        last_exc = None
        for attempt_url in attempts:
            is_https = attempt_url.lower().startswith("https://")
            req = urllib.request.Request(attempt_url, data=data, headers=headers,
                                         method=method)
            kwargs = {"timeout": timeout}
            if is_https:
                ctx = self._ssl_context()
                if ctx is not None:
                    kwargs["context"] = ctx
            try:
                with urllib.request.urlopen(req, **kwargs) as resp:
                    raw = resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                # 服务器有响应（含 401），说明这条链路可用，不再回退
                code = e.code
                raw = e.read().decode("utf-8", "replace")
                if code == 401:
                    raise RankError(
                        str((json.loads(raw) if raw else {}).get("detail", "登录已失效"))
                        or "登录已失效", "TOKEN_INVALID")
                raise RankError(f"请求失败({code}): {raw[:120]}")
            except Exception as exc:
                last_exc = exc
                if is_https:
                    # HTTPS 不可用（常见于服务器 443 未放行）：记住并回退到 HTTP，
                    # 这样用户不需要改任何配置也能继续用；放行后重启即自动走加密。
                    self._https_failed = True
                    continue
                raise RankError(_friendly_net_error(exc), "NETWORK")
            try:
                j = json.loads(raw)
            except Exception:
                raise RankError(f"服务器返回无法解析：{raw[:200]}")
            if "detail" in j and not j.get("ok"):
                raise RankError(str(j.get("detail") or "请求失败"))
            return j

        raise RankError(_friendly_net_error(last_exc) if last_exc else "连接失败", "NETWORK")

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

    def report_today(self, tokens: int, board_version: str = ""):
        """上报今日累计 token。

        带上已有的 board_version：服务器在榜单没变时只回版本号（board=None），
        省掉每次 200 人的全量榜单回传。
        """
        return self._request("POST", "/api/report",
                             {"tokens": int(tokens), "board_version": board_version or ""})

    def board(self, since_version: str = ""):
        """拉取今日全平台榜单（带版本号可省流量）。"""
        from urllib.parse import quote
        path = "/api/board"
        if since_version:
            path += "?since_version=" + quote(since_version)
        return self._request("GET", path)


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


def resolve_server() -> str:
    """确定要连接的服务器地址（可配置，不必改代码重发版）。

    优先级：
      1. 环境变量 DSTM_RANK_SERVER（最高，便于临时切换/测试）
      2. ranking.json 的 server 字段（用户在设置页填的，随会话持久化）
      3. 内置默认值 _DEFAULT_SERVER

    这样服务器迁移时用户自己就能改，不需要等新版本发布。
    """
    env = (os.environ.get("DSTM_RANK_SERVER") or "").strip()
    if env:
        return env.rstrip("/")
    sess = load_session()
    saved = (sess.get("server") or "").strip()
    if saved:
        return saved.rstrip("/")
    return _DEFAULT_SERVER


def set_server(url: str, persist: bool = True) -> str:
    """设置服务器地址（设置页调用）。会做基本规范化并立即持久化。"""
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    if persist:
        sess = load_session()
        sess["server"] = url
        save_session(sess)
        # 地址变了，之前那份榜单就不再可信
        _state["board"] = None
        _state["day"] = None
        _state["error"] = None
        _state["error_streak"] = 0
    return url


def make_client():
    s = load_session()
    server = resolve_server()
    # 旧会话迁移：服务端已从 :8000 切到标准 80 端口，历史会话地址自动升级。
    # 注：http -> https 的升级不在这里做——_request 会优先尝试 https，
    # 成功即自动加密，失败则回退，用户无需改配置。
    if server and (":8000" in str(server)):
        server = _DEFAULT_SERVER
        sess = load_session()
        sess["server"] = server
        save_session(sess)
        s = sess
    c = RankClient(base=server, token=s.get("token", ""))
    c.user = s.get("user")
    return c


def get_state():
    """最近一次榜单/我的名次（供 UI 读取）。

    内存态为空时会回落到磁盘缓存（rank_cache.json）——这样**程序刚启动、
    首轮上报还没跑完时，界面直接显示上次的名次**，而不是干巴巴的"同步中"
    （用户会以为坏了）。首轮上报成功后立即用新数据覆盖。
    """
    # 仅当"从未同步过"时才回落到缓存。
    # 一旦本轮产生了结果（成功或失败）就以内存态为准——否则会把
    # "同步失败"这种需要用户知道的状态，掩盖成上次的名次。
    if _state.get("my_rank") is None and _state.get("board") is None \
            and not _state.get("error") and not _state.get("last_time"):
        cached = _load_cache()
        if cached:
            return cached
    return _state


def _cache_path() -> str:
    """缓存文件路径：与 ranking.json 同目录（跟随 _APP_DIR，便于测试隔离）。"""
    return os.path.join(os.path.dirname(_RANK_PATH), "rank_cache.json")


def _save_cache():
    """把最近一次成功的结果落到磁盘（仅排名相关，不含敏感信息）。"""
    try:
        data = {
            "board": _state.get("board"),
            "day": _state.get("day"),
            "my_rank": _state.get("my_rank"),
            "my_tokens": _state.get("my_tokens"),
            "last_time": _state.get("last_time"),
            "board_version": _state.get("board_version"),
            "error": None,
            "error_streak": 0,
            "token_invalid": False,
            "from_cache": False,
        }
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _cache_path())
    except Exception:
        pass


def _load_cache():
    """读取磁盘缓存；带上 from_cache 标记，UI 可据此提示"上次同步"。"""
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if data.get("my_rank") is None and not data.get("board"):
            return None
        data["from_cache"] = True
        return data
    except Exception:
        return None


_state = {"board": None, "day": None, "my_rank": None, "my_tokens": None,
          "last_time": None, "error": None, "token_invalid": False,
          "error_streak": 0, "board_version": ""}


def backoff_seconds():
    """当前应等待的秒数：无错误 30s；连续失败按 30→60→120→240→300 封顶退避。

    供 reporter 线程与排行榜页轮询共用，恢复成功后 streak 清零自动回到 30s。
    """
    streak = _state.get("error_streak") or 0
    if streak <= 0:
        return _REPORT_INTERVAL
    return min(300, _REPORT_INTERVAL * (2 ** (streak - 1)))


# ---------- 后台上报线程（30s，常驻） ----------
def _today_tokens():
    """本机当日累计 token（全部数据源：命中+未命中+输出）。

    返回 None 表示【读取失败/数据库未就绪】，调用方必须跳过本次上报——
    绝不能把失败当成 0：服务器存的是"当日累计最新值"，一旦用 0 覆盖，
    当天已累积的真实用量就永久丢了（2026-09-14 实际踩过：调试脚本在
    未初始化 storage 的进程里上报，把当天 260 万 token 覆盖成了 0）。
    """
    try:
        import storage
        if not getattr(storage, "_db_path", None):
            return None          # 数据库尚未初始化，不能报 0
        s = storage.today_stats()
        total = int((s["cache_hit"] or 0) + (s["cache_miss"] or 0) + (s["completion"] or 0))
        return total
    except Exception:
        return None              # 读取异常同样跳过，不污染服务端数据


def _sync_once(c):
    """上报一次今日累计并把榜单写入内存态。返回 True=成功。

    401 时把 _state["token_invalid"] 置 True，UI 端读取后可清 ranking.json 并回登录页。
    """
    total = _today_tokens()
    if total is None:
        # 本地库未就绪：跳过本次上报（下次再报），避免用 0 覆盖服务端累计值
        _state["error"] = "本地数据库未就绪，暂缓上报"
        return False
    try:
        r = c.report_today(total, _state.get("board_version") or "")
    except RankError as exc:
        _state["error"] = str(exc)
        if exc.code == "TOKEN_INVALID":
            _state["token_invalid"] = True
        else:
            # 网络/服务器错误才触发退避；401 登录失效不拉长轮询（等用户重登）
            _state["error_streak"] = (_state.get("error_streak") or 0) + 1
        return False
    except Exception as exc:
        _state["error"] = _friendly_net_error(exc)
        _state["error_streak"] = (_state.get("error_streak") or 0) + 1
        return False
    me = c.user or {}
    uid = me.get("user_id")
    # board=None 表示服务器判定"榜单未变化"，沿用本地缓存，避免无谓重传
    if r.get("board") is not None:
        board = r.get("board") or []
        _state["board"] = board
    else:
        board = _state.get("board") or []
    if r.get("board_version"):
        _state["board_version"] = r["board_version"]
    _state["day"] = r.get("day")
    _state["my_rank"] = next((b["rank"] for b in board if b["user_id"] == uid), None)
    _state["my_tokens"] = total
    _state["last_time"] = time.strftime("%H:%M:%S")
    _state["error"] = None
    _state["error_streak"] = 0
    _state["token_invalid"] = False
    _save_cache()   # 落盘：下次启动立即有数据可显示
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
        # 启动后稍等让主程序完成初始化再首报。
        # 从 5 秒缩短到 2 秒：界面在首报完成前会显示磁盘缓存的名次，
        # 但缓存首次为空（新用户）时仍会短暂显示"同步中"，越短越好。
        time.sleep(2)
        while True:
            try:
                sess = load_session()
                c = RankClient(base=resolve_server(), token=sess.get("token", ""))
                c.user = sess.get("user")
                if c.token:
                    _sync_once(c)
            except Exception:
                pass
            # 失败退避：连续失败间隔自动拉长（由 backoff_seconds 决定）
            time.sleep(max(5, backoff_seconds()))

    threading.Thread(target=loop, daemon=True, name="rank-reporter").start()
