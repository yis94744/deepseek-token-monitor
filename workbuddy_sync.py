# -*- coding: utf-8 -*-
"""WorkBuddy 数据源同步模块。

设计目标：不加任何转发跳数、不增加 API 延迟、不改 WorkBuddy 的任何配置。
WorkBuddy CLI 把每次模型调用以 JSON 行追加到
~/.workbuddy/projects/<项目目录名>/<会话ID>.jsonl，
其中携带真实用量的字段是 providerData.rawUsage（OpenAI 兼容格式）：

  {
    "prompt_tokens": ...,                输入总 token
    "completion_tokens": ...,            输出 token
    "total_tokens": ...,
    "prompt_cache_hit_tokens": ...,      输入·缓存命中
    "prompt_cache_miss_tokens": ...,     输入·缓存未命中
    "credit": ...                        内部点数（本项目不用）
  }

本模块增量读取各会话 jsonl，把每次调用导入 usage.db；
计费口径与其他数据源一致：按 config.json 的人民币单价用 token 数重算。
只提取 usage 数值，不读取任何对话内容。
"""
import json
import os
from datetime import datetime

import pricing
import storage

_LOG = None  # 日志文件路径，由 run() 初始化


def _projects_dir(config: dict) -> str:
    """定位 WorkBuddy 项目会话目录：config.workbuddy.projects_dir > ~/.workbuddy/projects。"""
    path = (config.get("workbuddy") or {}).get("projects_dir")
    if path:
        return os.path.expandvars(os.path.expanduser(str(path)))
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "projects")


def _log(text: str):
    """同步过程日志（仅排查用），写入 data 目录的 workbuddy_sync.log。"""
    if not _LOG:
        return
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + text + "\n")
    except Exception:
        pass


def _iter_session_files(projects_dir: str):
    """递归列出所有会话 jsonl 文件。"""
    if not os.path.isdir(projects_dir):
        return
    for root, _dirs, files in os.walk(projects_dir):
        for f in files:
            if f.endswith(".jsonl"):
                yield os.path.join(root, f)


def _parse_usage(d: dict):
    """从一行会话事件中提取 (hit, miss, comp)，无有效用量返回 None。

    WorkBuddy 的 rawUsage 在 providerData.rawUsage，仅部分消息行携带；
    字段口径与 DeepSeek 一致：prompt_cache_hit_tokens / prompt_cache_miss_tokens /
    completion_tokens。
    """
    pd = d.get("providerData") or {}
    ru = pd.get("rawUsage")
    if not isinstance(ru, dict):
        return None
    hit = int(ru.get("prompt_cache_hit_tokens") or 0)
    miss = int(ru.get("prompt_cache_miss_tokens") or 0)
    comp = int(ru.get("completion_tokens") or 0)
    if hit + miss + comp <= 0:
        return None
    return hit, miss, comp


def _to_local(ts_ms):
    """毫秒时间戳转本地 naive datetime，失败返回 None。"""
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000.0)
    except Exception:
        return None


def sync_once(config: dict, settings: dict) -> int:
    """同步一轮：增量读取所有 WorkBuddy 会话 jsonl 的用量，返回新增条数。

    游标（字节偏移 + 文件大小）存 settings["workbuddy_cursor"]；
    文件被滚动/重写（大小变小）时从头读，去重键 wb:<会话>:<消息ID> 保证不重复。
    """
    projects_dir = _projects_dir(config)
    if not os.path.isdir(projects_dir):
        return 0
    cursor = settings.get("workbuddy_cursor") or {}
    if not isinstance(cursor, dict):
        cursor = {}

    pending = []  # (session, msg_id, dt, model, hit, miss, comp)
    seen_paths = set()  # 本轮见到的文件（用于清理已删除文件的游标，防 settings 膨胀）
    for path in sorted(_iter_session_files(projects_dir)):
        seen_paths.add(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        pos = cursor.get(path, {})
        offset = int(pos.get("offset", 0) or 0)
        old_size = int(pos.get("size", 0) or 0)
        if offset > size or old_size > size:
            offset = 0  # 文件被重写/裁剪变小：从头读，去重键保证不重复
        if offset == size:
            continue
        try:
            # 二进制逐行读取，只消费以 \n 结尾的完整行：文件尾不完整的半行
            # （WorkBuddy 正在写入）留到下一轮，避免半行被消费后该条记录永久丢失。
            with open(path, "rb") as f:
                f.seek(offset)
                consumed = offset
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break  # 半行：等下轮写入方补全后再读
                    consumed += len(raw)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue  # 解析失败（异常行）：跳过，不影响其余记录
                    usage = _parse_usage(d)
                    if not usage:
                        continue
                    dt = _to_local(d.get("timestamp"))
                    if dt is None:
                        continue
                    pd = d.get("providerData") or {}
                    session = str(d.get("sessionId") or pd.get("sessionId") or "unknown")
                    msg_id = str(d.get("id") or ("%s:%s" % (d.get("timestamp"), usage)))
                    model = pd.get("model") or config.get("unknown_model_fallback",
                                                          "deepseek-v4-flash")
                    pending.append((session, msg_id, dt, str(model), usage[0], usage[1], usage[2]))
                offset = consumed  # 新偏移只推进到最后一个完整行（文件追加时从上次处继续）
        except Exception as exc:
            _log("读取失败 %s: %s" % (path, exc))
        cursor[path] = {"offset": offset, "size": size}

    # 清理已被删除的会话文件的游标，防止 settings.json 无限膨胀
    for gone in [p for p in cursor if p not in seen_paths]:
        del cursor[gone]
    settings["workbuddy_cursor"] = cursor
    if not pending:
        return 0

    rows = []
    for session, msg_id, dt, model, hit, miss, comp in pending:
        price = pricing.get_price(model, config, dt)
        cost = pricing.calc_cost(
            {"prompt_cache_hit_tokens": hit,
             "prompt_cache_miss_tokens": miss,
             "completion_tokens": comp}, price)
        key = "wb:%s:%s" % (session, msg_id)
        rows.append((key, dt, model, hit, miss, comp, cost))
    return storage.add_external_requests(rows)


def run(config: dict, settings: dict, state: dict, stop_event):
    """后台线程入口：周期增量同步 WorkBuddy 会话用量。"""
    global _LOG
    wb = config.get("workbuddy") or {}
    data_dir = os.path.dirname(storage._db_path or "")
    if data_dir:
        _LOG = os.path.join(data_dir, "workbuddy_sync.log")
    interval = max(5, int(wb.get("sync_interval_seconds", 10)))
    state["workbuddy_sync"] = {"enabled": bool(wb.get("enabled", True)), "total_added": 0,
                               "last_added": 0, "last_time": None, "error": None}
    _log(f"同步线程启动，间隔={interval}秒，目录={_projects_dir(config)}")

    while not stop_event.wait(interval):
        if not (config.get("workbuddy") or {}).get("enabled", True):
            state["workbuddy_sync"]["enabled"] = False  # 设置页关闭后线程常驻等待，重新开启即恢复
            state["workbuddy_sync"]["error"] = None
            continue
        try:
            added = sync_once(config, settings)
            info = state["workbuddy_sync"]
            info["enabled"] = True  # 恢复同步时标记启用（设置页开关状态同步）
            if added:
                info["total_added"] = info.get("total_added", 0) + added
                info["last_added"] = added
            info["last_time"] = datetime.now().strftime("%H:%M:%S")
            info["error"] = None
            _log(f"本轮新增={added} 累计={info.get('total_added', 0)}")
        except Exception as exc:
            state["workbuddy_sync"]["error"] = str(exc)
            _log("ERROR: " + str(exc))
