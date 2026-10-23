"""Exploratory, weekday-matched outlier detection on daily aggregate counts.

An outlier is a *statistical deviation*, not evidence of fraud or an outage.
This is exploratory visualization only, not a Zooniverse security alerting policy.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from statistics import median
from typing import Mapping


@dataclass(frozen=True)
class DailyOutlier:
    """One unusual daily count relative to *earlier matching weekdays*."""

    day: date
    observed: int
    baseline: float
    score: float
    direction: str  # "spike" or "drop"


def find_weekday_outliers(
    daily: Mapping[date, int],
    *,
    lookback_weeks: int = 8,
    minimum_weeks: int = 4,
    threshold: float = 5.0,
    min_relative_change: float = 0.25,
    min_absolute_change: int = 10,
) -> list[DailyOutlier]:
    """Flag large deviations using the previous same weekday as a baseline.

    Requires a continuous, zero-filled series; calendar gaps must not be
    silently treated as missing queries. Uses robust z = difference / (1.4826
    * MAD). A zero-MAD baseline is unscored rather than assigned false
    certainty. Scores are exploratory, not p-values.
    """
    if not 4 <= minimum_weeks <= lookback_weeks <= 12:
        raise ValueError("require 4 <= minimum_weeks <= lookback_weeks <= 12")
    if not isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold must be finite and positive")
    if not isfinite(min_relative_change) or min_relative_change < 0:
        raise ValueError("min_relative_change must be finite and nonnegative")
    if min_absolute_change < 0:
        raise ValueError("min_absolute_change must be nonnegative")
    if not daily:
        return []
    if any(not isinstance(value, int) or value < 0 for value in daily.values()):
        raise ValueError("daily counts must be nonnegative integers")
    ordered = sorted(daily)
    if (ordered[-1] - ordered[0]).days + 1 != len(ordered):
        raise ValueError("daily series must include every calendar date")

    outliers: list[DailyOutlier] = []
    for day in ordered:
        history = [
            daily[prior]
            for week in range(1, lookback_weeks + 1)
            if (prior := day - timedelta(days=7 * week)) in daily
        ]
        if len(history) < minimum_weeks:
            continue
        baseline = float(median(history))
        mad = median(abs(count - baseline) for count in history)
        if mad == 0:
            continue
        deviation = daily[day] - baseline
        if abs(deviation) < max(min_absolute_change, baseline * min_relative_change):
            continue
        score = deviation / (1.4826 * mad)
        if abs(score) >= threshold:
            outliers.append(
                DailyOutlier(
                    day=day,
                    observed=daily[day],
                    baseline=baseline,
                    score=score,
                    direction="spike" if score > 0 else "drop",
                )
            )
    return outliers
