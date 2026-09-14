# -*- coding: utf-8 -*-
"""提交前敏感信息扫描：防止账号/密码/token/本机绝对路径被提交。

背景：本仓库曾把服务器 IP + Administrator + 明文密码写在 deploy_tools.py，
虽然当时被 .gitignore 挡住了，但没有任何自动检查兜底。CI 里跑本脚本可以
在"误提交"发生前拦住。

用法：
    python tools/scan_secrets.py            # 扫描仓库受版本控制的文件
    python tools/scan_secrets.py --all      # 扫描工作区所有文件（含未跟踪）
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (规则名, 正则, 说明)
RULES = [
    ("明文密码赋值",
     re.compile(r"""(?i)(password|passwd|pwd|密码)\s*[:=]\s*["'][^"'\s]{4,}["']"""),
     "疑似硬编码密码"),
    ("SSH 连接凭据",
     re.compile(r"""(?i)(PWD|PASSWORD)\s*=\s*["'][^"'\s]{4,}["']"""),
     "疑似 SSH/数据库凭据"),
    ("API Key",
     # 真实 DeepSeek/OpenAI Key 形如 sk- + 32 位以上随机串；测试里的
     # "sk-abc123456789wxyz" 这类明显是占位（含连续序数/重复模式），
     # 由 _looks_like_placeholder 进一步排除。
     re.compile(r"""sk-[A-Za-z0-9]{16,}"""),
     "疑似 API Key 明文"),
    ("云厂商密钥",
     re.compile(r"""(?i)(AKID|LTAI|AKIA)[A-Za-z0-9]{12,}"""),
     "疑似云厂商 AccessKey"),
    ("本机用户绝对路径",
     re.compile(r"""[A-Za-z]:\\Users\\(?!Administrator\b|<)[^\\\s"']+"""),
     "本机绝对路径（隐私：会泄露用户名）"),
    ("真实邮箱",
     re.compile(r"""\b[\w.+-]+@(qq|163|gmail|outlook|hotmail|foxmail)\.com\b"""),
     "真实邮箱地址"),
    ("私钥文件",
     re.compile(r"""-----BEGIN [A-Z ]*PRIVATE KEY-----"""),
     "私钥内容"),
]

# 允许出现（示例/占位/文档）的上下文
ALLOW_HINTS = (
    "example", "placeholder", "请改成", "改成强密码", "your_", "xxx",
    "******", "<", "scan_secrets", "test_", "RuleId", "password_hash",
    "password_ok", "verify_password", "hash_password", "PASSWORD_HASH",
)

TEXT_EXT = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".bat", ".ps1",
            ".iss", ".spec", ".cfg", ".ini", ".sql", ".sh"}
SKIP_DIRS = {".git", "build", "dist", "__pycache__", "work", ".tmp", "vendor",
             "node_modules", "tools"}


def _iter_files(scan_all: bool):
    if not scan_all:
        try:
            out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace")
            if out.returncode == 0:
                for rel in out.stdout.splitlines():
                    yield os.path.join(ROOT, rel.strip())
                return
        except Exception:
            pass
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in TEXT_EXT:
                yield os.path.join(dirpath, fn)


PLACEHOLDER_HINTS = ("abc123", "123456", "zzz", "xxx", "aaa", "test",
                     "dummy", "fake", "sample", "example", "foo", "bar")


def _looks_like_placeholder(token: str) -> bool:
    """判断疑似 Key 是否明显是占位串（降低测试数据误报）。"""
    low = token.lower()
    if any(h in low for h in PLACEHOLDER_HINTS):
        return True
    # 连续递增/重复字符（如 aaaaaaaa、abcdabcd）视为占位
    body = low.split("-", 1)[-1]
    if len(set(body)) <= 6:
        return True
    for size in (2, 3, 4):
        unit = body[:size]
        if unit and body == (unit * (len(body) // size + 1))[:len(body)]:
            return True
    return False


def scan_line(line: str):
    """返回命中的规则名，未命中返回 None。"""
    low = line.lower()
    for name, rx, _desc in RULES:
        m = rx.search(line)
        if not m:
            continue
        if any(h.lower() in low for h in ALLOW_HINTS):
            continue
        if name == "API Key" and _looks_like_placeholder(m.group(0)):
            continue
        return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="扫描所有文件（含未跟踪）")
    args = ap.parse_args()

    findings = []
    for path in _iter_files(args.all):
        if not os.path.isfile(path):
            continue
        if os.path.splitext(path)[1].lower() not in TEXT_EXT:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    hit = scan_line(line)
                    if hit:
                        rel = os.path.relpath(path, ROOT)
                        findings.append((rel, i, hit, line.strip()[:110]))
        except Exception:
            continue

    if not findings:
        print("敏感信息扫描通过：未发现硬编码凭据/密钥/隐私路径")
        return 0

    print("发现 %d 处疑似敏感信息：" % len(findings))
    for rel, line_no, rule, text in findings:
        print("  %s:%s  [%s]" % (rel, line_no, rule))
        print("      %s" % text)
    print("\n请改为从环境变量/配置文件读取，或加入 .gitignore。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
