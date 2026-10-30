"""Characterize shared calendar coverage without conflating dense and sparse sources."""

from datetime import date

from zooniverse_dashboard.periods import covered_period_total


def test_covered_period_total_rejects_incomplete_history_without_running_sum():
    calls: list[tuple[date, date]] = []

    def sum_between(start: date, end: date) -> int:
        calls.append((start, end))
        return 42

    bounds = {"first": date(2026, 1, 3), "last": date(2026, 1, 27)}
    assert (
        covered_period_total(
            **bounds, start=date(2026, 1, 1), end=date(2026, 1, 27), sum_between=sum_between
        )
        is None
    )
    assert (
        covered_period_total(
            **bounds, start=date(2026, 1, 3), end=date(2026, 1, 28), sum_between=sum_between
        )
        is None
    )
    assert calls == []
    assert (
        covered_period_total(
            **bounds, start=date(2026, 1, 3), end=date(2026, 1, 27), sum_between=sum_between
        )
        == 42
    )
    assert calls == [(date(2026, 1, 3), date(2026, 1, 27))]


def test_empty_current_period_is_zero_without_querying_any_source():
    def unexpected_sum(_start: date, _end: date) -> int:
        raise AssertionError("no completed days must not query data")

    assert (
        covered_period_total(
            first=date(2025, 1, 1),
            last=date(2025, 12, 31),
            start=date(2026, 1, 1),
            end=date(2025, 12, 31),
            sum_between=unexpected_sum,
        )
        == 0
    )
