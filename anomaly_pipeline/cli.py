"""Command-line entry point for the batch pipeline.

    python -m anomaly_pipeline.cli data/sample_transactions.csv --labels data/sample_labels.csv
"""

from __future__ import annotations

import argparse
import json
import sys

from .models import Severity
from .pipeline import (
    evaluate,
    filter_alerts,
    load_labels,
    load_transactions_csv,
    run_pipeline,
    write_alerts_json,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Flag suspicious transactions in a CSV file.")
    parser.add_argument("input", help="CSV with transaction_id, account_id, timestamp, amount, merchant, country")
    parser.add_argument("--output", help="write alerts to this JSON file")
    parser.add_argument("--min-severity", default="LOW", help="LOW, MEDIUM, HIGH or CRITICAL (default LOW)")
    parser.add_argument("--labels", help="optional ground-truth CSV; prints precision and recall")
    parser.add_argument("--top", type=int, default=10, help="how many top alerts to print (default 10)")
    args = parser.parse_args(argv)

    try:
        min_severity = Severity.parse(args.min_severity)
        transactions, errors = load_transactions_csv(args.input)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = run_pipeline(transactions, errors=errors)
    alerts = filter_alerts(result.alerts, min_severity)

    print(f"Transactions scored : {result.total}")
    print(f"Rows skipped (bad)  : {len(result.errors)}")
    print(f"Alerts (>= {min_severity.name:<8}): {len(alerts)}")
    print("All alerts by severity:")
    for name, count in result.by_severity.items():
        print(f"  {name:<9}{count}")
    for message in result.errors[:5]:
        print(f"  skipped {message}", file=sys.stderr)

    if alerts and args.top > 0:
        print(f"\nTop {min(args.top, len(alerts))} alerts (highest score first):")
        for alert in alerts[: args.top]:
            txn = alert.transaction
            print(f"  {alert.alert_id} {alert.severity.name:<8} score={alert.score:<3} "
                  f"{txn.transaction_id} acct={txn.account_id} {txn.amount:,.2f} {txn.country}")
            for reason in alert.reasons:
                print(f"      + {reason.points:>2}  {reason.code}: {reason.description}")

    if args.output:
        write_alerts_json(alerts, args.output)
        print(f"\nAlerts written to {args.output}")

    if args.labels:
        try:
            metrics = evaluate(result.alerts, result.total, load_labels(args.labels))
        except (OSError, KeyError) as exc:
            print(f"error reading labels: {exc}", file=sys.stderr)
            return 2
        print("\nEvaluation against ground-truth labels (synthetic data):")
        print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
