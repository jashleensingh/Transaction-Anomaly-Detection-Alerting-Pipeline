"""Batch pipeline: CSV in, scored alerts out."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .config import ScoringConfig
from .models import Alert, Severity, Transaction, ValidationError
from .service import AnomalyService


@dataclass
class PipelineResult:
    total: int
    alerts: List[Alert]
    by_severity: Dict[str, int]
    errors: List[str] = field(default_factory=list)

    @property
    def flagged(self) -> int:
        return len(self.alerts)


def load_transactions_csv(path) -> Tuple[List[Transaction], List[str]]:
    """Read transactions from a CSV file. Bad rows are skipped and reported."""
    transactions: List[Transaction] = []
    errors: List[str] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for line_no, row in enumerate(reader, start=2):  # header is line 1
            try:
                transactions.append(Transaction.from_dict(row))
            except ValidationError as exc:
                errors.append(f"line {line_no}: {exc}")
    return transactions, errors


def run_pipeline(
    transactions: Iterable[Transaction],
    config: Optional[ScoringConfig] = None,
    errors: Optional[List[str]] = None,
) -> PipelineResult:
    """Score transactions in time order and return every alert raised."""
    ordered = sorted(transactions, key=lambda t: (t.timestamp, t.transaction_id))
    service = AnomalyService(config)
    service.process(ordered)
    alerts, _ = service.store.query(limit=len(service.store) or 1)
    return PipelineResult(
        total=len(ordered),
        alerts=alerts,
        by_severity=service.store.counts_by_severity(),
        errors=list(errors or []),
    )


def filter_alerts(alerts: Iterable[Alert], min_severity: Severity) -> List[Alert]:
    return [a for a in alerts if a.severity >= min_severity]


def write_alerts_json(alerts: Iterable[Alert], path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump([a.to_dict() for a in alerts], handle, indent=2)


def load_labels(path) -> Dict[str, str]:
    """Read ground-truth labels: transaction_id -> anomaly type."""
    with open(path, newline="", encoding="utf-8") as handle:
        return {row["transaction_id"]: row["anomaly_type"] for row in csv.DictReader(handle)}


def evaluate(alerts: Iterable[Alert], total: int, labels: Dict[str, str]) -> dict:
    """Compare flagged transactions with ground truth."""
    flagged: Set[str] = {a.transaction.transaction_id for a in alerts}
    truth: Set[str] = set(labels)
    tp = len(flagged & truth)
    fp = len(flagged - truth)
    fn = len(truth - flagged)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    per_type: Dict[str, str] = {}
    for kind in sorted(set(labels.values())):
        members = {t for t, k in labels.items() if k == kind}
        per_type[kind] = f"{len(members & flagged)}/{len(members)}"
    return {
        "labelled_anomalies": len(truth),
        "flagged": len(flagged),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "normal_transactions": total - len(truth),
        "false_positive_rate": round(fp / (total - len(truth)), 4) if total > len(truth) else 0.0,
        "flagged_by_type": per_type,
    }
