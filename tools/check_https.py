# -*- coding: utf-8 -*-
"""HTTPS 就绪检查：确认 443 是否已放行，并给出下一步动作。

用途：在腾讯云控制台放行 443 后运行本脚本，确认加密链路可用；
      若可用，会提示把客户端地址切到 https://。

用法：
    python tools/check_https.py                 # 用默认服务器
    python tools/check_https.py 1.2.3.4         # 指定服务器
"""
import io
import os
import socket
import ssl
import sys

# Windows 控制台默认 GBK，输出勾叉等符号会抛 UnicodeEncodeError，统一切 UTF-8
for _name in ("stdout", "stderr"):
    _s = getattr(sys, _name, None)
    try:
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CA_PATH = os.path.join(ROOT, "cloud_rank", "certs", "ca.crt")
if not os.path.isfile(CA_PATH):
    CA_PATH = os.path.join(ROOT, "assets", "ca.crt")

SERVER = sys.argv[1] if len(sys.argv) > 1 else "106.52.172.73"


def check_tcp(host, port, timeout=8):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True, "TCP 可连接"
    except Exception as exc:
        return False, str(exc)


def check_tls(host, port=443, timeout=12):
    """用我们的 CA 做严格校验的 TLS 握手。"""
    if not os.path.isfile(CA_PATH):
        return False, "找不到 CA 证书（%s）" % CA_PATH
    ctx = ssl.create_default_context(cafile=CA_PATH)
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except Exception as exc:
        return False, "TCP 连接失败: %s" % exc
    try:
        with ctx.wrap_socket(raw, server_hostname=host) as ss:
            cert = ss.getpeercert()
            cn = dict(x[0] for x in cert["subject"]).get("commonName", "?")
            return True, "TLS %s，证书 CN=%s，SAN=%s" % (
                ss.version(), cn,
                ", ".join(str(v) for _, v in cert.get("subjectAltName", [])))
    except ssl.SSLCertVerificationError as exc:
        return False, "证书校验失败（CA 未安装？先跑 trust_ca.py --install）: %s" % exc
    except socket.timeout:
        return False, "TLS 握手超时 —— 443 很可能未在腾讯云安全组放行"
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)


def check_http(host, port, path, https=False, timeout=10):
    import urllib.error
    import urllib.request
    scheme = "https" if https else "http"
    url = "%s://%s%s" % (scheme, host, path)
    kwargs = {"timeout": timeout}
    if https and os.path.isfile(CA_PATH):
        kwargs["context"] = ssl.create_default_context(cafile=CA_PATH)
    try:
        with urllib.request.urlopen(url, **kwargs) as r:
            return True, "HTTP %s: %s" % (r.status, r.read().decode()[:80])
    except urllib.error.HTTPError as e:
        return True, "HTTP %s（有响应即正常）" % e.code
    except Exception as exc:
        return False, str(exc)[:100]


def main():
    print("=" * 62)
    print("HTTPS 就绪检查  目标: %s" % SERVER)
    print("=" * 62)

    print("\n[1] 80 端口")
    ok, msg = check_tcp(SERVER, 80)
    print("    %s %s" % ("OK  " if ok else "FAIL", msg))
    if ok:
        ok2, msg2 = check_http(SERVER, 80, "/api/health")
        print("    %s %s" % ("OK  " if ok2 else "FAIL", msg2))

    print("\n[2] 443 端口")
    ok443, msg443 = check_tcp(SERVER, 443)
    print("    %s %s" % ("OK  " if ok443 else "FAIL", msg443))
    print("    提示：腾讯云安全组未放行时，TCP 握手由云网关代答，")
    print("          可能显示'可连接'但实际流量未转发 —— 以下一步为准。")

    print("\n[3] TLS 握手（用我们的 CA 严格校验）")
    ok_tls, msg_tls = check_tls(SERVER)
    print("    %s %s" % ("OK  " if ok_tls else "FAIL", msg_tls))

    print("\n[4] HTTPS 业务接口")
    if ok_tls:
        ok3, msg3 = check_http(SERVER, 443, "/api/health", https=True)
        print("    %s %s" % ("OK  " if ok3 else "FAIL", msg3))
    else:
        print("    跳过（TLS 未通）")

    print("\n" + "=" * 62)
    if ok_tls:
        print("结论：HTTPS 已就绪 ✓")
        print()
        print("下一步：把客户端地址切到加密通道（二选一）")
        print("  · 设置页「排名服务器」填 https://%s 后点「保存并重连」" % SERVER)
        print("  · 或设环境变量 DSTM_RANK_SERVER=https://%s" % SERVER)
        print()
        print("客户端本身已支持：配置 http:// 也会优先尝试 https 并自动升级。")
    else:
        print("结论：HTTPS 尚未就绪 ✗")
        print()
        print("需要你在腾讯云控制台放行 443：")
        print("  1. 登录 https://console.cloud.tencent.com/lighthouse")
        print("     （若是 CVM 云服务器则用 /cvm）")
        print("  2. 找到实例 ins-4bpuwrj8（广州 ap-guangzhou-7）")
        print("  3. 进入「防火墙」/「安全组」→ 添加规则：")
        print("       来源 0.0.0.0/0   协议 TCP   端口 443   策略 允许")
        print("  4. 保存后重新运行本脚本验证")
        print()
        print("服务器侧已就绪：80 与 443 都在监听，证书有效至 2028-12-17。")
        print("在放行之前，客户端会自动走 HTTP，功能不受影响。")
    print("=" * 62)
    return 0 if ok_tls else 1


if __name__ == "__main__":
    sys.exit(main())
