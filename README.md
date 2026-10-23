# Zooniverse Platform Dashboard

A deliberately small, internal Dash application for three **platform-wide aggregate** metrics:

- **User registrations** — read-only grouped daily counts from `Panoptes public.users.created_at`.
- **Classifications** — public ERAS `/classifications` daily counts.
- **Talk activity** — public ERAS `/comments` counts (not unique Talk contributors).

Each page displays seven UTC calendar KPI periods (yesterday, current and prior week,
month and year), full-history navigable graphs, a seven-day moving average and
**experimental** unusual-activity markers. The root URL redirects to `/users`.
No overview, data-sources, anomalies, simulation or database-diagnostics pages remain.

## Install locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,docs,server]'
cp .env.example .env
chmod 600 .env
# Fill DB_URL_PANOPTES in .env using a read-only Panoptes database account.
python app.py
```

Open <http://127.0.0.1:8050/>. The first visit displays an empty-state message until a
refresh succeeds; the application never loads database records while rendering pages.

The **Refresh data** button makes one approved Panoptes full-history aggregation and
retrieves full ERAS history on the first refresh (and recent days thereafter).
A global, one-hour cooldown and POSIX file lock
prevent concurrent or repeated refreshes. Each successful snapshot includes
`last_modified_utc`; the dashboard shows its actual timestamp and warns when the
coverage is behind the last completed UTC day. A partial failure preserves good files.

On the **first ERAS refresh**, the collector requests **all available daily
classification and Talk comment history**, rather than only 28 days. It uses
one public all-time request per source (no `start_date`), validates that the
reported total equals the sum of its daily buckets, and fills omitted dates
within the observed history with zero. Subsequent refreshes reconcile the
latest 14 days only. Existing 28-day snapshots are recognized as partial and
upgraded on the next successful refresh; the last valid files are preserved
if the initial full-history request fails.

If an ERAS full-history response is too large or slow, the collector will
**fail safely**, rather than silently publishing an incomplete history. In that
case, consult the [operations guide](docs/operations.md) before attempting a
bounded manual backfill.

## Docker: one service and one persistent volume

```bash
cp .env.example .env  # edit safely; do not commit
# In Docker, only the DB_URL_PANOPTES setting needs to be provided via environment.
docker compose -f deploy/compose.yml up --build -d
# For an explicit initial ERAS historical backfill:
docker compose -f deploy/compose.yml exec dashboard \
  zooniverse-dashboard backfill --source classifications
```

The container keeps `users.json`, `classifications.json`, `talks.json`, and
`refresh_status.json` in `/data`, backed by the named Docker volume `analytics_data`.
It must have **a single deployment replica on one shared POSIX filesystem**.
There is no cron job, separate database, or object-storage bucket.

The refresh button is available to all dashboard visitors. **Restrict access to the
entire deployed dashboard** with Zooniverse's ingress/SSO/VPN or an authenticated
reverse proxy, and enforce HTTPS there. Do not publish the container directly on a
public interface. Panoptes credentials remain server-side environment variables.

See the [Sphinx documentation](docs/index.md) for implementation, operational and
security details. The original exploratory project was not imported into this repo.

## Publish to the Zooniverse GitHub organization

This checkout begins with a **new Git history**. No GitHub remote is preconfigured.
From a machine with GitHub CLI authenticated and permission to create repos in
`zooniverse`, run:

```bash
gh auth status
gh repo create zooniverse/zooniverse-platform-dashboard \
  --private --source=. --remote=origin --push
```

If your organization limits repository creation, ask an organization owner to
create a **private, empty** repository at this name, then run:

```bash
git remote add origin git@github.com:zooniverse/zooniverse-platform-dashboard.git
git push -u origin main
```

Do not enable CI workflows or inject production database credentials until the
Zooniverse team has agreed on its deployment approach. The dashboard is designed
for one access-controlled instance, not a public site.

## Development

Run formatting, linting, tests and the documentation build directly:

```bash
black app.py src tests
ruff check --fix app.py src tests
black --check app.py src tests
ruff check app.py src tests
pytest -q
sphinx-build -b html docs docs/_build/html
```

The documentation uses the **Furo** theme and **Sphinx AutoAPI**, which
creates a navigable reference from the source without importing runtime code.

## Production caveats

- Panoptes `created_at` is `timestamp without time zone`; its UTC interpretation
  must be confirmed with the database maintainers.
- Full-history Panoptes aggregation was observed at 1,330.3 ms on October 23,
  2026 (one approved production measurement). This is not a guaranteed SLA.
- ERAS public aggregate counts may be delayed; the recent 14 days are reconciled.
- The refresh runs synchronously within a Gunicorn worker (120-second timeout).
  Very long ERAS operations should use the CLI rather than the web button.
- All anomaly flags are uncalibrated statistical observations, never accusations
  about user behavior or production security alerts.
