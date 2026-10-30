"""Run the alert API over HTTPS.

    python -m anomaly_pipeline.server --cert certs/server.crt --key certs/server.key
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .api import create_app
from .certs import build_ssl_context, generate_self_signed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve the alert API over TLS.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5443)
    parser.add_argument("--cert", default="certs/server.crt")
    parser.add_argument("--key", default="certs/server.key")
    parser.add_argument("--generate-cert", action="store_true",
                        help="create a self-signed certificate if the files do not exist")
    parser.add_argument("--insecure-http", action="store_true",
                        help="serve plain HTTP (local debugging only)")
    args = parser.parse_args(argv)

    app = create_app(api_key=os.environ.get("ALERT_API_KEY") or None)

    if args.insecure_http:
        print("WARNING: serving plain HTTP. Do not use this outside local debugging.", file=sys.stderr)
        app.run(host=args.host, port=args.port, threaded=True)
        return 0

    cert, key = Path(args.cert), Path(args.key)
    if not (cert.exists() and key.exists()):
        if not args.generate_cert:
            print(
                f"Certificate or key not found ({cert}, {key}).\n"
                "Run `python scripts/generate_cert.py`, or pass --generate-cert.",
                file=sys.stderr,
            )
            return 2
        generate_self_signed(cert, key, hostnames=("localhost", "127.0.0.1"))
        print(f"Generated self-signed certificate: {cert}")

    context = build_ssl_context(cert, key)
    print(f"Serving https://{args.host}:{args.port}  (TLS 1.2+)")
    app.run(host=args.host, port=args.port, ssl_context=context, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
