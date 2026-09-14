# -*- coding: utf-8 -*-
"""CloudRank 数据库备份（每日 mysqldump，保留 N 天）。

背景：MySQL 与服务同机，此前没有任何备份。磁盘故障或误操作会直接丢数据。
本脚本用 mysqldump 导出到 backups/，按天保留，超期自动清理。

用法：
    python backup.py                 # 备份一次
    python backup.py --keep 14       # 指定保留天数
    python backup.py --list          # 列出已有备份
"""
import argparse
import glob
import gzip
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE, "backups")
LOG_FILE = os.path.join(BASE, "backup.log")

DB_HOST = os.environ.get("CLOUDRANK_DB_HOST", "127.0.0.1")
DB_PORT = os.environ.get("CLOUDRANK_DB_PORT", "3306")
DB_USER = os.environ.get("CLOUDRANK_DB_USER", "root")
DB_PASS = os.environ.get("CLOUDRANK_DB_PASS", "root")
DB_NAME = os.environ.get("CLOUDRANK_DB_NAME", "cloud_rank")
KEEP_DAYS = int(os.environ.get("CLOUDRANK_BACKUP_KEEP_DAYS", "7"))
MIN_FREE_MB = int(os.environ.get("CLOUDRANK_BACKUP_MIN_FREE_MB", "500"))


def log(msg):
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + str(msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG_FILE) > 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except Exception:
        pass


def find_mysqldump():
    """定位 mysqldump.exe（MySQL 8 默认安装位置 + PATH）。"""
    found = shutil.which("mysqldump")
    if found:
        return found
    for pat in (r"C:\Program Files\MySQL\MySQL Server *\bin\mysqldump.exe",
                r"C:\Program Files (x86)\MySQL\MySQL Server *\bin\mysqldump.exe",
                r"C:\mysql*\bin\mysqldump.exe"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def free_mb(path):
    try:
        import shutil as _sh
        return _sh.disk_usage(path).free / 1024 / 1024
    except Exception:
        return None


def do_backup(keep_days):
    os.makedirs(BACKUP_DIR, exist_ok=True)

    free = free_mb(BACKUP_DIR)
    if free is not None and free < MIN_FREE_MB:
        log("磁盘空间不足（剩余 %.0f MB < %d MB），跳过备份以免写满" % (free, MIN_FREE_MB))
        return 1

    dump = find_mysqldump()
    if not dump:
        log("找不到 mysqldump.exe —— 请确认 MySQL 已安装并加入 PATH")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = os.path.join(BACKUP_DIR, "cloud_rank_%s.sql" % stamp)
    gz_path = tmp_path + ".gz"

    cmd = [dump, "-h", DB_HOST, "-P", str(DB_PORT), "-u", DB_USER,
           "--single-transaction", "--routines", "--events",
           "--default-character-set=utf8mb4", DB_NAME]
    env = dict(os.environ)
    if DB_PASS:
        env["MYSQL_PWD"] = DB_PASS      # 避免密码出现在命令行（可能被其他进程看到）

    try:
        with open(tmp_path, "wb") as out:
            r = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace")[:300]
            log("mysqldump 失败(exit %s): %s" % (r.returncode, err))
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return 1
    except Exception as exc:
        log("执行 mysqldump 异常: %s" % exc)
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return 1

    # 压缩（MySQL 导出文本压缩比很高）
    try:
        with open(tmp_path, "rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        os.remove(tmp_path)
        size_kb = os.path.getsize(gz_path) / 1024
        log("备份完成: %s (%.1f KB)" % (os.path.basename(gz_path), size_kb))
    except Exception as exc:
        log("压缩失败: %s（保留未压缩文件）" % exc)

    # 清理超期备份
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for path in glob.glob(os.path.join(BACKUP_DIR, "cloud_rank_*.sql*")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(path)) < cutoff:
                os.remove(path)
                removed += 1
        except Exception:
            pass
    if removed:
        log("已清理 %d 个超期备份（保留 %d 天）" % (removed, keep_days))

    total = len(glob.glob(os.path.join(BACKUP_DIR, "cloud_rank_*.sql*")))
    log("当前共 %d 份备份" % total)
    return 0


def list_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "cloud_rank_*.sql*")))
    if not files:
        print("尚无备份")
        return 0
    print("备份目录:", BACKUP_DIR)
    for p in files:
        st = os.stat(p)
        print("  %-40s %8.1f KB  %s" % (
            os.path.basename(p), st.st_size / 1024,
            datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=KEEP_DAYS)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        return list_backups()
    log("开始备份 %s@%s:%s/%s（保留 %d 天）" % (DB_USER, DB_HOST, DB_PORT, DB_NAME, args.keep))
    return do_backup(args.keep)


if __name__ == "__main__":
    sys.exit(main())
