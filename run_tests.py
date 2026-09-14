# -*- coding: utf-8 -*-
"""统一测试入口：发现并运行根目录下所有 test_*.py。

原状态（2026-09-14）：仓库里有 8 个 test_*.py，但只有 2 个进了自动运行
（靠 .tmp/run_tests_wrapper.py 手工串起来），其余 6 个长期无人执行。
本脚本自动发现全部测试，任何一个失败都会以非 0 退出，可直接用于 CI。

用法：
    python run_tests.py            # 跑全部
    python run_tests.py -v         # 显示每个测试的完整输出
    python run_tests.py logutil    # 只跑匹配名字的测试
"""
import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Windows 控制台默认 GBK，子进程输出的替换字符会直接抛 UnicodeEncodeError，
# 这里统一把本进程的输出也切成 UTF-8（errors=replace 兜底）。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    try:
        if _stream is not None and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 这些测试需要真实外部环境（GUI/托盘/真实数据源），CI 上跳过
SKIP_ON_CI = {"test_tray_update.py", "test_dash_click.py"}


def discover(pattern: str = "") -> list:
    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "test_*.py")))
    if pattern:
        files = [f for f in files if pattern.lower() in f.lower()]
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", nargs="?", default="", help="只跑名字包含该串的测试")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    on_ci = bool(os.environ.get("CI"))
    files = discover(args.pattern)
    if not files:
        print("没有发现任何测试文件")
        return 1

    passed, failed, skipped = [], [], []
    for name in files:
        if on_ci and name in SKIP_ON_CI:
            skipped.append(name)
            print("SKIP %s（CI 环境无 GUI，跳过）" % name)
            continue
        print("=" * 60)
        print("RUN  %s" % name)
        print("=" * 60)
        proc = subprocess.run([sys.executable, os.path.join(HERE, name)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=HERE)
        out = (proc.stdout or "") + (proc.stderr or "")
        if args.verbose or proc.returncode != 0:
            print(out)
        else:
            # 只显示 PASS 行，保持输出简洁（编码异常不应中断整个测试流程）
            try:
                for line in out.splitlines():
                    if line.startswith("PASS") or "PASSED" in line:
                        print("  " + line.strip())
            except Exception:
                print("  （输出含无法显示的字符，已跳过）")
        if proc.returncode == 0:
            passed.append(name)
            print("--> OK\n")
        else:
            failed.append(name)
            print("--> FAILED (exit %s)\n" % proc.returncode)

    print("=" * 60)
    print("汇总：通过 %d，失败 %d，跳过 %d" % (len(passed), len(failed), len(skipped)))
    if failed:
        print("失败：%s" % ", ".join(failed))
        return 1
    print("全部测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
