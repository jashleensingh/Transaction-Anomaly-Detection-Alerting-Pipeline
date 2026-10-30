import pytest

from anomaly_pipeline.models import Severity, Transaction, ValidationError


def valid(**overrides):
    data = {
        "transaction_id": "T1", "account_id": "A1", "timestamp": "2026-09-10T12:00:00Z",
        "amount": 25.5, "merchant": "Cafe", "country": "ca",
    }
    data.update(overrides)
    return data


def test_valid_transaction_is_normalised():
    t = Transaction.from_dict(valid())
    assert t.country == "CA"
    assert t.channel == "online"
    assert t.timestamp.utcoffset().total_seconds() == 0
    assert t.amount == 25.5


def test_naive_timestamp_is_treated_as_utc():
    t = Transaction.from_dict(valid(timestamp="2026-09-10T12:00:00"))
    assert t.timestamp.hour == 12 and t.timestamp.tzinfo is not None


def test_offset_timestamp_converted_to_utc():
    t = Transaction.from_dict(valid(timestamp="2026-09-10T12:00:00-04:00"))
    assert t.timestamp.hour == 16


@pytest.mark.parametrize("field", ["transaction_id", "account_id", "timestamp", "amount", "merchant", "country"])
def test_missing_required_field(field):
    data = valid()
    del data[field]
    with pytest.raises(ValidationError, match=field):
        Transaction.from_dict(data)


@pytest.mark.parametrize("amount", [0, -5, "abc", float("nan"), float("inf"), True, 2e9, None])
def test_bad_amounts_rejected(amount):
    with pytest.raises(ValidationError):
        Transaction.from_dict(valid(amount=amount))


@pytest.mark.parametrize("country", ["CAN", "C", "12", "C A"])
def test_bad_country_rejected(country):
    with pytest.raises(ValidationError):
        Transaction.from_dict(valid(country=country))


def test_bad_timestamp_rejected():
    with pytest.raises(ValidationError):
        Transaction.from_dict(valid(timestamp="yesterday"))


def test_bad_channel_rejected():
    with pytest.raises(ValidationError):
        Transaction.from_dict(valid(channel="carrier-pigeon"))


def test_bad_id_rejected():
    with pytest.raises(ValidationError):
        Transaction.from_dict(valid(transaction_id="has spaces"))


def test_non_object_rejected():
    with pytest.raises(ValidationError):
        Transaction.from_dict("not a dict")


def test_severity_parse():
    assert Severity.parse("high") is Severity.HIGH
    with pytest.raises(ValidationError):
        Severity.parse("scary")
