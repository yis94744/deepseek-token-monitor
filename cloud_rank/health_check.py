# -*- coding: utf-8 -*-
"""CloudRank 外部健康检查（供计划任务每 5 分钟调用）

判定"半死"状态的两条独立判据：
  1. HTTP 探活：GET /api/board 必须返回 401（未带 token）——有响应即监听活着。
  2. 业务判据：需要带上一个有效 token 调 /api/admin/overview 或 /api/board，
     校验返回的 day 是否等于当天（北京时间）。若落后 >= 1 天，说明"进程活着
     但业务已停止"（正是 2026-09-09 那种僵活场景），必须告警。

告警方式：写 health.log + 可选推送到 webhook（企业微信/钉钉/Bark 任一）。
连续失败 3 次才告警，避免网络抖动误报；恢复时补一条"已恢复"。

环境变量：
  CLOUDRANK_URL          默认 http://127.0.0.1
  CLOUDRANK_HEALTH_TOKEN 可选，带 token 做业务判据（不带则只做 HTTP 探活）
  CLOUDRANK_WEBHOOK      可选，告警推送地址
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("CLOUDRANK_URL", "http://127.0.0.1").rstrip("/")
TOKEN = os.environ.get("CLOUDRANK_HEALTH_TOKEN", "").strip()
WEBHOOK = os.environ.get("CLOUDRANK_WEBHOOK", "").strip()

# 本地配置文件（health_config.json）：存放 url / 业务判据 token / webhook 地址。
# 单独成文件是为了让凭据不进仓库；环境变量优先级更高，便于临时覆盖。
_CFG_PATH = os.path.join(BASE, "health_config.json")
try:
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _cfg = json.load(_f)
    URL = (os.environ.get("CLOUDRANK_URL") or _cfg.get("url") or URL).rstrip("/")
    TOKEN = (os.environ.get("CLOUDRANK_HEALTH_TOKEN") or _cfg.get("token") or TOKEN).strip()
    WEBHOOK = (os.environ.get("CLOUDRANK_WEBHOOK") or _cfg.get("webhook") or WEBHOOK).strip()
except Exception:
    pass

LOG = os.path.join(BASE, "health.log")
STATE = os.path.join(BASE, "health_state.json")
TZ = timezone(timedelta(hours=8))


def log(msg):
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + str(msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG) > 1024 * 1024:
            os.replace(LOG, LOG + ".1")
    except Exception:
        pass


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"fails": 0, "alerted": False}


def save_state(st):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def push(text):
    if not WEBHOOK:
        return
    try:
        body = json.dumps({"msgtype": "text", "text": {"content": text}}).encode("utf-8")
        req = urllib.request.Request(WEBHOOK, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).read()
        log("  已推送告警")
    except Exception as exc:
        log("  告警推送失败: %s" % exc)


def probe_http():
    """HTTP 探活：任何 HTTP 状态码都算"监听活着"。"""
    try:
        with urllib.request.urlopen(URL + "/api/board", timeout=8) as r:
            return True, "HTTP %s" % r.status
    except urllib.error.HTTPError as e:
        return True, "HTTP %s" % e.code
    except Exception as exc:
        return False, str(exc)


def probe_business():
    """业务判据：带 token 校验 day 是否落后。返回 (ok, detail)。"""
    if not TOKEN:
        return True, "（未配置 token，跳过业务判据）"
    req = urllib.request.Request(URL + "/api/board",
                                 headers={"Authorization": "Bearer " + TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return False, "业务探活失败: %s" % exc
    day = str(data.get("day") or "")
    today = datetime.now(TZ).date().isoformat()
    if not day:
        return False, "响应缺少 day 字段"
    if day != today:
        return False, "服务端 day=%s 落后本地 %s（业务已停止）" % (day, today)
    return True, "day=%s, 上榜 %s 人" % (day, len(data.get("board") or []))


def main():
    st = load_state()
    ok1, d1 = probe_http()
    ok2, d2 = (True, "") if ok1 else (False, "跳过")
    if ok1:
        ok2, d2 = probe_business()

    healthy = ok1 and ok2
    if healthy:
        if st.get("alerted"):
            log("服务已恢复 (%s; %s)" % (d1, d2))
            push("[CloudRank] 服务已恢复正常 (%s)" % d2)
        st["fails"] = 0
        st["alerted"] = False
        save_state(st)
        return 0

    st["fails"] = int(st.get("fails") or 0) + 1
    detail = ("HTTP: " + d1) if not ok1 else ("业务: " + d2)
    log("健康检查失败(%s/3) %s" % (st["fails"], detail))
    if st["fails"] >= 3 and not st.get("alerted"):
        st["alerted"] = True
        msg = "[CloudRank] 服务异常已持续 %s 次检查：%s\n地址: %s" % (st["fails"], detail, URL)
        log("触发告警: " + msg.replace("\n", " | "))
        push(msg)
    save_state(st)
    return 1


if __name__ == "__main__":
    sys.exit(main())
