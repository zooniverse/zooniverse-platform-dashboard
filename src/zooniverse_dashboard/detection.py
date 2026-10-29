"""Exploratory weekday-matched median/MAD outlier detection.

This legacy, standalone detector compares a day's aggregate count against
counts from preceding occurrences of the same weekday. It neither queries
upstream systems nor reads individual user records. It is **not** the detector
used by the dashboard Anomalies page: that production path is implemented in
:mod:`zooniverse_dashboard.anomalies.compute` and its snapshot-based catalog in
:mod:`zooniverse_dashboard.anomalies.catalog`.

The robust score is a heuristic for investigation, not a probability or a
security alert. A zero median absolute deviation (MAD) deliberately yields no
score for that day, rather than an artificial infinite outlier score.

See Also
--------
:func:`zooniverse_dashboard.detection.find_weekday_outliers`
    Evaluate a validated, continuous daily aggregate series.
:func:`zooniverse_dashboard.anomalies.compute.score_days`
    Production, past-only negative-binomial daily scoring.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from statistics import median
from typing import Mapping


@dataclass(frozen=True)
class DailyOutlier:
    """Represent one unusually high or low aggregate daily count.

    This immutable result records a weekday-matched median baseline and the
    signed robust deviation score returned by :func:`zooniverse_dashboard.detection.find_weekday_outliers`. It is
    an exploratory datum rather than an incident record.

    Attributes
    ----------
    day : datetime.date
        UTC calendar day with a flagged aggregate count.
    observed : int
        Observed nonnegative activity count for that day.
    baseline : float
        Median count from earlier matching weekdays.
    score : float
        Signed robust score: ``(observed - baseline) / (1.4826 * MAD)``. Positive
        scores are increases and negative scores are decreases.
    direction : str
        ``"spike"`` for positive deviations or ``"drop"`` for negative ones.

    See Also
    --------
    :func:`zooniverse_dashboard.detection.find_weekday_outliers`
        Create these records using historical same-weekday observations.
    """

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
    """Find exploratory outliers relative to earlier matching weekdays.

    For every UTC date, collect up to ``lookback_weeks`` earlier counts on the
    same weekday. When ``minimum_weeks`` observations are available, calculate the
    median baseline and median absolute deviation (MAD). A qualifying count must
    both exceed the configured absolute/relative change floor and have an
    absolute signed robust score of at least ``threshold``.

    Only past dates contribute to each baseline. Days whose historical MAD is zero
    are not scored. The input must be a *continuous* calendar series, including
    explicit zeros for genuine zero-count days; omitted dates are not imputed by
    this function. The resulting flags are descriptive and must not be interpreted
    as p-values, known incidents, or evidence of suspicious individuals.

    Parameters
    ----------
    daily : collections.abc.Mapping[datetime.date, int]
        Complete UTC date-to-count mapping. Counts must be nonnegative integers,
        with exactly one key per calendar date and no missing dates in its span.
    lookback_weeks : int, optional
        Maximum number of preceding occurrences of the same weekday (default 8;
        accepted together with ``minimum_weeks`` in the range 4 through 12).
    minimum_weeks : int, optional
        Minimum matching-weekday observations to establish a baseline (default 4).
    threshold : float, optional
        Minimum absolute robust score required for an outlier (default 5.0).
    min_relative_change : float, optional
        Nonnegative minimum change as a fraction of the median baseline (default
        0.25), combined with ``min_absolute_change`` using the greater threshold.
    min_absolute_change : int, optional
        Minimum absolute difference from the median baseline (default 10).

    Returns
    -------
    list[DailyOutlier]
        Outliers in ascending calendar order; an empty list for no data or no
        qualifying deviations.

    Raises
    ------
    ValueError
        If lookback bounds, thresholds, counts, or calendar continuity are invalid.

    See Also
    --------
    :class:`DailyOutlier`
        Typed result for an individual flagged day.
    :func:`zooniverse_dashboard.anomalies.compute.score_days`
        The separate, production anomaly scoring approach.
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
    ordered: list[date] = sorted(daily)
    if (ordered[-1] - ordered[0]).days + 1 != len(ordered):
        raise ValueError("daily series must include every calendar date")

    outliers: list[DailyOutlier] = []
    for day in ordered:
        history: list[int] = [
            daily[prior]
            for week in range(1, lookback_weeks + 1)
            if (prior := day - timedelta(days=7 * week)) in daily
        ]
        if len(history) < minimum_weeks:
            continue
        baseline: float = float(median(history))
        mad: float = median(abs(count - baseline) for count in history)
        if mad == 0:
            continue
        deviation: float = daily[day] - baseline
        if abs(deviation) < max(min_absolute_change, baseline * min_relative_change):
            continue
        score: float = deviation / (1.4826 * mad)
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
