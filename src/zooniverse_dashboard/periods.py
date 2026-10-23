"""UTC calendar KPIs computed exclusively from pre-aggregated daily counts."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol


class DailyAggregateSeries(Protocol):
    """Minimal daily-count interface shared by registration and ERAS snapshots."""

    first: date
    last: date
    as_of: date
    daily: tuple[tuple[date, int], ...]


@dataclass(frozen=True)
class CalendarPeriod:
    """A named UTC calendar interval with an inclusive endpoint."""

    key: str
    label: str
    start: date
    end: date
    count: int | None


# Backward-compatible name for existing registration page imports.


def _previous_month_start(day: date) -> date:
    month_start = day.replace(day=1)
    return (month_start - timedelta(days=1)).replace(day=1)


def calendar_periods(snapshot: DailyAggregateSeries) -> tuple[CalendarPeriod, ...]:
    """Return seven KPIs, anchored at midnight UTC after the last complete day.

    Current week/month/year are month-to-date, week-to-date and year-to-date;
    preceding periods are full completed calendar periods. Weeks start Monday.
    Missing history is reported as unavailable, never silently assumed zero.
    """
    today = snapshot.as_of
    yesterday = today - timedelta(days=1)
    current_week = today - timedelta(days=today.weekday())
    previous_week = current_week - timedelta(days=7)
    current_month = today.replace(day=1)
    previous_month = _previous_month_start(today)
    current_year = date(today.year, 1, 1)
    previous_year = date(today.year - 1, 1, 1)
    periods = (
        ("yesterday", "Yesterday", yesterday, yesterday),
        ("week", "Current week", current_week, yesterday),
        ("previous-week", "Previous week", previous_week, current_week - timedelta(days=1)),
        ("month", "Current month", current_month, yesterday),
        ("previous-month", "Previous month", previous_month, current_month - timedelta(days=1)),
        ("year", "Current year", current_year, yesterday),
        ("previous-year", "Previous year", previous_year, current_year - timedelta(days=1)),
    )
    # A single prefix sum makes all the calendar totals constant-time.
    running = [0]
    for _, count in snapshot.daily:
        running.append(running[-1] + count)
    results = []
    for key, label, start, end in periods:
        if start > end:
            # Exactly at the first instant of a week/month/year, the current
            # period has no completed days yet: display 0 rather than future data.
            value = 0
        elif start < snapshot.first or end > snapshot.last:
            value = None
        else:
            left = (start - snapshot.first).days
            right = (end - snapshot.first).days + 1
            value = running[right] - running[left]
        results.append(CalendarPeriod(key, label, start, end, value))
    return tuple(results)


def visible_date_range(*, first: date, last: date, window: str) -> tuple[date, date]:
    """Choose an inclusive initial viewport, clamped to snapshot availability."""
    if first > last:
        raise ValueError("empty dataset date span")
    if window == "all":
        return first, last
    if window not in {"7", "28", "90", "180", "365"}:
        raise ValueError("unsupported history window")
    return max(first, last - timedelta(days=int(window) - 1)), last
