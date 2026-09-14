# -*- coding: utf-8 -*-
"""一键启用 GitHub Actions CI。

背景：推送用的 gh token 缺少 workflow scope，GitHub 拒绝写入
.github/workflows/**（这是平台硬限制，不是脚本问题）。本脚本引导你
完成一次授权，然后把 CI 文件就位并推送。

用法（在项目根目录）：
    python tools/enable_ci.py

它会依次：
  1. 检查当前 token 是否已有 workflow scope；有则直接跳到第 4 步
  2. 打印需要你执行的授权命令（gh auth refresh）
  3. 等你确认后重新检查
  4. 把 .github/ci-workflow.yml 移到 .github/workflows/ci.yml 并提交推送
"""
import io
import os
import shutil
import subprocess
import sys

for _n in ("stdout", "stderr"):
    _s = getattr(sys, _n, None)
    try:
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, ".github", "ci-workflow.yml")
DST_DIR = os.path.join(ROOT, ".github", "workflows")
DST = os.path.join(DST_DIR, "ci.yml")


def gh(args, timeout=60):
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "找不到 gh 命令（请先安装 GitHub CLI）"
    except Exception as exc:
        return -1, str(exc)


def has_workflow_scope():
    code, out = gh(["auth", "status"])
    return "workflow" in out


def main():
    if not os.path.isfile(SRC) and not os.path.isfile(DST):
        print("找不到 CI 文件（.github/ci-workflow.yml）")
        return 1
    if os.path.isfile(DST):
        print("CI 已经就位：.github/workflows/ci.yml")
        print("（若 GitHub 上仍看不到 Actions，请确认已推送到远端）")
        return 0

    print("=" * 62)
    print("启用 GitHub Actions CI")
    print("=" * 62)

    if has_workflow_scope():
        print("\n[1] 当前 token 已含 workflow scope ✓，无需重新授权")
    else:
        print("\n[1] 当前 token 缺少 workflow scope（GitHub 不允许写入 workflows/）")
        print("\n     请复制执行下面这条命令完成授权：")
        print("\n         gh auth refresh -h github.com -s workflow\n")
        print("     它会打开浏览器让你确认，完成后回到这里继续。")
        try:
            input("\n     完成后按回车继续（或 Ctrl+C 取消）... ")
        except EOFError:
            print("\n     （非交互环境，无法等待；请手动执行上面的命令后重跑本脚本）")
            return 1
        if not has_workflow_scope():
            print("\n     仍未检测到 workflow scope —— 请确认授权已完成，然后重跑本脚本。")
            return 2
        print("     ✓ 已获得 workflow scope")

    print("\n[2] 把 CI 文件就位")
    os.makedirs(DST_DIR, exist_ok=True)
    shutil.move(SRC, DST)
    print("     %s -> %s" % (os.path.relpath(SRC, ROOT), os.path.relpath(DST, ROOT)))

    print("\n[3] 提交并推送")
    for args in (["add", "-A"],
                 ["commit", "-m", "ci: 启用 GitHub Actions（迁移到 workflows/）"]):
        r = subprocess.run(["git"] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT)
        print("     git %s -> %s" % (args[0], (r.stdout or r.stderr or "").strip()[:120]))

    code, tok = gh(["auth", "token"])
    token = tok.strip()
    if not token:
        print("     取不到 token，请手动 push")
        return 1
    url = "https://oauth2:%s@github.com/yis94744/deepseek-token-monitor.git" % token
    r = subprocess.run(["git", "push", url, "main"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    ok = r.returncode == 0
    print("     push -> %s" % ((r.stdout or r.stderr or "").strip()[-160:]))
    print()
    if ok:
        print("=" * 62)
        print("完成 ✓  CI 已启用")
        print("查看运行：https://github.com/yis94744/deepseek-token-monitor/actions")
        print("=" * 62)
        return 0
    print("推送失败，请检查上面的错误信息")
    return 1


if __name__ == "__main__":
    sys.exit(main())
