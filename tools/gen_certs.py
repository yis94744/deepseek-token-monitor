# -*- coding: utf-8 -*-
"""重新生成证书：补上 Authority Key Identifier（链校验必需）。

问题：原证书只有 SubjectKeyIdentifier，缺少 AuthorityKeyIdentifier，
OpenSSL 在做链校验时直接报 "Missing Authority Key Identifier"。
根因是签发时没调用 .add_extension(AuthorityKeyIdentifier(...))。
"""
import ipaddress
import os
import shutil
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

# 备份旧的（万一要回滚）
for name in ("ca.crt", "ca.key", "server.crt", "server.key", "ca_fingerprint.txt"):
    src = os.path.join(OUT, name)
    if os.path.isfile(src):
        shutil.copy2(src, src + ".bak")


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
ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())

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
    .add_extension(ca_ski, critical=False)
    # 自签 CA 也要带 AKI（指向自己的 SKI），否则部分校验器报错
    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
        ca_key.public_key()), critical=False)
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
san = [x509.IPAddress(ipaddress.ip_address(SERVER_IP)),
       x509.DNSName("localhost"),
       x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]

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
    .add_extension(x509.SubjectKeyIdentifier.from_public_key(srv_key.public_key()),
                   critical=False)
    # ★ 关键修复：链校验需要 AKI 指向签发者
    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
        ca_key.public_key()), critical=False)
    .sign(ca_key, hashes.SHA256())
)
write_key(os.path.join(OUT, "server.key"), srv_key)
write_cert(os.path.join(OUT, "server.crt"), srv_cert)

fp = ca_cert.fingerprint(hashes.SHA256()).hex().upper()
fp_fmt = ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))
with open(os.path.join(OUT, "ca_fingerprint.txt"), "w", encoding="utf-8") as f:
    f.write(fp_fmt + "\n")

print("证书已重新生成（含 AKI）")
for name in ("ca.crt", "server.crt"):
    c = x509.load_pem_x509_certificate(open(os.path.join(OUT, name), "rb").read())
    print("  %s 扩展: %s" % (name, ", ".join(e.oid._name for e in c.extensions)))
print()
print("CA 指纹(SHA-256):", fp_fmt)
