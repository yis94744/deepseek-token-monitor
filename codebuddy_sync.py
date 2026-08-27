# -*- coding: utf-8 -*-
"""CodeBuddy 数据源同步模块。

设计目标：不加任何转发跳数、不增加 API 延迟、不改 CodeBuddy 的任何配置。
CodeBuddy（腾讯云代码助手）每次 Agent 回合结束会把用量以日志行形式写入
用户数据目录（默认 %APPDATA%\\CodeBuddy CN\\logs）下各窗口扩展日志：

  [AgentReporter] [<traceId>] Agent execution successful with usage: {...}

usage JSON 字段（与 DeepSeek 官方口径一致）：
  inputTokens       输入总 token（含缓存部分）
  cacheTokens       输入·缓存命中
  cachedMissTokens  输入·缓存未命中
  outputTokens      输出 token
  totalTokens       合计 = inputTokens + outputTokens
  credit            CodeBuddy 内部点数（不用于本项目计费）

本模块只读日志尾部增量，把每次 Agent 回合的用量同步到 usage.db；
计费口径与其他数据源同步一致：按 config.json 的人民币单价用 token 数
重新计算。只提取 usage 数值，不读取任何对话内容。

说明：CodeBuddy 的会话级用量在本地仅此日志路径（state.vscdb 里的
CodeBuddy-LLMDataReportCACHE-llm-data 为加密上报缓存，无法解析），
因此以日志中的 AgentReporter 汇总行作为唯一数据源。
"""
import fnmatch
import json
import os
import re
from datetime import datetime

import pricing
import storage

_LOG = None  # 日志文件路径，由 run() 初始化

# 用量行形如：... [AgentReporter] [<traceId>] Agent execution successful with usage: {...}
_USAGE_PAT = re.compile(r"usage:\s*(\{.*?\})")
_TRACE_PAT = re.compile(r"\[AgentReporter\]\s*\[([0-9a-fA-F-]+)\]")
_TIME_PAT = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
# 模型名：ConversationManager 等行会带 modelName 字段（Auto=自动路由，不算具体模型）
_MODEL_PAT = re.compile(r'"modelName"\s*:\s*"([^"]+)"')
_AUTO_MODELS = {"auto", "inherit", "auto（自动路由）"}
# 只处理扩展日志文件名（支持滚动日志：腾讯云代码助手.log / .1.log / ...）
_LOG_NAME = "腾讯云代码助手*.log"


def _logs_dir(config: dict) -> str:
    """定位 CodeBuddy 日志目录：config.codebuddy.logs_dir > %APPDATA%\\CodeBuddy CN\\logs。"""
    path = (config.get("codebuddy") or {}).get("logs_dir")
    if path:
        return os.path.expandvars(os.path.expanduser(str(path)))
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "CodeBuddy CN", "logs")


def _norm_model(name: str) -> str:
    """把日志里的模型名归一化为 config.models 的小写键名。

    例：Deepseek-V4-Flash → deepseek-v4-flash；DeepSeek-V4 Pro → deepseek-v4-pro。
    """
    name = (name or "").strip().lower()
    if not name or name in _AUTO_MODELS:
        return None
    return name.replace(" ", "-")


def _log(text: str):
    """同步过程日志（仅排查用），写入 data 目录的 codebuddy_sync.log。"""
    if not _LOG:
        return
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
    except Exception:
        pass


def _iter_log_files(logs_dir: str):
    """递归列出所有匹配的扩展日志文件。"""
    if not os.path.isdir(logs_dir):
        return
    for root, _dirs, files in os.walk(logs_dir):
        for f in files:
            if fnmatch.fnmatch(f, _LOG_NAME):
                yield os.path.join(root, f)


def sync_once(config: dict, settings: dict) -> int:
    """同步一轮：增量读取 CodeBuddy 日志中的 AgentReporter 用量行，返回新增条数。

    游标（字节偏移 + 文件大小）存 settings["codebuddy_cursor"]；
    文件被滚动/重写（大小变小）时从头读，去重键 cb:<traceId> 保证不重复不虚高。
    """
    logs_dir = _logs_dir(config)
    if not os.path.isdir(logs_dir):
        return 0
    cursor = settings.get("codebuddy_cursor") or {}
    if not isinstance(cursor, dict):
        cursor = {}
    # 模型解析：config.codebuddy.model 手动指定优先，其次日志启发式提取
    forced_model = _norm_model((config.get("codebuddy") or {}).get("model"))
    cached_model = settings.get("codebuddy_model")
    fallback = config.get("unknown_model_fallback", "deepseek-v4-flash")

    pending = []  # (trace_id, dt, model, hit, miss, comp)
    seen_paths = set()  # 本轮见到的文件（用于清理已删除文件的游标，防 settings 膨胀）
    for path in sorted(_iter_log_files(logs_dir)):
        seen_paths.add(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        pos = cursor.get(path, {})
        offset = int(pos.get("offset", 0) or 0)
        old_size = int(pos.get("size", 0) or 0)
        if offset > size or old_size > size:
            offset = 0  # 文件被重写/截断：从头读，去重键保证不重复
        if offset == size:
            continue
        last_model = None
        try:
            # 二进制逐行读取，只消费以 \n 结尾的完整行：文件尾不完整的半行
            # （CodeBuddy 正在写入）留到下一轮。此前文本模式会把半行消费掉，
            # 偏移越过它之后剩余字节永远拼不成整行，该条用量记录就永久丢失。
            with open(path, "rb") as f:
                f.seek(offset)
                consumed = offset
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break  # 半行：等下轮写入方补全后再读
                    consumed += len(raw)
                    line = raw.decode("utf-8", errors="replace")
                    # 启发式记录最近出现的具体模型名（供本文件内 usage 行归属）
                    mm = _MODEL_PAT.search(line)
                    if mm:
                        norm = _norm_model(mm.group(1))
                        if norm:
                            last_model = norm
                    if "Agent execution successful" not in line or "usage:" not in line:
                        continue
                    um = _USAGE_PAT.search(line)
                    if not um:
                        continue
                    try:
                        u = json.loads(um.group(1))
                    except Exception:
                        continue
                    hit = int(u.get("cacheTokens") or 0)
                    miss = int(u.get("cachedMissTokens") or 0)
                    comp = int(u.get("outputTokens") or 0)
                    if hit + miss + comp <= 0:
                        continue
                    tm = _TIME_PAT.match(line)
                    if not tm:
                        continue
                    try:
                        dt = datetime.strptime(tm.group(1), "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        continue
                    tmd = _TRACE_PAT.search(line)
                    trace_id = tmd.group(1) if tmd else ""
                    if not trace_id:
                        continue
                    model = forced_model or last_model or cached_model or fallback
                    pending.append((trace_id, dt, model, hit, miss, comp))
                    # 把启发式提取的模型写入缓存，跨文件/跨轮复用
                    if last_model and cached_model != last_model:
                        cached_model = last_model
                offset = consumed  # 新偏移只推进到最后一个完整行（文件追加时从上次处继续）
        except Exception as exc:
            _log("读取失败 %s: %s" % (path, exc))
        cursor[path] = {"offset": offset, "size": size}

    # 清理已被删除的日志文件的游标，防止 settings.json 无限膨胀
    for gone in [p for p in cursor if p not in seen_paths]:
        del cursor[gone]
    settings["codebuddy_cursor"] = cursor
    if cached_model:
        settings["codebuddy_model"] = cached_model
    if not pending:
        return 0

    rows = []
    for trace_id, dt, model, hit, miss, comp in pending:
        price = pricing.get_price(model, config, dt)
        cost = pricing.calc_cost(
            {"prompt_cache_hit_tokens": hit,
             "prompt_cache_miss_tokens": miss,
             "completion_tokens": comp}, price)
        key = "cb:%s" % trace_id
        rows.append((key, dt, model, hit, miss, comp, cost))
    return storage.add_external_requests(rows)


def run(config: dict, settings: dict, state: dict, stop_event):
    """后台线程入口：周期增量同步 CodeBuddy 日志用量。"""
    global _LOG
    cb = config.get("codebuddy") or {}
    data_dir = os.path.dirname(storage._db_path or "")
    if data_dir:
        _LOG = os.path.join(data_dir, "codebuddy_sync.log")
    interval = max(5, int(cb.get("sync_interval_seconds", 10)))
    state["codebuddy_sync"] = {"enabled": bool(cb.get("enabled", True)), "total_added": 0,
                               "last_added": 0, "last_time": None, "error": None}
    _log(f"同步线程启动，间隔={interval}秒，目录={_logs_dir(config)}")

    while not stop_event.wait(interval):
        if not (config.get("codebuddy") or {}).get("enabled", True):
            state["codebuddy_sync"]["enabled"] = False  # 设置页关闭后线程常驻等待，重新开启即恢复
            state["codebuddy_sync"]["error"] = None
            continue
        try:
            added = sync_once(config, settings)
            info = state["codebuddy_sync"]
            info["enabled"] = True  # 恢复同步时标记启用（设置页开关状态同步）
            if added:
                info["total_added"] = info.get("total_added", 0) + added
                info["last_added"] = added
            info["last_time"] = datetime.now().strftime("%H:%M:%S")
            info["error"] = None
            _log(f"本轮新增={added} 累计={info.get('total_added', 0)}")
        except Exception as exc:
            state["codebuddy_sync"]["error"] = str(exc)
            _log("ERROR: " + str(exc))
