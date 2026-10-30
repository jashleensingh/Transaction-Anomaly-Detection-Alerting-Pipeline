"""Starts the real API behind TLS and talks to it over HTTPS."""

import ssl
import threading
from pathlib import Path

import pytest
import requests
from werkzeug.serving import make_server

from anomaly_pipeline.api import create_app
from anomaly_pipeline.certs import build_ssl_context, generate_self_signed
from anomaly_pipeline.server import main as server_main


@pytest.fixture
def tls_server(tmp_path):
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    generate_self_signed(cert, key)
    server = make_server("127.0.0.1", 0, create_app(api_key="k"), threaded=True,
                         ssl_context=build_ssl_context(cert, key))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{server.server_port}", cert, server.server_port
    server.shutdown()
    thread.join(timeout=5)


def test_https_with_trusted_cert_works(tls_server):
    url, cert, _ = tls_server
    r = requests.get(f"{url}/health", verify=str(cert), timeout=5)
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_full_flow_over_tls(tls_server):
    url, cert, _ = tls_server
    headers = {"X-API-Key": "k"}
    body = {"transaction_id": "T1", "account_id": "A", "timestamp": "2026-09-10T12:00:00Z",
            "amount": 15000, "merchant": "Gem Store", "country": "CA"}
    r = requests.post(f"{url}/transactions", json=body, headers=headers, verify=str(cert), timeout=5)
    assert r.status_code == 200 and r.json()["results"][0]["flagged"]
    alerts = requests.get(f"{url}/alerts", headers=headers, verify=str(cert), timeout=5).json()
    assert alerts["total"] == 1 and alerts["alerts"][0]["reasons"]


def test_untrusted_self_signed_cert_is_rejected_by_default(tls_server):
    url, _, _ = tls_server
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(f"{url}/health", timeout=5)


def test_plain_http_to_tls_port_fails(tls_server):
    _, _, port = tls_server
    with pytest.raises(requests.exceptions.RequestException):
        requests.get(f"http://127.0.0.1:{port}/health", timeout=5)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_old_tls_versions_refused(tls_server):
    _, cert, port = tls_server
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(str(cert))
    ctx.maximum_version = ssl.TLSVersion.TLSv1_1
    import socket
    with pytest.raises((ssl.SSLError, OSError)):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            ctx.wrap_socket(sock, server_hostname="127.0.0.1")


def test_key_file_permissions(tmp_path):
    cert, key = tmp_path / "c.crt", tmp_path / "c.key"
    generate_self_signed(cert, key)
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert b"BEGIN CERTIFICATE" in cert.read_bytes()


def test_server_refuses_to_start_without_certificate(tmp_path, capsys):
    code = server_main(["--cert", str(tmp_path / "no.crt"), "--key", str(tmp_path / "no.key")])
    assert code == 2 and "Certificate or key not found" in capsys.readouterr().err
