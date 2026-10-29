"""Fetch privacy-preserving daily registration aggregates from Panoptes.

This module owns the fixed SQL statements used by the registration collector.
Only a UTC calendar day and the number of registrations on that day are
returned to Python; individual user IDs, emails and other user fields are
never selected. Queries run in short-lived, read-only PostgreSQL transactions
with explicit timeouts and bound date parameters.

``public.users.created_at`` is a PostgreSQL timestamp without time zone.
Treating its date portion as UTC reflects the existing pipeline assumption;
this module does not convert source timestamps or establish their timezone.

See Also
--------
:func:`fetch_panoptes`
    Fetch and validate the aggregate series.
:func:`zooniverse_dashboard.snapshots.publish_snapshot`
    Validate and persist the daily registration counts.
:func:`zooniverse_dashboard.config.panoptes_url`
    Obtain the Panoptes connection URL from runtime configuration.
"""

from datetime import date, datetime, time, timedelta
from typing import Final

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.elements import TextClause

from zooniverse_dashboard.config import panoptes_url
from zooniverse_dashboard.snapshots import MAX_DAYS

# No dynamic table names or identifiers from outside this module.
# Two audited, fixed SQL shapes; never interpolate SQL from configuration.
REGISTRATIONS_SQL: Final[TextClause] = text(
    "SELECT created_at::date AS day, COUNT(*)::bigint AS registrations "
    'FROM public."users" WHERE created_at IS NOT NULL '
    "AND created_at < :end_exclusive "
    "GROUP BY created_at::date ORDER BY day"
)
REGISTRATIONS_BOUNDED_SQL: Final[TextClause] = text(
    "SELECT created_at::date AS day, COUNT(*)::bigint AS registrations "
    'FROM public."users" WHERE created_at IS NOT NULL '
    "AND created_at >= :start_inclusive AND created_at < :end_exclusive "
    "GROUP BY created_at::date ORDER BY day"
)


def fetch_panoptes(*, through: date, history_days: int = 0) -> dict[date, int]:
    """Fetch registration counts through the last completed reporting day.

    Query the fixed ``public.users`` aggregation in one read-only PostgreSQL
    transaction, then build a date-to-count mapping. For bounded windows, days
    with no matching registrations are represented by zero. For full history,
    coverage begins on the first returned registration date; an empty history is
    rejected instead of inventing a starting day.

    The query uses an exclusive midnight boundary after ``through`` and, for a
    bounded window, an inclusive midnight start. A connection timeout, statement
    timeout and lock timeout limit remote work; the connection pool is disposed
    whether the query succeeds or fails. Only grouped results leave PostgreSQL.

    Parameters
    ----------
    through : datetime.date
        Inclusive UTC reporting cutoff, normally the most recent completed day.
        It is used as a date bound; source timestamps have no timezone metadata.
    history_days : int, optional
        Requested number of days ending at ``through``. Zero (the default)
        requests all available history; positive values may not exceed
        :data:`zooniverse_dashboard.snapshots.MAX_DAYS`.

    Returns
    -------
    dict[datetime.date, int]
        Ordered-by-insertion mapping of calendar dates to nonnegative daily
        registration counts, with zeros for dates absent from the SQL result.

    Raises
    ------
    ValueError
        If ``history_days`` is invalid, the connection is not PostgreSQL, the
        all-history query returns no rows, or aggregates contain malformed,
        duplicate, negative, or out-of-range values.
    sqlalchemy.exc.SQLAlchemyError
        If a database connection or query fails (including server-side timeouts).

    See Also
    --------
    :func:`zooniverse_dashboard.project_launches.fetch_project_launches`
        Similar grouped read-only Panoptes query for approved project launches.
    """
    if type(history_days) is not int or not 0 <= history_days <= MAX_DAYS:
        raise ValueError("Invalid history limit")
    start: date | None = through - timedelta(days=history_days - 1) if history_days else None
    url: str = panoptes_url()
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        bound: datetime = datetime.combine(through + timedelta(days=1), time.min)
        with engine.connect() as conn, conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout = 20000")
            conn.exec_driver_sql("SET LOCAL lock_timeout = 250")
            conn.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = 35000")
            if start is None:
                rows = conn.execute(REGISTRATIONS_SQL, {"end_exclusive": bound}).all()
            else:
                rows = conn.execute(
                    REGISTRATIONS_BOUNDED_SQL,
                    {"start_inclusive": datetime.combine(start, time.min), "end_exclusive": bound},
                ).all()
        if not rows and start is None:
            raise ValueError("No registration aggregates returned")
        values: dict[date, int] = {}
        for day, count in rows:
            if (
                not isinstance(day, date)
                or isinstance(day, datetime)
                or day > through
                or type(count) is not int
                or count < 0
                or day in values
            ):
                raise ValueError("Unexpected registration aggregate")
            values[day] = count
        first: date = start if start is not None else min(values)
        return {
            first + timedelta(days=i): values.get(first + timedelta(days=i), 0)
            for i in range((through - first).days + 1)
        }
    finally:
        engine.dispose()
