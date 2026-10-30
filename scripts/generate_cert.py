#!/usr/bin/env python3
"""Create a self-signed TLS certificate for local use."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anomaly_pipeline.certs import generate_self_signed  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", default="certs/server.crt")
    parser.add_argument("--key", default="certs/server.key")
    parser.add_argument("--host", action="append", dest="hosts",
                        help="hostname or IP for the certificate (repeatable); default: localhost, 127.0.0.1")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    hosts = tuple(args.hosts) if args.hosts else ("localhost", "127.0.0.1")
    generate_self_signed(args.cert, args.key, hostnames=hosts, days=args.days)
    print(f"Wrote {args.cert} and {args.key} (valid {args.days} days) for: {', '.join(hosts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
