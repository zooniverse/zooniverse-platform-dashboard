#!/usr/bin/env python3
"""Read-only, aggregate-only investigation of Panoptes project launch dates.

No project identifiers, titles, owner details, or individual launch timestamps are
retrieved. Nothing is persisted. Intended only for an authorized database role.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from zooniverse_dashboard.config import panoptes_url

SCHEMA_SQL = text(
    "SELECT column_name, data_type, udt_name "
    "FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = 'projects' "
    "AND column_name IN ('id', 'created_at', 'launch_date', 'launch_approved', 'live', 'state') "
    "ORDER BY column_name"
)

STATS_SQL = text(
    "SELECT c.reltuples::bigint AS estimated_rows, "
    "pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = 'projects' AND c.relkind IN ('r', 'p')"
)

COVERAGE_SQL = text(
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

YEARLY_SQL = text(
    "SELECT EXTRACT(YEAR FROM launch_date)::int AS year, COUNT(*)::bigint AS launches "
    "FROM public.projects WHERE launch_approved IS TRUE AND launch_date IS NOT NULL "
    "AND launch_date::date <= :through "
    "GROUP BY EXTRACT(YEAR FROM launch_date) ORDER BY year"
)

RECENT_SQL = text(
    "SELECT launch_date::date AS day, COUNT(*)::bigint AS launches "
    "FROM public.projects WHERE launch_approved IS TRUE AND launch_date IS NOT NULL "
    "AND launch_date::date >= :recent AND launch_date::date <= :through "
    "GROUP BY launch_date::date ORDER BY day DESC LIMIT 30"
)


def probe() -> None:
    """Print metadata and aggregate counts; never print raw records or database URLs."""
    through = datetime.now(timezone.utc).date() - timedelta(days=1)
    engine = create_engine(panoptes_url(), poolclass=NullPool, connect_args={"connect_timeout": 5})
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
            columns = {row.column_name: row for row in conn.execute(SCHEMA_SQL)}
            for name, row in columns.items():
                print(f"{name}: {row.data_type} ({row.udt_name})")
            required = {"created_at", "launch_date", "launch_approved"}
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
            summary = conn.execute(COVERAGE_SQL, {"through": through}).mappings().one()
            for name, value in summary.items():
                print(f"{name}: {value}")
            print("\n=== Approved launches per year through", through.isoformat(), "===")
            for year, count in conn.execute(YEARLY_SQL, {"through": through}):
                print(f"{year}: {count}")
            print("\n=== Latest 30 calendar dates with approved launches (last 90 days) ===")
            recent = through - timedelta(days=89)
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
