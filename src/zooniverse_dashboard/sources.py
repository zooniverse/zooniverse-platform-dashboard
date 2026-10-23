"""Fixed, aggregate-only remote sources. No individual users or classifications."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from zooniverse_dashboard.config import panoptes_url

# No dynamic table names or identifiers from outside this module.
REGISTRATIONS_SQL = text(
    'SELECT created_at::date AS day, COUNT(*)::bigint AS registrations '
    'FROM public."users" WHERE created_at IS NOT NULL '
    'AND created_at < :end_exclusive '
    'GROUP BY created_at::date ORDER BY day'
)


def fetch_panoptes(*, through: date) -> dict[date, int]:
    """One read-only PostgreSQL scan returning (day, count) only.

    `created_at` is timestamp WITHOUT timezone; the UTC interpretation still
    needs to be confirmed with the Panoptes maintainers.
    """
    url = panoptes_url()
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        bound = datetime.combine(through + timedelta(days=1), time.min)
        with engine.connect() as conn, conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql("SET LOCAL statement_timeout = 20000")
            conn.exec_driver_sql("SET LOCAL lock_timeout = 250")
            conn.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = 35000")
            rows = conn.execute(REGISTRATIONS_SQL, {"end_exclusive": bound}).all()
        if not rows:
            raise ValueError("No registration aggregates returned")
        values: dict[date, int] = {}
        for day, count in rows:
            if (not isinstance(day, date) or isinstance(day, datetime) or day > through
                    or type(count) is not int or count < 0 or day in values):
                raise ValueError("Unexpected registration aggregate")
            values[day] = count
        first = min(values)
        return {first + timedelta(days=i): values.get(first + timedelta(days=i), 0)
                for i in range((through - first).days + 1)}
    finally:
        engine.dispose()
