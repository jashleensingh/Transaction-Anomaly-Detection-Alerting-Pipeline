from datetime import datetime, timedelta, timezone

import pytest

from anomaly_pipeline.models import Transaction

BASE = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def make_txn(n=1, account="ACC1", amount=50.0, merchant="Maple Grocer", country="CA",
             when=None, minutes=0, channel="pos"):
    ts = when or (BASE + timedelta(minutes=minutes))
    return Transaction(
        transaction_id=f"T{n:06d}", account_id=account, timestamp=ts, amount=amount,
        merchant=merchant, country=country, channel=channel, category="retail",
    )


@pytest.fixture
def txn():
    return make_txn


def warm_up(scorer, account="ACC1", count=8, amount=50.0, start_n=1000):
    """Give an account a normal history (one purchase per day, midday, Canada)."""
    results = []
    for i in range(count):
        t = make_txn(
            start_n + i, account=account, amount=amount + (i % 3),
            when=BASE - timedelta(days=count - i), country="CA",
        )
        results.append(scorer.score(t))
    return results
