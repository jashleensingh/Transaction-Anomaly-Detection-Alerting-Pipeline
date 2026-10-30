"""Small statistical helpers (no third-party dependencies)."""

from __future__ import annotations

import math
import statistics
from typing import Sequence

# Floor for the robust spread, in natural-log units (~15% change in amount).
# Without it, an account whose amounts are almost identical would flag
# tiny differences as huge outliers.
MIN_LOG_SCALE = 0.15


def modified_z_score(value: float, history: Sequence[float]) -> float:
    """Robust z-score of ``value`` against ``history``, computed on log amounts.

    Uses the median and the median absolute deviation (MAD), so a few earlier
    outliers do not distort the baseline. Working in log space suits money
    amounts, which are right-skewed. Positive results mean "larger than usual".
    """
    if not history:
        raise ValueError("history must not be empty")
    logs = [math.log(v) for v in history]
    center = statistics.median(logs)
    deviations = [abs(v - center) for v in logs]
    mad = statistics.median(deviations)
    if mad > 0:
        scale = 1.4826 * mad
    else:
        scale = 1.2533 * (sum(deviations) / len(deviations))
    scale = max(scale, MIN_LOG_SCALE)
    return (math.log(value) - center) / scale


def upper_fence(values: Sequence[float], multiplier: float) -> float:
    """Q3 + multiplier * IQR. Needs at least two values."""
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q3 + multiplier * (q3 - q1)
