"""The scoring engine: rule-based and statistical checks, applied in time order.

Each transaction is compared with what the pipeline knew *before* it arrived,
so a transaction is never compared with itself or with the future.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple

from .config import ScoringConfig
from .models import Reason, Severity, Transaction
from .stats import modified_z_score, upper_fence

_HISTORY_LIMIT = 500
_RECENT_LIMIT = 200
_GLOBAL_LIMIT = 5_000
_GLOBAL_REFRESH_EVERY = 25


@dataclass(frozen=True)
class ScoreResult:
    score: int
    severity: Severity
    reasons: Tuple[Reason, ...]

    @property
    def flagged(self) -> bool:
        return self.severity > Severity.NONE


class _AccountState:
    """What we remember about one account."""

    __slots__ = ("amounts", "merchants", "countries", "recent", "last_ts", "last_country")

    def __init__(self) -> None:
        self.amounts: Deque[float] = deque(maxlen=_HISTORY_LIMIT)
        self.merchants: set = set()
        self.countries: Counter = Counter()
        self.recent: Deque[Tuple[datetime, str, float]] = deque(maxlen=_RECENT_LIMIT)
        self.last_ts: Optional[datetime] = None
        self.last_country: Optional[str] = None


def _rule(code: str, description: str, points: int) -> Reason:
    return Reason(code=code, description=description, points=points, kind="rule")


def _stat(code: str, description: str, points: int) -> Reason:
    return Reason(code=code, description=description, points=points, kind="statistical")


class TransactionScorer:
    """Stateful scorer. Feed it transactions in time order via :meth:`score`."""

    def __init__(self, config: Optional[ScoringConfig] = None) -> None:
        self.config = config or ScoringConfig()
        self._accounts: Dict[str, _AccountState] = {}
        self._global: Deque[float] = deque(maxlen=_GLOBAL_LIMIT)
        self._global_fence: Optional[float] = None
        self._global_fence_size = 0

    # ------------------------------------------------------------------ public

    def score(self, txn: Transaction) -> ScoreResult:
        state = self._accounts.setdefault(txn.account_id, _AccountState())
        reasons: List[Reason] = []
        reasons.extend(self._rule_checks(txn, state))
        reasons.extend(self._statistical_checks(txn, state))

        total = min(100, sum(r.points for r in reasons))
        severity = self.config.severity_for(total)
        self._remember(txn, state)
        return ScoreResult(score=total, severity=severity, reasons=tuple(reasons))

    # ------------------------------------------------------------------- rules

    def _rule_checks(self, txn: Transaction, state: _AccountState) -> List[Reason]:
        cfg = self.config
        out: List[Reason] = []
        amount = txn.amount
        established = len(state.amounts) >= cfg.min_history

        if amount >= cfg.very_high_amount:
            out.append(_rule(
                "VERY_HIGH_AMOUNT",
                f"Amount {amount:,.2f} is at or above the very-high limit of {cfg.very_high_amount:,.0f}",
                cfg.very_high_amount_points,
            ))
        elif amount >= cfg.high_amount:
            out.append(_rule(
                "HIGH_AMOUNT",
                f"Amount {amount:,.2f} is at or above the high-amount limit of {cfg.high_amount:,.0f}",
                cfg.high_amount_points,
            ))

        if amount >= cfg.round_amount_min and round(amount, 2) % 1000 == 0:
            out.append(_rule(
                "ROUND_LARGE_AMOUNT",
                f"Large round amount {amount:,.0f} (exact multiple of 1,000)",
                cfg.round_amount_points,
            ))

        if txn.timestamp.hour in cfg.odd_hours and amount >= cfg.odd_hours_min_amount:
            out.append(_rule(
                "ODD_HOURS",
                f"Amount {amount:,.2f} at {txn.timestamp:%H:%M} UTC, during unusual hours",
                cfg.odd_hours_points,
            ))

        if established and state.countries:
            home = state.countries.most_common(1)[0][0]
            if txn.country != home:
                out.append(_rule(
                    "FOREIGN_COUNTRY",
                    f"Country {txn.country} differs from this account's usual country {home}",
                    cfg.foreign_country_points,
                ))

        if established and txn.merchant.lower() not in state.merchants and amount >= cfg.new_merchant_min_amount:
            out.append(_rule(
                "NEW_MERCHANT_HIGH",
                f"First purchase at '{txn.merchant}' and the amount is {amount:,.2f}",
                cfg.new_merchant_points,
            ))

        window = cfg.velocity_window_seconds
        in_window = 1 + sum(
            1 for ts, _, _ in state.recent if 0 <= (txn.timestamp - ts).total_seconds() < window
        )
        if in_window >= cfg.velocity_count:
            out.append(_rule(
                "VELOCITY",
                f"{in_window} transactions within {window // 60} minutes on this account",
                cfg.velocity_points,
            ))

        if (
            state.last_ts is not None
            and state.last_country is not None
            and state.last_country != txn.country
            and abs((txn.timestamp - state.last_ts).total_seconds()) <= cfg.geo_jump_window_seconds
        ):
            minutes = abs((txn.timestamp - state.last_ts).total_seconds()) / 60
            out.append(_rule(
                "GEO_JUMP",
                f"Country changed from {state.last_country} to {txn.country} within {minutes:.0f} minutes",
                cfg.geo_jump_points,
            ))

        merchant = txn.merchant.lower()
        for ts, prev_merchant, prev_amount in state.recent:
            if (
                prev_merchant == merchant
                and prev_amount == amount
                and abs((txn.timestamp - ts).total_seconds()) <= cfg.duplicate_window_seconds
            ):
                out.append(_rule(
                    "DUPLICATE",
                    f"Same merchant and amount ({amount:,.2f}) repeated within {cfg.duplicate_window_seconds} seconds",
                    cfg.duplicate_points,
                ))
                break

        return out

    # ------------------------------------------------------------- statistical

    def _statistical_checks(self, txn: Transaction, state: _AccountState) -> List[Reason]:
        cfg = self.config
        if len(state.amounts) >= cfg.min_history:
            z = modified_z_score(txn.amount, list(state.amounts))
            if z >= cfg.z_extreme:
                return [_stat(
                    "AMOUNT_OUTLIER_EXTREME",
                    f"Amount is extremely high for this account (robust z-score {z:.1f})",
                    cfg.z_extreme_points,
                )]
            if z >= cfg.z_moderate:
                return [_stat(
                    "AMOUNT_OUTLIER",
                    f"Amount is unusually high for this account (robust z-score {z:.1f})",
                    cfg.z_moderate_points,
                )]
            return []

        # New account: not enough history, so compare with everyone else.
        fence = self._global_upper_fence()
        if fence is not None and txn.amount > fence:
            return [_stat(
                "GLOBAL_AMOUNT_OUTLIER",
                f"New account; amount {txn.amount:,.2f} is above the all-accounts fence of {fence:,.2f}",
                cfg.global_outlier_points,
            )]
        return []

    def _global_upper_fence(self) -> Optional[float]:
        cfg = self.config
        size = len(self._global)
        if size < cfg.global_min_samples:
            return None
        if self._global_fence is None or size - self._global_fence_size >= _GLOBAL_REFRESH_EVERY:
            self._global_fence = upper_fence(list(self._global), cfg.global_iqr_multiplier)
            self._global_fence_size = size
        return self._global_fence

    # ------------------------------------------------------------------- state

    def _remember(self, txn: Transaction, state: _AccountState) -> None:
        merchant = txn.merchant.lower()
        state.amounts.append(txn.amount)
        state.merchants.add(merchant)
        state.countries[txn.country] += 1
        state.recent.append((txn.timestamp, merchant, txn.amount))
        state.last_ts = txn.timestamp
        state.last_country = txn.country
        self._global.append(txn.amount)
