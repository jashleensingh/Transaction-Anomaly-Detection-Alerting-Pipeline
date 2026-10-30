"""Data models and input validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Mapping, Tuple


class ValidationError(ValueError):
    """Raised when an input record is malformed."""


class DuplicateTransactionError(ValidationError):
    """Raised when a transaction_id has already been processed."""


class Severity(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value: Any) -> "Severity":
        try:
            return cls[str(value).strip().upper()]
        except KeyError:
            names = ", ".join(s.name for s in cls if s is not cls.NONE)
            raise ValidationError(f"unknown severity {value!r}; expected one of: {names}") from None


_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_CHANNELS = ("online", "pos", "atm")
_REQUIRED = ("transaction_id", "account_id", "timestamp", "amount", "merchant", "country")
MAX_AMOUNT = 1_000_000_000.0


def parse_timestamp(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp. Naive values are treated as UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text[-1:] in ("Z", "z"):  # Python < 3.11 cannot parse a trailing Z
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise ValidationError(f"timestamp {value!r} is not ISO-8601") from None
    else:
        raise ValidationError("timestamp must be an ISO-8601 string")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean_id(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value.strip()):
        raise ValidationError(
            f"{name} must be 1-64 characters of letters, digits, '.', '_', ':' or '-'"
        )
    return value.strip()


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    account_id: str
    timestamp: datetime
    amount: float
    merchant: str
    country: str
    channel: str = "online"
    category: str = "other"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Transaction":
        if not isinstance(data, Mapping):
            raise ValidationError("each transaction must be a JSON object")
        missing = [k for k in _REQUIRED if k not in data or data[k] in (None, "")]
        if missing:
            raise ValidationError("missing required field(s): " + ", ".join(missing))

        raw_amount = data["amount"]
        if isinstance(raw_amount, bool):
            raise ValidationError("amount must be a number")
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            raise ValidationError(f"amount {raw_amount!r} is not a number") from None
        if not math.isfinite(amount) or amount <= 0 or amount > MAX_AMOUNT:
            raise ValidationError(f"amount must be greater than 0 and at most {MAX_AMOUNT:,.0f}")

        merchant = str(data["merchant"]).strip()
        if not merchant or len(merchant) > 100:
            raise ValidationError("merchant must be 1-100 characters")

        country = str(data["country"]).strip().upper()
        if not _COUNTRY_RE.match(country):
            raise ValidationError("country must be a 2-letter ISO code such as 'CA'")

        channel = str(data.get("channel") or "online").strip().lower()
        if channel not in _CHANNELS:
            raise ValidationError(f"channel must be one of: {', '.join(_CHANNELS)}")

        category = str(data.get("category") or "other").strip().lower()[:50] or "other"

        return cls(
            transaction_id=_clean_id("transaction_id", data["transaction_id"]),
            account_id=_clean_id("account_id", data["account_id"]),
            timestamp=parse_timestamp(data["timestamp"]),
            amount=round(amount, 2),
            merchant=merchant,
            country=country,
            channel=channel,
            category=category,
        )

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "account_id": self.account_id,
            "timestamp": self.timestamp.isoformat(),
            "amount": self.amount,
            "merchant": self.merchant,
            "country": self.country,
            "channel": self.channel,
            "category": self.category,
        }


@dataclass(frozen=True)
class Reason:
    """One check that fired, and how many points it added to the risk score."""

    code: str
    description: str
    points: int
    kind: str  # "rule" or "statistical"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "kind": self.kind,
            "points": self.points,
            "description": self.description,
        }


@dataclass(frozen=True)
class Alert:
    alert_id: str
    transaction: Transaction
    score: int
    severity: Severity
    reasons: Tuple[Reason, ...]
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity.name,
            "score": self.score,
            "created_at": self.created_at.isoformat(),
            "transaction": self.transaction.to_dict(),
            "reasons": [r.to_dict() for r in self.reasons],
        }
