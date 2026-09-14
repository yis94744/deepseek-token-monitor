# -*- coding: utf-8 -*-
"""CloudRank 数据保留与定期清理（生产版）

设计目标：**排名服务只需要"当天"的数据**，历史日数据纯属占位。
所以这里采用"保留窗口 + 归档 + 硬清理"三段式，把库长期压在很小规模。

清理策略（默认值，可用环境变量覆盖）：
  - daily_usage   : 保留最近 RETENTION_DAYS 天（默认 60）。更早的按天归档到
                    archive/daily_usage_<YYYY-MM>.jsonl 后从主表删除。
  - admin/login   : 无独立表，跳过。
  - 归档目录      : archive/ ，单文件不超过 ARCHIVE_MAX_MB（默认 20MB）。
  - 归档总保留    : ARCHIVE_KEEP_MONTHS（默认 24 个月），超期删除。
  - 表优化        : 清理后对 daily_usage 执行 OPTIMIZE TABLE 回收空间。
  - 日志清理      : server.log / watchdog.log / *.old_* 超过 LOG_KEEP_DAYS 的轮转副本删除。

用法：
  python cleanup.py                # 按默认策略清理（由计划任务每天调用）
  python cleanup.py --dry-run      # 只报告将要做什么，不改动数据
  python cleanup.py --days 90      # 临时指定保留天数
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(BASE, "archive")

DB_URL = os.environ.get(
    "CLOUDRANK_DB",
    "mysql+pymysql://root:root@127.0.0.1:3306/cloud_rank?charset=utf8")

RETENTION_DAYS = int(os.environ.get("CLOUDRANK_RETENTION_DAYS", "60"))
ARCHIVE_KEEP_MONTHS = int(os.environ.get("CLOUDRANK_ARCHIVE_KEEP_MONTHS", "24"))
LOG_KEEP_DAYS = int(os.environ.get("CLOUDRANK_LOG_KEEP_DAYS", "14"))
ARCHIVE_MAX_MB = int(os.environ.get("CLOUDRANK_ARCHIVE_MAX_MB", "20"))

LOG_FILE = os.path.join(BASE, "cleanup.log")


def log(msg):
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + str(msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # cleanup.log 自轮转：超过 2MB 就换一份
    try:
        if os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except Exception:
        pass


def get_engine():
    import sqlalchemy as sa
    return sa.create_engine(DB_URL, pool_pre_ping=True)


# ---------------- 1) 归档 + 清理 daily_usage ----------------
def archive_and_purge_daily(engine, days, dry_run=False):
    """把 cutoff 之前的 daily_usage 行按月归档到 jsonl，然后删除主表行。"""
    import sqlalchemy as sa

    cutoff = date.today() - timedelta(days=days)
    log("daily_usage: 保留最近 %s 天（cutoff=%s）" % (days, cutoff))

    with engine.connect() as conn:
        old_rows = conn.execute(sa.text(
            "SELECT user_id, day, tokens, updated_at FROM daily_usage "
            "WHERE day < :cutoff ORDER BY day ASC"), {"cutoff": cutoff}).fetchall()

    if not old_rows:
        log("  无需归档，主表已符合保留窗口")
        return 0, 0

    log("  待归档行数 = %s" % len(old_rows))

    if dry_run:
        return len(old_rows), 0

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    buckets = {}
    for user_id, day, tokens, updated_at in old_rows:
        buckets.setdefault(day.strftime("%Y-%m"), []).append({
            "user_id": int(user_id),
            "day": day.isoformat(),
            "tokens": int(tokens),
            "updated_at": updated_at.isoformat() if updated_at else None,
        })

    written = 0
    for month, rows in sorted(buckets.items()):
        path = os.path.join(ARCHIVE_DIR, "daily_usage_%s.jsonl" % month)
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        written += len(rows)
        log("  归档 %s -> %s (%s 行)" % (month, os.path.basename(path), len(rows)))

    with engine.begin() as conn:
        result = conn.execute(sa.text(
            "DELETE FROM daily_usage WHERE day < :cutoff"), {"cutoff": cutoff})
        deleted = result.rowcount
    log("  已删除主表行数 = %s" % deleted)

    try:
        with engine.begin() as conn:
            conn.execute(sa.text("OPTIMIZE TABLE daily_usage"))
        log("  OPTIMIZE TABLE daily_usage 完成")
    except Exception as exc:
        log("  OPTIMIZE 跳过: %s" % exc)

    return len(old_rows), deleted


# ---------------- 2) 归档文件轮转 ----------------
def prune_archives(dry_run=False):
    """归档目录总量与时效控制：超期月份删除，单文件过大则切分提示。"""
    if not os.path.isdir(ARCHIVE_DIR):
        log("archive: 目录不存在，跳过")
        return 0

    cutoff_month = (date.today() - timedelta(days=ARCHIVE_KEEP_MONTHS * 31)).strftime("%Y-%m")
    removed = 0
    for name in sorted(os.listdir(ARCHIVE_DIR)):
        if not name.endswith(".jsonl"):
            continue
        month = name.rsplit("_", 1)[-1].replace(".jsonl", "")
        path = os.path.join(ARCHIVE_DIR, name)
        size_mb = os.path.getsize(path) / 1024 / 1024
        if month < cutoff_month:
            if dry_run:
                log("  [dry-run] 将删除超期归档 %s" % name)
            else:
                os.remove(path)
                log("  删除超期归档 %s" % name)
            removed += 1
        elif size_mb > ARCHIVE_MAX_MB:
            log("  提示：%s 已达 %.1f MB（超过 %s MB 阈值，建议压缩）"
                % (name, size_mb, ARCHIVE_MAX_MB))
    if not removed:
        log("archive: 无需清理（保留 %s 个月）" % ARCHIVE_KEEP_MONTHS)
    return removed


# ---------------- 3) 日志清理 ----------------
def prune_logs(dry_run=False):
    """删除过期的日志轮转副本（server.log.N / *.old_* / watchdog.log.1）。"""
    cutoff = datetime.now() - timedelta(days=LOG_KEEP_DAYS)
    removed = 0
    for name in os.listdir(BASE):
        if not (name.endswith(".log.1") or ".old_" in name or name.endswith(".log.2")
                or name.endswith(".log.3") or name.endswith(".log.4") or name.endswith(".log.5")):
            continue
        path = os.path.join(BASE, name)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
        except Exception:
            continue
        if mtime < cutoff:
            if dry_run:
                log("  [dry-run] 将删除过期日志 %s" % name)
            else:
                os.remove(path)
                log("  删除过期日志 %s" % name)
            removed += 1
    if not removed:
        log("logs: 无需清理（保留 %s 天）" % LOG_KEEP_DAYS)
    return removed


# ---------------- 4) 统计输出 ----------------
def report(engine):
    import sqlalchemy as sa
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT COUNT(*) AS n, MIN(day) AS min_day, MAX(day) AS max_day, "
            "SUM(tokens) AS total FROM daily_usage")).fetchone()
        users = conn.execute(sa.text("SELECT COUNT(*) FROM users")).scalar()
    size = 0
    for dirpath, _dirnames, filenames in os.walk(ARCHIVE_DIR):
        for fn in filenames:
            try:
                size += os.path.getsize(os.path.join(dirpath, fn))
            except Exception:
                pass
    log("当前状态: users=%s, daily_usage=%s 行 [%s ~ %s], tokens 合计=%s, 归档=%.2f MB"
        % (users, rows[0], rows[1], rows[2], rows[3] or 0, size / 1024 / 1024))


def main():
    ap = argparse.ArgumentParser(description="CloudRank 数据保留与清理")
    ap.add_argument("--days", type=int, default=RETENTION_DAYS,
                    help="daily_usage 保留天数（默认 %s）" % RETENTION_DAYS)
    ap.add_argument("--dry-run", action="store_true", help="只报告，不修改数据")
    args = ap.parse_args()

    log("=" * 60)
    log("CloudRank cleanup 开始 (dry_run=%s, 保留 %s 天)" % (args.dry_run, args.days))

    try:
        engine = get_engine()
    except Exception as exc:
        log("数据库连接失败: %s" % exc)
        return 1

    report(engine)
    archive_and_purge_daily(engine, args.days, args.dry_run)
    prune_archives(args.dry_run)
    prune_logs(args.dry_run)
    report(engine)
    log("CloudRank cleanup 结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
