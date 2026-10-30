from datetime import timedelta, timezone, datetime

import pytest

from anomaly_pipeline.config import ScoringConfig
from anomaly_pipeline.models import Severity
from anomaly_pipeline.scoring import TransactionScorer
from conftest import BASE, make_txn, warm_up


def codes(result):
    return {r.code for r in result.reasons}


@pytest.fixture
def scorer():
    s = TransactionScorer()
    warm_up(s)
    return s


def test_normal_transaction_not_flagged(scorer):
    r = scorer.score(make_txn(1, amount=52))
    assert r.score == 0 and r.severity is Severity.NONE and not r.flagged and r.reasons == ()


def test_high_amount(scorer):
    r = scorer.score(make_txn(1, amount=6_200))
    assert "HIGH_AMOUNT" in codes(r) and "VERY_HIGH_AMOUNT" not in codes(r)


def test_very_high_amount_replaces_high(scorer):
    r = scorer.score(make_txn(1, amount=12_345.67))
    assert "VERY_HIGH_AMOUNT" in codes(r) and "HIGH_AMOUNT" not in codes(r)


def test_round_large_amount(scorer):
    assert "ROUND_LARGE_AMOUNT" in codes(scorer.score(make_txn(1, amount=3_000)))
    assert "ROUND_LARGE_AMOUNT" not in codes(scorer.score(make_txn(2, amount=3_001)))
    assert "ROUND_LARGE_AMOUNT" not in codes(scorer.score(make_txn(3, amount=500)))


def test_odd_hours_needs_amount(scorer):
    night = datetime(2026, 9, 10, 2, 30, tzinfo=timezone.utc)
    assert "ODD_HOURS" in codes(scorer.score(make_txn(1, amount=800, when=night)))
    assert "ODD_HOURS" not in codes(scorer.score(make_txn(2, amount=60, when=night + timedelta(hours=1))))


def test_foreign_country_only_after_history():
    fresh = TransactionScorer()
    assert "FOREIGN_COUNTRY" not in codes(fresh.score(make_txn(1, country="JP")))
    warmed = TransactionScorer()
    warm_up(warmed)
    assert "FOREIGN_COUNTRY" in codes(warmed.score(make_txn(2, country="JP")))


def test_new_merchant_high_amount(scorer):
    r = scorer.score(make_txn(1, amount=1_500, merchant="Brand New Shop"))
    assert "NEW_MERCHANT_HIGH" in codes(r)
    r2 = scorer.score(make_txn(2, amount=1_500, merchant="maple grocer", minutes=60))
    assert "NEW_MERCHANT_HIGH" not in codes(r2)  # known merchant, case-insensitive


def test_velocity(scorer):
    results = [scorer.score(make_txn(i, amount=20 + i, minutes=i)) for i in range(1, 7)]
    assert "VELOCITY" not in codes(results[3])  # 4th in the window
    assert "VELOCITY" in codes(results[4])      # 5th in the window
    assert "VELOCITY" in codes(results[5])


def test_velocity_window_expires(scorer):
    for i in range(1, 5):
        scorer.score(make_txn(i, amount=20 + i, minutes=i))
    late = scorer.score(make_txn(9, amount=33, minutes=60))
    assert "VELOCITY" not in codes(late)


def test_geo_jump(scorer):
    scorer.score(make_txn(1, country="CA", minutes=0))
    r = scorer.score(make_txn(2, country="JP", amount=70, minutes=20))
    assert "GEO_JUMP" in codes(r)
    far_later = scorer.score(make_txn(3, country="CA", amount=70, minutes=600))
    assert "GEO_JUMP" not in codes(far_later)


def test_duplicate(scorer):
    scorer.score(make_txn(1, amount=210.10, merchant="Bluebird Books", minutes=0))
    r = scorer.score(make_txn(2, amount=210.10, merchant="bluebird books", minutes=1))
    assert "DUPLICATE" in codes(r)
    assert r.severity >= Severity.LOW  # a duplicate charge alone is enough to alert
    later = scorer.score(make_txn(3, amount=210.10, merchant="Bluebird Books", minutes=30))
    assert "DUPLICATE" not in codes(later)


def test_amount_outlier_for_account(scorer):
    r = scorer.score(make_txn(1, amount=900))
    assert codes(r) & {"AMOUNT_OUTLIER", "AMOUNT_OUTLIER_EXTREME"}
    assert all(rr.kind == "statistical" for rr in r.reasons if rr.code.startswith("AMOUNT_OUTLIER"))


def test_global_outlier_for_new_account():
    s = TransactionScorer()
    for i in range(60):  # many accounts with one small purchase each
        s.score(make_txn(i, account=f"OTHER{i}", amount=30 + (i % 20), minutes=i))
    r = s.score(make_txn(999, account="BRANDNEW", amount=2_200, minutes=100))
    assert "GLOBAL_AMOUNT_OUTLIER" in codes(r)


def test_score_is_capped_and_severity_bands():
    s = TransactionScorer()
    warm_up(s)
    night = datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc)
    r = s.score(make_txn(1, amount=50_000, country="JP", merchant="Shady Store", when=night))
    assert r.score == 100 and r.severity is Severity.CRITICAL


def test_points_add_up_to_score(scorer):
    r = scorer.score(make_txn(1, amount=6_000, country="JP", merchant="Gadget Hut"))
    assert r.score == min(100, sum(x.points for x in r.reasons))


def test_every_reason_has_description_and_points(scorer):
    r = scorer.score(make_txn(1, amount=6_000, country="JP", merchant="Gadget Hut"))
    assert r.reasons
    for reason in r.reasons:
        assert reason.description and reason.points > 0 and reason.kind in {"rule", "statistical"}


def test_accounts_are_independent():
    s = TransactionScorer()
    warm_up(s, account="A")
    r = s.score(make_txn(1, account="B", country="JP", amount=70))
    assert "FOREIGN_COUNTRY" not in codes(r)  # B has no history


@pytest.mark.parametrize("score,expected", [
    (0, Severity.NONE), (19, Severity.NONE), (20, Severity.LOW), (39, Severity.LOW),
    (40, Severity.MEDIUM), (59, Severity.MEDIUM), (60, Severity.HIGH), (79, Severity.HIGH),
    (80, Severity.CRITICAL), (100, Severity.CRITICAL),
])
def test_severity_bands(score, expected):
    assert ScoringConfig().severity_for(score) is expected


def test_config_validation():
    with pytest.raises(ValueError):
        ScoringConfig(low_threshold=50, medium_threshold=40)
    with pytest.raises(ValueError):
        ScoringConfig(z_moderate=7, z_extreme=6)
    with pytest.raises(ValueError):
        ScoringConfig(min_history=0)


def test_custom_config_changes_behaviour():
    s = TransactionScorer(ScoringConfig(high_amount=100, very_high_amount=200))
    warm_up(s)
    assert "HIGH_AMOUNT" in codes(s.score(make_txn(1, amount=150)))
