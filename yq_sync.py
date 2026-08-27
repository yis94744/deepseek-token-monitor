# -*- coding: utf-8 -*-
"""YQ Harness 数据源同步模块。

设计目标：不加任何转发跳数、不增加 API 延迟、不改 YQ Harness 的任何配置。
YQ Harness（YQ）与 DeepSeek Harness 同源，把每个会话的用量投影缓存写到
YQ 数据目录的 storages/session_projcache.json（默认
%APPDATA%\\YQ\\yq-home\\storages\\session_projcache.json，桌面端会通过
YQ_HOME 环境变量指定）：
  tables.sessions.<会话ID>.rows.tokenUsage.val.totals = {
    uncachedInputTokens, outputTokens, cacheReadTokens, cacheWriteTokens }
本模块只读该文件，把各会话的用量增量同步到本软件的 usage.db。

计费口径：与其他数据源同步一致，按 config.json 的人民币单价用 token 数
重新计算。只提取用量数值，不读取任何对话内容。

模型归属：投影缓存没有模型字段。YQ 桌面端用 --port 0 启动后端（端口随机），
所以默认不依赖固定 API 端口：优先 config.yq.models 手动映射，其次 settings
缓存，再次 YQ 本地 API（api_base 可配，默认 http://127.0.0.1:3080，与
DeepSeek Harness 默认端口一致；连不上就跳过），最后退回读取 YQ 数据目录的
settings.yaml 里的 agent-default-model（实际默认模型），再不行用
unknown_model_fallback。
"""
import json
import os
import urllib.request
from datetime import datetime

import pricing
import storage

_LOG = None  # 日志文件路径，由 run() 初始化

# 模型解析默认参数（可用 config.json 的 yq 段覆盖）
_API_BASE_DEFAULT = "http://127.0.0.1:3080"
_MODEL_TTL_SECONDS = 300


def _yq_home() -> str:
    """定位 YQ 数据目录（yq-home）：YQ_HOME 环境变量 > %APPDATA%\\YQ\\yq-home > ~/.yq。"""
    home = os.environ.get("YQ_HOME")
    if home:
        return home
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "YQ", "yq-home")
    return os.path.join(os.path.expanduser("~"), ".yq")


def _projcache_path(config: dict) -> str:
    """解析 YQ 用量投影缓存路径，未配置时按 yq-home 默认位置。"""
    path = (config.get("yq") or {}).get("projcache_path")
    if path:
        return os.path.expandvars(os.path.expanduser(path))
    return os.path.join(_yq_home(), "storages", "session_projcache.json")


def _api_call(api_base: str, method: str, payload: dict, timeout: float = 4.0):
    """调用 YQ 本地 API（POST /api/<method>），失败返回 None。"""
    try:
        body = json.dumps({
            "type": "client-request",
            "rpcId": "yq-monitor",
            "method": method,
            "payload": payload,
        }).encode("utf-8")
        req = urllib.request.Request(api_base.rstrip("/") + "/api/" + method,
                                     data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = data.get("result") or {}
        if not result.get("ok"):
            return None
        return result.get("value")
    except Exception:
        return None


def _session_model_from_api(api_base: str, sid: str):
    """优先 session.models 当前选择；查不到（子代理等）则翻历史事件取最近一次模型。"""
    value = _api_call(api_base, "session.models", {"sessionId": sid})
    current = (value or {}).get("current") or {}
    model = current.get("model")
    if model:
        return str(model)
    # 子代理会话没有 current：翻最近的事件找 assistant/message 的 source.model
    value = _api_call(api_base, "session.history", {"sessionId": sid, "maxMessages": 40})
    for entry in (value or {}).get("events") or []:
        event = (entry or {}).get("event") or {}
        if event.get("type") != "assistant/message":
            continue
        source = ((event.get("data") or {}).get("message") or {}).get("source") or {}
        model = source.get("model")
        if model:
            return str(model)
    return None


def _default_model_from_settings(home: str):
    """从 YQ 数据目录的 settings.yaml 读取 agent-default-model.model。

    YQ 桌面端后端端口随机（--port 0），API 解析不可用时用它兜底——所有
    会话默认都用这个模型，除非用户中途切换。解析失败返回 None。
    """
    try:
        with open(os.path.join(home, "settings.yaml"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    for i, line in enumerate(lines):
        if line.strip().startswith("agent-default-model:"):
            for j in range(i + 1, min(i + 5, len(lines))):
                stripped = lines[j].strip()
                if stripped.startswith("model:"):
                    model = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    return model or None
                if stripped and not stripped.startswith(("provider:", "reasoningEffort:", "#")):
                    break  # 已离开该配置块
            break
    return None


def _resolve_session_models(config: dict, settings: dict, sids: list) -> dict:
    """解析一批会话的模型，返回 {sid: model}（解析不到的 sid 不出现）。

    优先级：config.yq.models 手动映射 > settings 缓存（TTL 内）> 实时查询
    API（api_base 可配）> settings.yaml 默认模型。全部失败不阻塞同步：
    该会话退回 unknown_model_fallback 计费。
    """
    yq = config.get("yq") or {}
    api_base = str(yq.get("api_base") or _API_BASE_DEFAULT)
    override = yq.get("models") or {}
    if not isinstance(override, dict):
        override = {}
    ttl = int(yq.get("model_refresh_seconds") or _MODEL_TTL_SECONDS)
    cache = settings.get("yq_models")
    if not isinstance(cache, dict):
        cache = {}
        settings["yq_models"] = cache

    now = datetime.now().timestamp()
    result = {}
    missing = []
    for sid in sids:
        if sid in override:
            result[sid] = str(override[sid])
            continue
        entry = cache.get(sid)
        if isinstance(entry, dict) and entry.get("model") and now - float(entry.get("ts") or 0) < ttl:
            result[sid] = str(entry["model"])
            continue
        missing.append(sid)

    if missing:
        # 先试本地 API（桌面端端口随机时可能连不上，失败即跳过）
        api_fail = False
        for sid in missing:
            model = _session_model_from_api(api_base, sid)
            if model:
                cache[sid] = {"model": model, "ts": now}
                result[sid] = model
            else:
                api_fail = True
        # API 不可用则退回 settings.yaml 的默认模型（一次解析，整批生效）
        if api_fail:
            default_model = _default_model_from_settings(_yq_home())
            if default_model:
                for sid in missing:
                    if sid not in result:
                        cache[sid] = {"model": default_model, "ts": now}
                        result[sid] = default_model
    return result


def _log(text: str):
    """同步过程日志（仅排查用），写入 data 目录的 yq_sync.log。"""
    if not _LOG:
        return
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
    except Exception:
        pass


def sync_once(config: dict, settings: dict) -> int:
    """同步一轮：读取 YQ 用量投影缓存并做会话级差分，返回新增条数。

    无状态差分：每个会话的上次已导入总量从 usage.db 自身读取（该会话最近
    一条 yq 记录），不依赖 settings 里的计数器。因此进程重启、settings
    丢失都不会漏记或重复计费——重启后第一次同步会把"上次入库以来的全部
    增量"补进去；去重键用累计总量本身（yq:<sid>:<h>:<m>:<c>），同一
    总量只入库一次，天然免疫重复执行。

    模型归属：投影缓存没有模型字段，本模块按
    手动映射 > settings 缓存 > 本地 API > settings.yaml 默认模型的顺序
    解析每个会话的模型（见 _resolve_session_models）。
    """
    path = _projcache_path(config)
    if not os.path.isfile(path):
        return 0  # YQ 尚未生成数据
    try:
        mtime_ms = int(os.path.getmtime(path) * 1000)
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return 0  # 文件被 YQ 写入中/损坏，跳过等下一轮
    sessions = ((data.get("tables") or {}).get("sessions") or {})
    if not sessions:
        return 0

    pending = []
    for sid, sdata in sessions.items():
        rows = sdata.get("rows") or {}
        tu = (rows.get("tokenUsage") or {}).get("val") or {}
        totals = tu.get("totals") or {}
        hit = int(totals.get("cacheReadTokens") or 0)
        miss = int(totals.get("uncachedInputTokens") or 0)
        comp = int(totals.get("outputTokens") or 0)
        if hit + miss + comp <= 0:
            continue
        prev = storage.yq_cumulative(str(sid))
        if prev is not None:
            delta = (hit - prev[0], miss - prev[1], comp - prev[2])
            if delta[0] + delta[1] + delta[2] <= 0:
                continue  # 总量未增长（含投影回退），跳过
        else:
            delta = (hit, miss, comp)  # 库内尚无该会话记录：导入全部用量
        pending.append((sid, delta))

    if not pending:
        return 0

    # 解析涉及会话的模型（缓存优先，只对未缓存/过期的会话发 API 请求）
    models = _resolve_session_models(config, settings, [sid for sid, _ in pending])

    dt = datetime.fromtimestamp(mtime_ms / 1000.0)  # 投影最后更新时刻≈用量发生时刻
    rows_out = []
    for sid, (hit, miss, comp) in pending:
        model = models.get(sid) or config.get("unknown_model_fallback", "deepseek-v4-flash")
        price = pricing.get_price(model, config, dt)
        cost = pricing.calc_cost(
            {"prompt_cache_hit_tokens": hit,
             "prompt_cache_miss_tokens": miss,
             "completion_tokens": comp}, price)
        key = "yq:%s:%d:%d:%d" % (sid, hit, miss, comp)
        rows_out.append((key, dt, model, hit, miss, comp, cost))
    return storage.add_external_requests(rows_out)


def run(config: dict, settings: dict, state: dict, stop_event):
    """后台线程入口：周期增量同步 YQ Harness 会话用量。"""
    global _LOG
    yq = config.get("yq") or {}
    data_dir = os.path.dirname(storage._db_path or "")
    if data_dir:
        _LOG = os.path.join(data_dir, "yq_sync.log")
    interval = max(2, int(yq.get("sync_interval_seconds", 5)))
    state["yq_sync"] = {"enabled": bool(yq.get("enabled", True)), "total_added": 0,
                        "last_added": 0, "last_time": None, "error": None}
    _log(f"同步线程启动，间隔={interval}秒，文件={_projcache_path(config)}")

    while not stop_event.wait(interval):
        if not (config.get("yq") or {}).get("enabled", True):
            state["yq_sync"]["enabled"] = False  # 设置页关闭后线程常驻等待，重新开启即恢复
            state["yq_sync"]["error"] = None
            continue
        try:
            added = sync_once(config, settings)
            info = state["yq_sync"]
            info["enabled"] = True  # 恢复同步时标记启用（设置页开关状态同步）
            if added:
                info["total_added"] = info.get("total_added", 0) + added
                info["last_added"] = added
            info["last_time"] = datetime.now().strftime("%H:%M:%S")
            info["error"] = None
            _log(f"本轮新增={added} 累计={info.get('total_added', 0)}")
        except Exception as exc:
            state["yq_sync"]["error"] = str(exc)
            _log("ERROR: " + str(exc))
