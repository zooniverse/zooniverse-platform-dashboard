"""Retrieve validated, platform-wide ERAS classification and Talk aggregates.

This module limits access to two public ERAS endpoints: ``/classifications``
and ``/comments``. It makes unauthenticated, bounded HTTP requests and never
retains individual classifications, volunteers, project identifiers or Talk
message bodies. Only daily counts and their reported totals are returned.

The same :class:`ClassificationProbe` describes either endpoint; ``CommentProbe``
is an alias of that class, not a different response schema. Short diagnostic
windows, configured history windows and all-time history have distinct
size and calendar limits. Full-history and configured-history responses must
have totals equal to the sum of returned daily buckets before use.

See Also
--------
:class:`ClassificationProbe`
    Immutable representation of a validated ERAS aggregate response.
:func:`fetch_full_classifications`
    Retrieve all available daily classification history.
:func:`fetch_window_comments`
    Retrieve daily Talk counts for an explicit reporting window.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Final, TypeAlias

import requests

ERAS_CLASSIFICATIONS_URL: Final[str] = "https://eras.zooniverse.org/classifications"
ERAS_COMMENTS_URL: Final[str] = "https://eras.zooniverse.org/comments"
MAX_PROBE_DAYS: Final[int] = 35
MAX_RESPONSE_BYTES: Final[int] = 128_000
MAX_FULL_HISTORY_DAYS: Final[int] = 35 * 366
MAX_FULL_RESPONSE_BYTES: Final[int] = 1_500_000


class ErasResponseError(ValueError):
    """Indicate an unsafe, malformed or unavailable ERAS aggregate response.

    Raised when a remote request fails, response limits are exceeded, or ERAS
    returns daily counts inconsistent with the required schema. Error messages
    are intentionally generic: they must not include raw upstream payloads or
    personal data.
    """


@dataclass(frozen=True)
class ClassificationProbe:
    """Hold a validated ERAS total and sparse observed UTC daily buckets.

    An endpoint may omit days without activity. :attr:`observed` contains only
    the days actually returned by ERAS; :meth:`calendar_series` explicitly fills
    missing days with zero *within* the requested inclusive reporting window.
    :meth:`observed_total` and :meth:`missing_days` let callers inspect that
    relationship before publishing a continuous snapshot.

    Attributes
    ----------
    start : datetime.date
        First requested UTC calendar date, inclusive.
    end : datetime.date
        Last requested UTC calendar date, inclusive.
    reported_total : int
        Nonnegative aggregate total supplied in the ERAS response. Only full-
        history and configured-history parsing require it to match daily counts.
    observed : tuple[tuple[datetime.date, int], ...]
        Sorted, unique, nonnegative daily counts actually returned by the API.

    See Also
    --------
    :class:`zooniverse_dashboard.snapshots.Snapshot`
        Validated continuous activity snapshot published from aggregate counts.
    """

    start: date
    end: date
    reported_total: int
    observed: tuple[tuple[date, int], ...]

    @property
    def observed_total(self) -> int:
        """Calculate the sum of explicitly reported daily buckets.

        Returns
        -------
        int
            Sum of counts in :attr:`observed`, without inserting missing days or
            consulting :attr:`reported_total`.
        """
        return sum(count for _, count in self.observed)

    @property
    def missing_days(self) -> int:
        """Count dates absent from the ERAS daily bucket list.

        Returns
        -------
        int
            Number of missing dates between :attr:`start` and :attr:`end`, inclusive.
            An omitted date may represent a zero-count day; no zeros are added to
            :attr:`observed` itself.
        """
        return (self.end - self.start).days + 1 - len(self.observed)

    def calendar_series(self) -> tuple[tuple[date, int], ...]:
        """Produce a continuous UTC series from validated sparse daily buckets.

        Insert a zero count for every omitted day within the requested window, but
        never outside the validated start and end dates. The probe remains unchanged.

        Returns
        -------
        tuple[tuple[datetime.date, int], ...]
            Chronological ``(day, count)`` pairs for each calendar date from
            :attr:`start` through :attr:`end`.
        """
        lookup: dict[date, int] = dict(self.observed)
        return tuple(
            (day, lookup.get(day, 0))
            for day in (
                self.start + timedelta(days=offset)
                for offset in range((self.end - self.start).days + 1)
            )
        )


def _utc_bucket(value: Any) -> date:
    """Parse and validate the UTC midnight of one ERAS daily bucket.

    Parameters
    ----------
    value : typing.Any
        Raw ``period`` field supplied in an ERAS JSON daily bucket. It must be
        an ISO-8601 string with a trailing ``Z`` and represent midnight UTC.

    Returns
    -------
    datetime.date
        Calendar day identified by the bucket.

    Raises
    ------
    ErasResponseError
        If the value is not a UTC timestamp string, cannot be parsed, or does
        not identify midnight exactly.
    """
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ErasResponseError("ERAS daily bucket must have a UTC timestamp")
    try:
        stamp: datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ErasResponseError("ERAS returned an invalid date bucket") from exc
    if stamp.tzinfo != timezone.utc or stamp.time() != datetime.min.time():
        raise ErasResponseError("ERAS returned a non-midnight UTC bucket")
    return stamp.date()


def parse_classifications(
    payload: Any, *, start: date, end: date, max_buckets: int = MAX_PROBE_DAYS
) -> ClassificationProbe:
    """Validate a platform-wide ERAS response without retaining extra fields.

    Require a JSON mapping with ``total_count`` and a bounded ``data`` list.
    Each item must have a unique UTC-midnight ``period`` and a nonnegative
    integer ``count`` within the requested inclusive dates. Additional fields
    are ignored. This parser does **not** require ``total_count`` to equal the
    sum of daily buckets; its callers decide whether that comparison is needed.

    Parameters
    ----------
    payload : typing.Any
        Decoded JSON response from ERAS; may contain unapproved extra fields.
    start : datetime.date
        First allowed UTC calendar day, inclusive.
    end : datetime.date
        Last allowed UTC calendar day, inclusive.
    max_buckets : int, optional
        Maximum number of daily rows permitted; defaults to :data:`MAX_PROBE_DAYS`.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated non-identifying count totals and observed daily buckets.

    Raises
    ------
    ErasResponseError
        If the response envelope, a daily bucket, date, count, or ordering
        uniqueness requirement is invalid, or ``max_buckets`` is exceeded.
    """
    if not isinstance(payload, dict):
        raise ErasResponseError("ERAS response must be a JSON object")
    total: object = payload.get("total_count")
    rows: object = payload.get("data")
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise ErasResponseError("ERAS response lacks valid aggregate counts")
    if len(rows) > max_buckets:
        raise ErasResponseError("ERAS response exceeds the allowed daily bucket count")

    parsed: dict[date, int] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise ErasResponseError("ERAS returned an invalid aggregate bucket")
        day: date = _utc_bucket(item.get("period"))
        count: object = item.get("count")
        if type(count) is not int or count < 0 or day < start or day > end or day in parsed:
            raise ErasResponseError("ERAS returned an invalid or duplicate daily count")
        parsed[day] = count
    return ClassificationProbe(start, end, total, tuple(sorted(parsed.items())))


def parse_full_history(payload: Any, *, through: date) -> ClassificationProbe:
    """Validate an all-time ERAS daily history and reconcile its total.

    The earliest returned day defines the beginning of history; no synthetic
    start date is guessed. An empty, oversized, future-only or inconsistent
    response is rejected. Gaps inside the returned date span may be filled with
    zeros later by :meth:`ClassificationProbe.calendar_series`.

    Parameters
    ----------
    payload : typing.Any
        Decoded full-history ERAS JSON containing a nonempty daily ``data`` list.
    through : datetime.date
        Last permissible completed UTC day, inclusive.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated observed buckets from the first returned day through ``through``.

    Raises
    ------
    ErasResponseError
        If the response is malformed, exceeds :data:`MAX_FULL_HISTORY_DAYS`,
        contains invalid date buckets or totals inconsistent with the daily rows.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ErasResponseError("ERAS full-history response must contain daily aggregates")
    rows: list[Any] = payload["data"]
    if not rows or len(rows) > MAX_FULL_HISTORY_DAYS:
        raise ErasResponseError("ERAS returned empty or oversized full history")
    try:
        first: date = min(_utc_bucket(row.get("period")) for row in rows)
    except (AttributeError, TypeError) as exc:
        raise ErasResponseError("ERAS returned malformed full-history buckets") from exc
    if first > through or (through - first).days + 1 > MAX_FULL_HISTORY_DAYS:
        raise ErasResponseError("ERAS returned an invalid full-history date range")
    parsed: ClassificationProbe = parse_classifications(
        payload, start=first, end=through, max_buckets=MAX_FULL_HISTORY_DAYS
    )
    if parsed.reported_total != parsed.observed_total:
        raise ErasResponseError("ERAS full-history total does not match daily buckets")
    return parsed


def _request_daily_counts(
    endpoint: str,
    params: dict[str, str],
    *,
    session: requests.Session | None = None,
    limit: int = MAX_RESPONSE_BYTES,
) -> Any:
    """Issue one unauthenticated, bounded HTTP request to an ERAS endpoint.

    Use streaming to impose a strict byte limit before decoding JSON. Redirects
    are refused; timeouts include a 5-second connection limit and a finite read
    timeout. A supplied session is reused without being closed. If this function
    creates the session, it closes it in all outcomes.

    Parameters
    ----------
    endpoint : str
        One of the fixed public aggregate endpoint URLs passed by this module.
    params : dict[str, str]
        Query parameters for the API, such as ``period``, ``start_date`` and
        ``end_date``. These are submitted by the caller to the public endpoint.
    session : requests.Session or None, optional
        Client session to reuse; when omitted, a temporary session is created.
    limit : int, optional
        Maximum accepted response size in bytes; defaults to
        :data:`MAX_RESPONSE_BYTES`.

    Returns
    -------
    typing.Any
        Decoded JSON value awaiting strict validation by a parser. Raw fields
        are not saved in any snapshot by this function.

    Raises
    ------
    ErasResponseError
        On HTTP failures, a response exceeding ``limit``, invalid JSON, or a
        request exception. Exceptions do not include the upstream response body.
    """
    own_session: bool = session is None
    client: requests.Session = session if session is not None else requests.Session()
    try:
        try:
            with client.get(
                endpoint,
                params=params,
                timeout=(5, 45 if limit > MAX_RESPONSE_BYTES else 20),
                stream=True,
                allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ErasResponseError("ERAS returned a non-success HTTP status")
                chunks: list[bytes] = []
                size: int = 0
                for chunk in response.iter_content(chunk_size=8192):
                    size += len(chunk)
                    if size > limit:
                        raise ErasResponseError("ERAS response exceeded the safety size limit")
                    chunks.append(chunk)
                try:
                    import json

                    return json.loads(b"".join(chunks))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ErasResponseError("ERAS returned invalid JSON") from exc
        except requests.RequestException:
            raise ErasResponseError("ERAS request failed; no response data retained") from None
    finally:
        if own_session:
            client.close()


def _fetch_aggregate_counts(
    start: date,
    end: date,
    *,
    endpoint: str,
    session: requests.Session | None = None,
) -> ClassificationProbe:
    """Fetch a short, inclusive UTC date window of aggregate ERAS counts.

    Parameters
    ----------
    start : datetime.date
        First day in the requested range.
    end : datetime.date
        Last day in the requested range, inclusive. The total window is limited
        to :data:`MAX_PROBE_DAYS` days.
    endpoint : str
        Fixed ERAS classifications or comments endpoint.
    session : requests.Session or None, optional
        Optional reusable HTTP session.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated daily count buckets and the reported ERAS total.

    Raises
    ------
    ValueError
        If the dates are invalid, reversed, or outside the permitted window.
    ErasResponseError
        If the HTTP request or aggregate response validation fails.
    """
    if (
        not isinstance(start, date)
        or not isinstance(end, date)
        or start > end
        or not 1 <= (end - start).days + 1 <= MAX_PROBE_DAYS
    ):
        raise ValueError(f"choose a valid window of 1–{MAX_PROBE_DAYS} UTC days")
    payload = _request_daily_counts(
        endpoint,
        {"period": "day", "start_date": start.isoformat(), "end_date": end.isoformat()},
        session=session,
    )
    return parse_classifications(payload, start=start, end=end)


def _fetch_full_history(
    through: date,
    *,
    endpoint: str,
    session: requests.Session | None = None,
) -> ClassificationProbe:
    """Retrieve and validate all ERAS history through a completed date.

    This request intentionally omits ``start_date`` so that the earliest
    returned bucket, not a locally guessed date, defines the coverage. It
    rejects partial results if the reported total differs from daily counts.

    Parameters
    ----------
    through : datetime.date
        Inclusive upper UTC date boundary for all-history retrieval.
    endpoint : str
        Fixed public ERAS classifications or comments endpoint.
    session : requests.Session or None, optional
        Reusable session; a temporary one is created if omitted.

    Returns
    -------
    :class:`ClassificationProbe`
        Reconciled historical daily aggregates beginning at ERAS's earliest
        returned bucket.

    Raises
    ------
    ValueError
        If ``through`` is not a plain calendar date.
    ErasResponseError
        If ERAS fails, returns oversized or malformed history, or daily counts
        fail to reconcile with the reported total.
    """
    if not isinstance(through, date) or isinstance(through, datetime):
        raise ValueError("Invalid full-history reporting cutoff")
    payload = _request_daily_counts(
        endpoint,
        {"period": "day", "end_date": through.isoformat()},
        session=session,
        limit=MAX_FULL_RESPONSE_BYTES,
    )
    return parse_full_history(payload, through=through)


def fetch_classifications(
    start: date, end: date, *, session: requests.Session | None = None
) -> ClassificationProbe:
    """Fetch a short window of platform-wide classification counts.

    Parameters
    ----------
    start : datetime.date
        First UTC date of the requested window, inclusive.
    end : datetime.date
        Last UTC date of the requested window, inclusive. Up to
        :data:`MAX_PROBE_DAYS` days may be requested.
    session : requests.Session or None, optional
        Optional reusable HTTP session.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated daily classification aggregates, without volunteer records.

    Raises
    ------
    ValueError
        If the window is invalid or too long.
    ErasResponseError
        If the public request or aggregate validation fails.
    """
    return _fetch_aggregate_counts(start, end, endpoint=ERAS_CLASSIFICATIONS_URL, session=session)


# ERAS has the same documented response shape for classifications and comments.
# This is a common aggregate representation; it is not a volunteer-level object.
CommentProbe: TypeAlias = ClassificationProbe


def parse_comments(payload: Any, *, start: date, end: date) -> CommentProbe:
    """Parse an ERAS Talk comment response using the shared count schema.

    Only the approved total and day/count fields are retained; the response
    format is the same as for classifications. Unlike full-history parsing,
    short-range parsing does not enforce total-versus-daily reconciliation.

    Parameters
    ----------
    payload : typing.Any
        Decoded ERAS ``/comments`` JSON response.
    start : datetime.date
        First permitted UTC date, inclusive.
    end : datetime.date
        Last permitted UTC date, inclusive.

    Returns
    -------
    :class:`ClassificationProbe`
        Aggregate Talk comment buckets in the shared representation
        (also known as ``CommentProbe``).

    Raises
    ------
    ErasResponseError
        If the response is malformed, too large in daily bucket count, or
        contains invalid daily data.
    """
    return parse_classifications(payload, start=start, end=end)


def fetch_comments(
    start: date, end: date, *, session: requests.Session | None = None
) -> CommentProbe:
    """Fetch a short window of platform-wide Talk comment counts.

    Parameters
    ----------
    start : datetime.date
        First UTC day in the requested interval.
    end : datetime.date
        Last UTC day in the requested interval, inclusive.
    session : requests.Session or None, optional
        Optional reusable HTTP session.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated aggregate comments response (``CommentProbe`` alias).

    Raises
    ------
    ValueError
        If the short requested window is invalid or exceeds the day limit.
    ErasResponseError
        If ERAS fails or the returned counts are malformed.
    """
    return _fetch_aggregate_counts(start, end, endpoint=ERAS_COMMENTS_URL, session=session)


def fetch_full_classifications(
    through: date, *, session: requests.Session | None = None
) -> ClassificationProbe:
    """Fetch all available daily classification counts from public ERAS.

    Parameters
    ----------
    through : datetime.date
        Inclusive UTC reporting cutoff.
    session : requests.Session or None, optional
        Optional reusable HTTP session.

    Returns
    -------
    :class:`ClassificationProbe`
        Reconciled historical classification aggregates.

    Raises
    ------
    ValueError
        If ``through`` is not a plain date.
    ErasResponseError
        If the request fails or the full-history response cannot be validated.
    """
    return _fetch_full_history(through, endpoint=ERAS_CLASSIFICATIONS_URL, session=session)


def fetch_full_comments(through: date, *, session: requests.Session | None = None) -> CommentProbe:
    """Fetch all available daily Talk comment counts from public ERAS.

    Parameters
    ----------
    through : datetime.date
        Last completed UTC day to include.
    session : requests.Session or None, optional
        Optional reusable HTTP session.

    Returns
    -------
    :class:`ClassificationProbe`
        Reconciled historical comment aggregates (``CommentProbe`` alias).

    Raises
    ------
    ValueError
        If ``through`` is invalid.
    ErasResponseError
        If the request fails or the returned history cannot be validated.
    """
    return _fetch_full_history(through, endpoint=ERAS_COMMENTS_URL, session=session)


def _fetch_history_window(
    start: date,
    through: date,
    *,
    endpoint: str,
    session: requests.Session | None = None,
) -> ClassificationProbe:
    """Fetch and reconcile one explicit history window from ERAS.

    Unlike the short probe, a configured collection may cover as many as
    :data:`MAX_FULL_HISTORY_DAYS` inclusive UTC dates. The response total
    must equal the sum of its returned daily buckets before publication.

    Parameters
    ----------
    start : datetime.date
        First day of the configured history window, inclusive.
    through : datetime.date
        Last day of the configured history window, inclusive.
    endpoint : str
        Fixed public ERAS classifications or comments endpoint.
    session : requests.Session or None, optional
        Reusable session, if provided.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated counts for the requested UTC range. Call
        :meth:`ClassificationProbe.calendar_series` for a continuous series.

    Raises
    ------
    ValueError
        If the date limits are invalid, reversed, or exceed the maximum span.
    ErasResponseError
        If ERAS rejects the request, supplies malformed buckets, or reports
        a total different from its daily counts.
    """
    if (
        not isinstance(start, date)
        or isinstance(start, datetime)
        or not isinstance(through, date)
        or isinstance(through, datetime)
        or start > through
        or (through - start).days + 1 > MAX_FULL_HISTORY_DAYS
    ):
        raise ValueError("Invalid ERAS history range")
    payload = _request_daily_counts(
        endpoint,
        {"period": "day", "start_date": start.isoformat(), "end_date": through.isoformat()},
        session=session,
        limit=MAX_FULL_RESPONSE_BYTES,
    )
    result: ClassificationProbe = parse_classifications(
        payload, start=start, end=through, max_buckets=MAX_FULL_HISTORY_DAYS
    )
    if result.reported_total != result.observed_total:
        raise ErasResponseError("ERAS date-range total does not match daily buckets")
    return result


def fetch_window_classifications(
    start: date, through: date, *, session: requests.Session | None = None
) -> ClassificationProbe:
    """Fetch all classification buckets for a configured history window.

    Parameters
    ----------
    start : datetime.date
        Inclusive starting UTC day.
    through : datetime.date
        Inclusive ending UTC day.
    session : requests.Session or None, optional
        Optional reusable HTTP session.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated and reconciled classification daily counts.

    Raises
    ------
    ValueError
        If the requested date range is invalid or oversized.
    ErasResponseError
        If ERAS fails or its daily counts do not reconcile.
    """
    return _fetch_history_window(start, through, endpoint=ERAS_CLASSIFICATIONS_URL, session=session)


def fetch_window_comments(
    start: date, through: date, *, session: requests.Session | None = None
) -> CommentProbe:
    """Fetch all Talk comment buckets for a configured history window.

    Parameters
    ----------
    start : datetime.date
        Inclusive first UTC date.
    through : datetime.date
        Inclusive last UTC date.
    session : requests.Session or None, optional
        Optional reusable HTTP session.

    Returns
    -------
    :class:`ClassificationProbe`
        Validated and reconciled Talk comment aggregates.

    Raises
    ------
    ValueError
        If the date range is invalid or exceeds permitted history.
    ErasResponseError
        If the request fails or returned daily totals do not reconcile.
    """
    return _fetch_history_window(start, through, endpoint=ERAS_COMMENTS_URL, session=session)
