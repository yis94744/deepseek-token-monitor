# -*- coding: utf-8 -*-
"""生成本地 CA + 服务器证书（IP SAN），供 CloudRank HTTPS 使用。

为什么用私有 CA 而不是 Let's Encrypt：
  Let's Encrypt 不给裸 IP 签证书（必须是域名），当前服务器没有域名、PTR 也为空。
  私有 CA 方案的 HTTPS 强度与公信 CA 完全相同（同样的 TLS 加密），区别只在于
  客户端要信任一次自签 CA；而且以后若买了域名，随时可换成公信证书（代码不用改）。

产物（写入 cloud_rank/certs/）：
  ca.key / ca.crt      根证书（导入客户端"受信任的根证书颁发机构"）
  server.key/server.crt 服务器证书（含 IP SAN，供 uvicorn 加载）
  ca.crt 的 SHA-256 指纹（供客户端校验）
"""
import ipaddress
import os
import sys
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

SERVER_IP = os.environ.get("CLOUDRANK_IP", "106.52.172.73")
_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(_HERE), "cloud_rank", "certs")
os.makedirs(OUT, exist_ok=True)
now = datetime.now(timezone.utc)


def write_key(path, key):
    with open(path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def write_cert(path, cert):
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


# ---------- 1) 根 CA ----------
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
ca_name = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Capybara Monitor"),
    x509.NameAttribute(NameOID.COMMON_NAME, "Capybara Monitor Root CA"),
])
ca_cert = (
    x509.CertificateBuilder()
    .subject_name(ca_name)
    .issuer_name(ca_name)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(days=1))
    .not_valid_after(now + timedelta(days=3650))
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
    .add_extension(x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=True,
        crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
    .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                   critical=False)
    .sign(ca_key, hashes.SHA256())
)
write_key(os.path.join(OUT, "ca.key"), ca_key)
write_cert(os.path.join(OUT, "ca.crt"), ca_cert)

# ---------- 2) 服务器证书 ----------
srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
srv_name = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Capybara Monitor"),
    x509.NameAttribute(NameOID.COMMON_NAME, SERVER_IP),
])
san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
try:
    san.insert(0, x509.IPAddress(ipaddress.ip_address(SERVER_IP)))
except Exception as exc:
    print("警告：IP SAN 添加失败", exc)

srv_cert = (
    x509.CertificateBuilder()
    .subject_name(srv_name)
    .issuer_name(ca_name)
    .public_key(srv_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(days=1))
    .not_valid_after(now + timedelta(days=825))
    .add_extension(x509.SubjectAlternativeName(san), critical=False)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=True,
        data_encipherment=False, key_agreement=False, key_cert_sign=False,
        crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
    .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                   critical=False)
    .sign(ca_key, hashes.SHA256())
)
write_key(os.path.join(OUT, "server.key"), srv_key)
write_cert(os.path.join(OUT, "server.crt"), srv_cert)

# ---------- 3) 指纹（供客户端校验） ----------
fp = ca_cert.fingerprint(hashes.SHA256()).hex().upper()
fp_fmt = ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))
with open(os.path.join(OUT, "ca_fingerprint.txt"), "w", encoding="utf-8") as f:
    f.write(fp_fmt + "\n")

print("证书已生成到:", OUT)
for name in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, name)
    print("  %-20s %6d bytes" % (name, os.path.getsize(p)))
print()
print("服务器证书 SAN:", ", ".join(str(s.value) for s in san))
print("有效期至:", (now + timedelta(days=825)).strftime("%Y-%m-%d"))
print("CA 指纹(SHA-256):", fp_fmt)
