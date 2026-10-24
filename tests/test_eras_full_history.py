"""Full ERAS history: public aggregates, no start date, bounded responses."""

import json
from datetime import date

import pytest

from zooniverse_dashboard.eras import (
    ErasResponseError,
    fetch_full_classifications,
    fetch_full_comments,
    fetch_window_classifications,
    fetch_window_comments,
    parse_full_history,
)


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i : i + chunk_size]


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *, params, **kwargs):
        self.calls.append((url, params, kwargs))
        return FakeResponse(self.payload)


def payload():
    return {
        "total_count": 12,
        "data": [
            {"period": "2015-06-29T00:00:00Z", "count": 5},
            {"period": "2026-10-20T00:00:00Z", "count": 7},
        ],
        "unapproved_personal_info": "must not be retained",
    }


@pytest.mark.parametrize(
    "fetcher,endpoint",
    [
        (fetch_full_classifications, "/classifications"),
        (fetch_full_comments, "/comments"),
    ],
)
def test_all_time_eras_endpoint_has_no_start_date(fetcher, endpoint):
    session = FakeSession(payload())
    result = fetcher(date(2026, 10, 22), session=session)
    assert result.reported_total == result.observed_total == 12
    assert result.start == date(2015, 6, 29)
    assert result.end == date(2026, 10, 22)
    assert len(result.calendar_series()) == (result.end - result.start).days + 1
    assert session.calls[0][0].endswith(endpoint)
    assert session.calls[0][1] == {"period": "day", "end_date": "2026-10-22"}
    assert "unapproved_personal_info" not in str(result)


def test_full_history_rejects_invalid_or_incomplete_totals():
    bad = payload()
    bad["total_count"] = 13
    with pytest.raises(ErasResponseError, match="does not match"):
        parse_full_history(bad, through=date(2026, 10, 22))
    bad = payload()
    bad["data"].append(bad["data"][0])
    with pytest.raises(ErasResponseError, match="duplicate"):
        parse_full_history(bad, through=date(2026, 10, 22))
    bad = payload()
    bad["data"].append({"period": "2026-10-23T00:00:00Z", "count": 1})
    with pytest.raises(ErasResponseError):
        parse_full_history(bad, through=date(2026, 10, 22))


def test_full_history_rejects_empty_history_and_oversized_response():
    with pytest.raises(ErasResponseError, match="empty"):
        parse_full_history({"total_count": 0, "data": []}, through=date(2026, 10, 22))
    huge = FakeSession({"total_count": 0, "data": [], "junk": "x" * 1_500_001})
    with pytest.raises(ErasResponseError, match="size limit"):
        fetch_full_classifications(date(2026, 10, 22), session=huge)


@pytest.mark.parametrize(
    "fetcher,endpoint",
    [(fetch_window_classifications, "/classifications"), (fetch_window_comments, "/comments")],
)
def test_bounded_history_eras_single_request(fetcher, endpoint):
    body = {
        "total_count": 6,
        "data": [
            {"period": "2026-10-20T00:00:00Z", "count": 2},
            {"period": "2026-10-22T00:00:00Z", "count": 4},
        ],
        "user_info": "do not retain",
    }
    session = FakeSession(body)
    result = fetcher(date(2026, 10, 20), date(2026, 10, 22), session=session)
    assert session.calls[0][0].endswith(endpoint)
    assert session.calls[0][1] == {
        "period": "day",
        "start_date": "2026-10-20",
        "end_date": "2026-10-22",
    }
    assert result.calendar_series() == (
        (date(2026, 10, 20), 2),
        (date(2026, 10, 21), 0),
        (date(2026, 10, 22), 4),
    )
    assert "user_info" not in str(result)


def test_bounded_history_rejects_wrong_total_and_out_of_range():
    session = FakeSession(
        {
            "total_count": 90,
            "data": [{"period": "2026-10-22T00:00:00Z", "count": 2}],
        }
    )
    with pytest.raises(ErasResponseError, match="does not match"):
        fetch_window_comments(date(2026, 10, 20), date(2026, 10, 22), session=session)
    with pytest.raises(ValueError):
        fetch_window_comments(date(2026, 10, 22), date(2026, 10, 20), session=session)
