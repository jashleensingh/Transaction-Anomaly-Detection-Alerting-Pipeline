#!/usr/bin/env python3
"""Send the sample CSV to a running HTTPS server and print the alerts it raised.

    python scripts/demo_client.py --cacert certs/server.crt [--api-key KEY]
"""

import argparse
import csv
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://127.0.0.1:5443")
    parser.add_argument("--cacert", default="certs/server.crt", help="certificate to trust")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--csv", default="data/sample_transactions.csv")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    with open(args.csv, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))  # file is already in time order

    flagged = 0
    for i in range(0, len(rows), args.batch_size):
        batch = rows[i:i + args.batch_size]
        resp = requests.post(f"{args.url}/transactions", json=batch, headers=headers,
                             verify=args.cacert, timeout=30)
        if resp.status_code != 200:
            print(f"batch starting at row {i + 1} failed: {resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        flagged += sum(1 for r in resp.json()["results"] if r["flagged"])
    print(f"Sent {len(rows)} transactions over TLS; {flagged} were flagged.")

    stats = requests.get(f"{args.url}/stats", headers=headers, verify=args.cacert, timeout=30).json()
    print("Server stats:", stats)
    top = requests.get(f"{args.url}/alerts", params={"limit": args.top}, headers=headers,
                       verify=args.cacert, timeout=30).json()
    print(f"\nTop {args.top} alerts:")
    for alert in top["alerts"]:
        t = alert["transaction"]
        print(f"  {alert['alert_id']} {alert['severity']:<8} score={alert['score']:<3} "
              f"{t['transaction_id']} {t['account_id']} {t['amount']:,.2f} {t['country']}")
        for reason in alert["reasons"]:
            print(f"      + {reason['points']:>2} {reason['code']}: {reason['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
