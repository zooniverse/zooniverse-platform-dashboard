"""Calendar-binned chart series derived only from validated daily snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

INTERVALS = ("daily", "weekly", "monthly", "yearly")


@dataclass(frozen=True)
class ChartBucket:
    """A UTC calendar bucket, possibly partial at the snapshot boundaries."""

    start: date
    first_observed: date
    last_observed: date
    count: int

    @property
    def center(self) -> date:
        """A plot position inside the covered portion of this bucket."""
        return self.first_observed + timedelta(
            days=(self.last_observed - self.first_observed).days // 2
        )

    @property
    def width_ms(self) -> int:
        """Bar width representing the covered portion, with a small visual gap."""
        days = (self.last_observed - self.first_observed).days + 1
        return int(days * 86_400_000 * 0.88)


def bucket_start(day: date, interval: str) -> date:
    """Return a UTC calendar bucket's first day (ISO weeks start Monday)."""
    if interval == "daily":
        return day
    if interval == "weekly":
        return day - timedelta(days=day.weekday())
    if interval == "monthly":
        return day.replace(day=1)
    if interval == "yearly":
        return date(day.year, 1, 1)
    raise ValueError("Unsupported aggregation interval")


def group_daily(
    daily: tuple[tuple[date, int], ...], interval: str = "daily"
) -> tuple[ChartBucket, ...]:
    """Sum daily counts into calendar buckets without scaling partial periods."""
    if interval not in INTERVALS:
        raise ValueError("Unsupported aggregation interval")
    buckets: list[ChartBucket] = []
    for day, count in daily:
        beginning = bucket_start(day, interval)
        if buckets and buckets[-1].start == beginning:
            old = buckets[-1]
            buckets[-1] = ChartBucket(beginning, old.first_observed, day, old.count + count)
        else:
            buckets.append(ChartBucket(beginning, day, day, count))
    return tuple(buckets)
