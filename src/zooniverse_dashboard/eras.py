"""Public ERAS count aggregates (classifications and Talk comments) only.

Only the platform-wide, unauthenticated endpoints are allowed. No user IDs,
project IDs, discussion contents, or authenticated requests are retrieved.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

ERAS_CLASSIFICATIONS_URL = "https://eras.zooniverse.org/classifications"
ERAS_COMMENTS_URL = "https://eras.zooniverse.org/comments"
MAX_PROBE_DAYS = 35
MAX_RESPONSE_BYTES = 128_000


class ErasResponseError(ValueError):
    """ERAS returned an unexpected aggregate shape or failed the request."""


@dataclass(frozen=True)
class ClassificationProbe:
    """Validated non-identifying ERAS daily counts for a UTC date window."""

    start: date
    end: date
    reported_total: int
    observed: tuple[tuple[date, int], ...]

    @property
    def observed_total(self) -> int:
        """Sum of returned nonempty daily buckets."""
        return sum(count for _, count in self.observed)

    @property
    def missing_days(self) -> int:
        """Days with no returned bucket; ERAS may omit zero-count days."""
        return (self.end - self.start).days + 1 - len(self.observed)

    def calendar_series(self) -> tuple[tuple[date, int], ...]:
        """Fill missing calendar dates with zero for charting after validation."""
        lookup = dict(self.observed)
        return tuple(
            (day, lookup.get(day, 0))
            for day in (
                self.start + timedelta(days=offset)
                for offset in range((self.end - self.start).days + 1)
            )
        )


def _utc_bucket(value: Any) -> date:
    """Validate and extract the UTC calendar date from an ERAS day bucket."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ErasResponseError("ERAS daily bucket must have a UTC timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ErasResponseError("ERAS returned an invalid date bucket") from exc
    if stamp.tzinfo != timezone.utc or stamp.time() != datetime.min.time():
        raise ErasResponseError("ERAS returned a non-midnight UTC bucket")
    return stamp.date()


def parse_classifications(payload: Any, *, start: date, end: date) -> ClassificationProbe:
    """Keep only aggregate fields; reject user-level or malformed responses.

    The response may contain additional fields, but they are neither retained
    nor logged. The total-versus-bucket consistency check is exposed to the
    caller rather than assumed until live range semantics are verified.
    """
    if not isinstance(payload, dict):
        raise ErasResponseError("ERAS response must be a JSON object")
    total = payload.get("total_count")
    rows = payload.get("data")
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise ErasResponseError("ERAS response lacks valid aggregate counts")
    if len(rows) > MAX_PROBE_DAYS:
        raise ErasResponseError("ERAS response exceeds the allowed daily bucket count")

    parsed: dict[date, int] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise ErasResponseError("ERAS returned an invalid aggregate bucket")
        day = _utc_bucket(item.get("period"))
        count = item.get("count")
        if type(count) is not int or count < 0 or day < start or day > end or day in parsed:
            raise ErasResponseError("ERAS returned an invalid or duplicate daily count")
        parsed[day] = count
    return ClassificationProbe(start, end, total, tuple(sorted(parsed.items())))


def _fetch_aggregate_counts(
    start: date,
    end: date,
    *,
    endpoint: str,
    session: requests.Session | None = None,
) -> ClassificationProbe:
    """Fetch public daily counts from an explicitly selected ERAS endpoint."""
    if (
        not isinstance(start, date)
        or not isinstance(end, date)
        or start > end
        or not 1 <= (end - start).days + 1 <= MAX_PROBE_DAYS
    ):
        raise ValueError(f"choose a valid window of 1–{MAX_PROBE_DAYS} UTC days")

    own_session = session is None
    client = session if session is not None else requests.Session()
    try:
        try:
            with client.get(
                endpoint,
                params={
                    "period": "day",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
                timeout=(5, 20),
                stream=True,
                allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ErasResponseError("ERAS returned a non-success HTTP status")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ErasResponseError("ERAS response exceeded the safety size limit")
                    chunks.append(chunk)
                try:
                    import json

                    payload = json.loads(b"".join(chunks))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ErasResponseError("ERAS returned invalid JSON") from exc
        except requests.RequestException:
            raise ErasResponseError("ERAS request failed; no response data retained") from None
    finally:
        if own_session:
            client.close()
    return parse_classifications(payload, start=start, end=end)


def fetch_classifications(
    start: date, end: date, *, session: requests.Session | None = None
) -> ClassificationProbe:
    """Fetch public platform classification counts, never individual contributions."""
    return _fetch_aggregate_counts(start, end, endpoint=ERAS_CLASSIFICATIONS_URL, session=session)


# ERAS has the same documented response shape for classifications and comments.
# This is a common aggregate representation; it is not a volunteer-level object.
CommentProbe = ClassificationProbe


def parse_comments(payload: Any, *, start: date, end: date) -> CommentProbe:
    """Parse platform-wide Talk comment counts; discard all unapproved response fields."""
    return parse_classifications(payload, start=start, end=end)


def fetch_comments(
    start: date, end: date, *, session: requests.Session | None = None
) -> CommentProbe:
    """Fetch only public, platform-wide /comments daily counts, with no auth."""
    return _fetch_aggregate_counts(start, end, endpoint=ERAS_COMMENTS_URL, session=session)
