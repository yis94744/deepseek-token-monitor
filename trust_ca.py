# -*- coding: utf-8 -*-
"""安装/卸载服务器 CA 到 Windows 受信任根（供 HTTPS 使用）。

为什么需要：服务器没有域名（裸 IP 拿不到公信证书），所以用私有 CA 签发。
把 CA 装进"受信任的根证书颁发机构"后，Windows 与 Python 都会正常校验该
服务器的 HTTPS 证书，且**不会降低其他站点的安全性**（只信任这一张 CA）。

用法（由设置页按钮触发，也可命令行）：
    python trust_ca.py --install <ca.crt 路径>   安装
    python trust_ca.py --uninstall                卸载
    python trust_ca.py --status                   查看是否已安装
"""
import os
import subprocess
import sys

# 中文输出兼容：英文 Windows 控制台默认 cp1252，print 中文会抛
# UnicodeEncodeError（本地中文 Windows 不暴露）。统一切到 UTF-8。
for _name in ("stdout", "stderr"):
    _s = getattr(sys, _name, None)
    try:
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


CA_SUBJECT_CN = "Capybara Monitor Root CA"


def _certutil(args):
    try:
        r = subprocess.run(["certutil"] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return -1, str(exc)


def is_installed() -> bool:
    """检查 CA 是否已在当前用户的受信任根存储中。"""
    code, out = _certutil(["-user", "-store", "Root", CA_SUBJECT_CN])
    return code == 0 and CA_SUBJECT_CN in out


def install(ca_path: str) -> tuple:
    """把 CA 安装到【当前用户】的受信任根（不需要管理员权限）。"""
    if not os.path.isfile(ca_path):
        return False, "找不到证书文件：%s" % ca_path
    code, out = _certutil(["-user", "-addstore", "-f", "Root", ca_path])
    if code == 0 and is_installed():
        return True, "已信任服务器证书"
    return False, (out or "安装失败")[:300]


def uninstall() -> tuple:
    code, out = _certutil(["-user", "-delstore", "Root", CA_SUBJECT_CN])
    if code == 0:
        return True, "已移除服务器证书信任"
    return False, (out or "移除失败")[:300]


if __name__ == "__main__":
    if "--install" in sys.argv:
        idx = sys.argv.index("--install")
        path = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else ""
        ok, msg = install(path)
        print(("OK " if ok else "FAIL ") + msg)
        sys.exit(0 if ok else 1)
    if "--uninstall" in sys.argv:
        ok, msg = uninstall()
        print(("OK " if ok else "FAIL ") + msg)
        sys.exit(0 if ok else 1)
    print("已安装" if is_installed() else "未安装")
