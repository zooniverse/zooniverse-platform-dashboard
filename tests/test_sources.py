"""Fixed SQL contract, parsing and ERAS window coverage."""

from datetime import date

import pytest

from zooniverse_dashboard.eras import parse_classifications
from zooniverse_dashboard.sources import REGISTRATIONS_SQL


def test_approved_panoptes_query_is_aggregate_only():
    sql = str(REGISTRATIONS_SQL).lower()
    assert "count(*)" in sql
    assert "group by created_at::date" in sql
    assert "where created_at is not null" in sql
    assert "select *" not in sql
    assert all(x not in sql for x in ("email", "password", "ip_address", "api_key", "login"))


def test_public_eras_daily_counts_are_validated():
    data = {"total_count": 4, "data": [{"period": "2026-10-20T00:00:00Z", "count": 4}]}
    probe = parse_classifications(data, start=date(2026, 10, 19), end=date(2026, 10, 20))
    assert probe.missing_days == 1
    assert probe.calendar_series() == ((date(2026, 10, 19), 0), (date(2026, 10, 20), 4))
    with pytest.raises(ValueError):
        parse_classifications(
            {"total_count": -1, "data": []}, start=date(2026, 10, 19), end=date(2026, 10, 20)
        )
