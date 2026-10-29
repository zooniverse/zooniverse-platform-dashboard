#!/usr/bin/env python3
"""Audit the coverage and consistency of Panoptes project launch dates.

This opt-in, developer-only diagnostic explores the schema and *aggregate*
launch statistics in ``public.projects`` before adopting or revising the
project-launch collector. It inspects column types and catalog size, counts
approved/dated projects, flags approved dates earlier than creation, and lists
annual and recent daily launch *counts*. It never selects project identifiers,
titles, owners, user data, or individual launch timestamps, and persists no
results. This script is not part of routine dashboard refreshes.

The probe requires an authorized PostgreSQL read-only role configured through
``DB_URL_PANOPTES``. Its transaction is explicitly read-only, uses UTC dates,
and enforces statement and lock timeouts. Database exceptions are caught at the
command-line boundary, which prints only the exception class to avoid leaking
connection information or query parameters.

See Also
--------
:func:`zooniverse_dashboard.project_launches.fetch_project_launches`
    Production aggregate launch collection with additional privacy rules.
:func:`zooniverse_dashboard.config.panoptes_url`
    Resolve the configured PostgreSQL connection URL.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Row, RowMapping
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.elements import TextClause

from zooniverse_dashboard.config import panoptes_url

SCHEMA_SQL: TextClause = text(
    "SELECT column_name, data_type, udt_name "
    "FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = 'projects' "
    "AND column_name IN ('id', 'created_at', 'launch_date', 'launch_approved', 'live', 'state') "
    "ORDER BY column_name"
)

STATS_SQL: TextClause = text(
    "SELECT c.reltuples::bigint AS estimated_rows, "
    "pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = 'projects' AND c.relkind IN ('r', 'p')"
)

COVERAGE_SQL: TextClause = text(
    "SELECT COUNT(*)::bigint AS projects_total, "
    "COUNT(*) FILTER (WHERE launch_approved IS TRUE)::bigint AS approved_total, "
    "COUNT(*) FILTER (WHERE launch_date IS NOT NULL)::bigint AS dated_total, "
    "COUNT(*) FILTER (WHERE launch_approved IS TRUE AND launch_date IS NOT NULL)::bigint "
    "AS approved_with_date, "
    "COUNT(*) FILTER (WHERE launch_approved IS TRUE AND launch_date IS NULL)::bigint "
    "AS approved_without_date, "
    "COUNT(*) FILTER (WHERE launch_approved IS NOT TRUE AND launch_date IS NOT NULL)::bigint "
    "AS dated_without_approval, "
    "COUNT(*) FILTER (WHERE launch_approved IS TRUE AND launch_date::date > :through)::bigint "
    "AS approved_future_dates, "
    "COUNT(*) FILTER (WHERE launch_approved IS TRUE AND launch_date::date < created_at::date) "
    "::bigint AS approved_date_before_creation, "
    "MIN(launch_date::date) FILTER (WHERE launch_approved IS TRUE) AS first_approved_date, "
    "MAX(launch_date::date) FILTER (WHERE launch_approved IS TRUE) AS last_approved_date "
    "FROM public.projects"
)

YEARLY_SQL: TextClause = text(
    "SELECT EXTRACT(YEAR FROM launch_date)::int AS year, COUNT(*)::bigint AS launches "
    "FROM public.projects WHERE launch_approved IS TRUE AND launch_date IS NOT NULL "
    "AND launch_date::date <= :through "
    "GROUP BY EXTRACT(YEAR FROM launch_date) ORDER BY year"
)

RECENT_SQL: TextClause = text(
    "SELECT launch_date::date AS day, COUNT(*)::bigint AS launches "
    "FROM public.projects WHERE launch_approved IS TRUE AND launch_date IS NOT NULL "
    "AND launch_date::date >= :recent AND launch_date::date <= :through "
    "GROUP BY launch_date::date ORDER BY day DESC LIMIT 30"
)


def probe() -> None:
    """Print a read-only schema and aggregate launch-date quality report.

    The reporting cutoff is *yesterday* in UTC, matching the completed-day
    convention of :func:`zooniverse_dashboard.project_launches.fetch_project_launches`.
    Queries run in a single PostgreSQL transaction with a 20-second statement
    timeout, a 250-millisecond lock timeout, and a 35-second idle-in-transaction
    timeout. If required fields or their types are missing, the script prints a
    short diagnostic and does not execute the aggregate launch queries.

    Returns
    -------
    None
        Prints schema metadata, approximate table size, completeness counts,
        annual approved-launch counts, and up to 30 recent approved-launch dates.
        No files or project-level rows are written.

    Raises
    ------
    ValueError
        If the configured database is not PostgreSQL.
    sqlalchemy.exc.SQLAlchemyError
        If a database connection or statement fails. The command-line guard
        hides the exception details before printing an error message.
    OSError
        If the runtime configuration cannot be read.

    See Also
    --------
    :func:`zooniverse_dashboard.project_launches.fetch_project_launches`
        Validated, privacy-preserving production launch collector.
    """
    through: date = datetime.now(timezone.utc).date() - timedelta(days=1)
    engine: Engine = create_engine(
        panoptes_url(), poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        with engine.connect() as conn, conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout = 20000")
            conn.exec_driver_sql("SET LOCAL lock_timeout = 250")
            conn.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = 35000")
            conn.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            print("=== public.projects schema (approved fields only) ===")
            columns: dict[str, Row] = {row.column_name: row for row in conn.execute(SCHEMA_SQL)}
            for name, row in columns.items():
                print(f"{name}: {row.data_type} ({row.udt_name})")
            required: set[str] = {"created_at", "launch_date", "launch_approved"}
            if not required <= columns.keys():
                print("Missing required fields:", ", ".join(sorted(required - columns.keys())))
                return
            if columns["launch_approved"].udt_name != "bool":
                print("Unexpected launch_approved type; stopping")
                return
            if columns["launch_date"].udt_name not in {"date", "timestamp", "timestamptz"}:
                print("Unexpected launch_date type; stopping")
                return
            print("\n=== Approximate table size (catalog statistics) ===")
            for row in conn.execute(STATS_SQL):
                print(f"Estimated rows: {row.estimated_rows}; total size: {row.total_size}")
            print("\n=== Launch-date completeness (aggregate only) ===")
            summary: RowMapping = conn.execute(COVERAGE_SQL, {"through": through}).mappings().one()
            for name, value in summary.items():
                print(f"{name}: {value}")
            print("\n=== Approved launches per year through", through.isoformat(), "===")
            for year, count in conn.execute(YEARLY_SQL, {"through": through}):
                print(f"{year}: {count}")
            print("\n=== Latest 30 calendar dates with approved launches (last 90 days) ===")
            recent: date = through - timedelta(days=89)
            for day, count in conn.execute(RECENT_SQL, {"recent": recent, "through": through}):
                print(f"{day}: {count}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        probe()
    except Exception as exc:
        # Database drivers may attach private connection parameters to their
        # exception messages. Deliberately suppress both messages and tracebacks.
        print(f"Probe failed ({type(exc).__name__}); no database details printed")
        raise SystemExit(1) from None
