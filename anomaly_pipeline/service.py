"""Thread-safe service that scores transactions and stores the alerts."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from .config import ScoringConfig
from .models import Alert, DuplicateTransactionError, Reason, Severity, Transaction
from .scoring import TransactionScorer


@dataclass(frozen=True)
class ScoredTransaction:
    transaction: Transaction
    score: int
    severity: Severity
    reasons: Tuple[Reason, ...]
    alert_id: Optional[str]

    @property
    def flagged(self) -> bool:
        return self.alert_id is not None

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction.transaction_id,
            "score": self.score,
            "severity": self.severity.name,
            "flagged": self.flagged,
            "alert_id": self.alert_id,
            "reasons": [r.to_dict() for r in self.reasons],
        }


class AlertStore:
    """In-memory alert storage."""

    def __init__(self) -> None:
        self._alerts: Dict[str, Alert] = {}
        self._counter = 0

    def add(self, txn: Transaction, score: int, severity: Severity, reasons: Sequence[Reason]) -> Alert:
        self._counter += 1
        alert = Alert(
            alert_id=f"A-{self._counter:06d}",
            transaction=txn,
            score=score,
            severity=severity,
            reasons=tuple(reasons),
            created_at=datetime.now(timezone.utc),
        )
        self._alerts[alert.alert_id] = alert
        return alert

    def get(self, alert_id: str) -> Optional[Alert]:
        return self._alerts.get(alert_id)

    def query(
        self,
        min_severity: Optional[Severity] = None,
        account_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Alert], int]:
        """Return (page, total_matching). Highest score first, then oldest first."""
        matches = [
            a for a in self._alerts.values()
            if (min_severity is None or a.severity >= min_severity)
            and (account_id is None or a.transaction.account_id == account_id)
        ]
        matches.sort(key=lambda a: (-a.score, a.alert_id))
        return matches[offset: offset + limit], len(matches)

    def counts_by_severity(self) -> Dict[str, int]:
        counts = {s.name: 0 for s in Severity if s is not Severity.NONE}
        for alert in self._alerts.values():
            counts[alert.severity.name] += 1
        return counts

    def __len__(self) -> int:
        return len(self._alerts)


class AnomalyService:
    """Scores transactions in the order given and records alerts."""

    def __init__(self, config: Optional[ScoringConfig] = None) -> None:
        self.config = config or ScoringConfig()
        self.scorer = TransactionScorer(self.config)
        self.store = AlertStore()
        self._seen_ids: set = set()
        self._processed = 0
        self._lock = threading.Lock()

    def process(self, transactions: Sequence[Transaction]) -> List[ScoredTransaction]:
        """Score a batch. All-or-nothing: a repeated transaction_id rejects the whole batch."""
        with self._lock:
            ids = [t.transaction_id for t in transactions]
            if len(set(ids)) != len(ids):
                raise DuplicateTransactionError("the batch contains a repeated transaction_id")
            already = [i for i in ids if i in self._seen_ids]
            if already:
                raise DuplicateTransactionError(f"transaction_id already processed: {already[0]}")

            results: List[ScoredTransaction] = []
            for txn in transactions:
                outcome = self.scorer.score(txn)
                alert_id = None
                if outcome.flagged:
                    alert_id = self.store.add(txn, outcome.score, outcome.severity, outcome.reasons).alert_id
                self._seen_ids.add(txn.transaction_id)
                self._processed += 1
                results.append(ScoredTransaction(txn, outcome.score, outcome.severity, outcome.reasons, alert_id))
            return results

    def stats(self) -> dict:
        with self._lock:
            return {
                "transactions_processed": self._processed,
                "alerts_total": len(self.store),
                "alerts_by_severity": self.store.counts_by_severity(),
            }

    def query_alerts(self, **kwargs):
        with self._lock:
            return self.store.query(**kwargs)

    def get_alert(self, alert_id: str) -> Optional[Alert]:
        with self._lock:
            return self.store.get(alert_id)
