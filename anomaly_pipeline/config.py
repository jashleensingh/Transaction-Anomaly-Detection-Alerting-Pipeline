"""Tunable thresholds and point values for the scoring engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .models import Severity


@dataclass(frozen=True)
class ScoringConfig:
    # --- amount rules ---
    high_amount: float = 5_000.0
    high_amount_points: int = 30
    very_high_amount: float = 10_000.0
    very_high_amount_points: int = 45
    round_amount_min: float = 1_000.0
    round_amount_points: int = 10

    # --- time rule (hours are in UTC) ---
    odd_hours: Tuple[int, ...] = (0, 1, 2, 3, 4)
    odd_hours_min_amount: float = 500.0
    odd_hours_points: int = 15

    # --- history-based rules ---
    min_history: int = 5  # transactions needed before an account has a "normal"
    foreign_country_points: int = 15
    new_merchant_min_amount: float = 1_000.0
    new_merchant_points: int = 10

    # --- behaviour rules ---
    velocity_window_seconds: int = 600
    velocity_count: int = 5
    velocity_points: int = 25
    geo_jump_window_seconds: int = 3_600
    geo_jump_points: int = 30
    duplicate_window_seconds: int = 120
    duplicate_points: int = 20

    # --- statistical checks ---
    z_moderate: float = 4.0
    z_moderate_points: int = 25
    z_extreme: float = 6.0
    z_extreme_points: int = 40
    global_min_samples: int = 30
    global_iqr_multiplier: float = 3.0
    global_outlier_points: int = 20

    # --- severity bands (lower bound of each band, score is capped at 100) ---
    low_threshold: int = 20
    medium_threshold: int = 40
    high_threshold: int = 60
    critical_threshold: int = 80

    def __post_init__(self) -> None:
        bands = (
            self.low_threshold,
            self.medium_threshold,
            self.high_threshold,
            self.critical_threshold,
        )
        if not (0 < bands[0] < bands[1] < bands[2] < bands[3] <= 100):
            raise ValueError("severity thresholds must be increasing and within 1..100")
        if self.min_history < 1:
            raise ValueError("min_history must be at least 1")
        if self.z_moderate >= self.z_extreme:
            raise ValueError("z_moderate must be smaller than z_extreme")

    def severity_for(self, score: int) -> Severity:
        if score >= self.critical_threshold:
            return Severity.CRITICAL
        if score >= self.high_threshold:
            return Severity.HIGH
        if score >= self.medium_threshold:
            return Severity.MEDIUM
        if score >= self.low_threshold:
            return Severity.LOW
        return Severity.NONE
