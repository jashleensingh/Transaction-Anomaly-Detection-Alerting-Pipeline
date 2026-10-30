"""Create a self-signed certificate for local TLS and build a hardened SSL context."""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import ssl
from pathlib import Path
from typing import Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_self_signed(
    cert_path,
    key_path,
    hostnames: Sequence[str] = ("localhost", "127.0.0.1"),
    days: int = 365,
) -> None:
    """Write a self-signed RSA-2048 certificate and private key (key file mode 0600)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Anomaly Pipeline (local dev)"),
        x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0]),
    ])
    sans = []
    for host in hostnames:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            sans.append(x509.DNSName(host))
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_file, key_file = Path(cert_path), Path(key_path)
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    os.chmod(key_file, 0o600)


def build_ssl_context(cert_path, key_path) -> ssl.SSLContext:
    """Server-side TLS context that only accepts TLS 1.2 or newer."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return context
