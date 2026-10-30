import csv
import json
from pathlib import Path

import pytest

from anomaly_pipeline.cli import main as cli_main
from anomaly_pipeline.models import DuplicateTransactionError, Severity
from anomaly_pipeline.pipeline import (
    evaluate, filter_alerts, load_labels, load_transactions_csv, run_pipeline, write_alerts_json,
)
from anomaly_pipeline.service import AnomalyService
from conftest import make_txn

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample_transactions.csv"
LABELS = ROOT / "data" / "sample_labels.csv"


def test_service_records_alert_with_reasons():
    svc = AnomalyService()
    out = svc.process([make_txn(1, amount=15_000)])
    assert out[0].flagged and out[0].alert_id == "A-000001"
    alert = svc.get_alert("A-000001")
    assert alert.severity >= Severity.MEDIUM and alert.reasons


def test_service_clean_transaction_creates_no_alert():
    svc = AnomalyService()
    out = svc.process([make_txn(1, amount=20)])
    assert not out[0].flagged and len(svc.store) == 0


def test_repeated_id_in_batch_rejected_atomically():
    svc = AnomalyService()
    with pytest.raises(DuplicateTransactionError):
        svc.process([make_txn(1), make_txn(2), make_txn(1)])
    assert svc.stats()["transactions_processed"] == 0


def test_repeated_id_across_calls_rejected():
    svc = AnomalyService()
    svc.process([make_txn(1)])
    with pytest.raises(DuplicateTransactionError):
        svc.process([make_txn(1)])


def test_query_filters_and_sorting():
    svc = AnomalyService()
    svc.process([make_txn(1, account="A", amount=15_000), make_txn(2, account="B", amount=5_500, minutes=1)])
    alerts, total = svc.query_alerts(limit=10)
    assert total == 2 and alerts[0].score >= alerts[1].score
    only_b, total_b = svc.query_alerts(account_id="B", limit=10)
    assert total_b == 1 and only_b[0].transaction.account_id == "B"
    high, _ = svc.query_alerts(min_severity=Severity.HIGH, limit=10)
    assert all(a.severity >= Severity.HIGH for a in high)
    page, _ = svc.query_alerts(limit=1, offset=1)
    assert len(page) == 1


def test_csv_loader_skips_bad_rows(tmp_path):
    path = tmp_path / "t.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["transaction_id", "account_id", "timestamp", "amount", "merchant", "country"])
        w.writerow(["T1", "A1", "2026-09-01T10:00:00Z", "20.00", "Cafe", "CA"])
        w.writerow(["T2", "A1", "2026-09-01T10:05:00Z", "-5", "Cafe", "CA"])
        w.writerow(["T3", "A1", "not-a-time", "20", "Cafe", "CA"])
    txns, errors = load_transactions_csv(path)
    assert len(txns) == 1 and len(errors) == 2
    assert errors[0].startswith("line 3") and errors[1].startswith("line 4")


def test_pipeline_orders_by_time():
    # Submitted newest-first; the pipeline must still treat the earliest as first.
    txns = [make_txn(i, minutes=10 - i, amount=40) for i in range(1, 8)]
    result = run_pipeline(txns)
    assert result.total == 7 and result.errors == []


def test_pipeline_on_sample_data_catches_injected_anomalies():
    txns, errors = load_transactions_csv(SAMPLE)
    assert errors == [] and len(txns) > 2000
    result = run_pipeline(txns)
    metrics = evaluate(result.alerts, result.total, load_labels(LABELS))
    # Sample data is synthetic and seeded, so these bounds are stable.
    assert metrics["precision"] >= 0.7
    assert metrics["recall"] >= 0.6
    assert metrics["false_positive_rate"] <= 0.01
    by_type = metrics["flagged_by_type"]
    for kind in ("large_amount", "geo_jump", "night_foreign", "duplicate_charge", "amount_outlier"):
        found, of = map(int, by_type[kind].split("/"))
        assert found == of, kind


def test_pipeline_is_deterministic():
    txns, _ = load_transactions_csv(SAMPLE)
    a = run_pipeline(txns)
    b = run_pipeline(txns)
    assert [(x.transaction.transaction_id, x.score) for x in a.alerts] == \
           [(x.transaction.transaction_id, x.score) for x in b.alerts]


def test_filter_and_write_json(tmp_path):
    txns, _ = load_transactions_csv(SAMPLE)
    result = run_pipeline(txns)
    high = filter_alerts(result.alerts, Severity.HIGH)
    assert all(a.severity >= Severity.HIGH for a in high)
    out = tmp_path / "nested" / "alerts.json"
    write_alerts_json(high, out)
    data = json.loads(out.read_text())
    assert len(data) == len(high)
    assert all(item["reasons"] for item in data)


def test_cli_runs_and_writes_output(tmp_path, capsys):
    out = tmp_path / "alerts.json"
    code = cli_main([str(SAMPLE), "--output", str(out), "--labels", str(LABELS), "--min-severity", "MEDIUM", "--top", "2"])
    text = capsys.readouterr().out
    assert code == 0 and out.exists()
    assert "Transactions scored" in text and "precision" in text


def test_cli_reports_missing_file(capsys):
    assert cli_main(["does-not-exist.csv"]) == 2
    assert "error" in capsys.readouterr().err


def test_cli_rejects_bad_severity(capsys):
    assert cli_main([str(SAMPLE), "--min-severity", "SCARY"]) == 2
