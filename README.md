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

The **Refresh data** button re-queries the **entire selected history** for
all three sources on each use; no incremental backfill or 14-day reconciliation
is retained. By default this means all available Panoptes registrations and
all available public ERAS classification/Talk counts. Each published JSON
records `last_modified_utc`, its `history_days` limit and actual date coverage.
A shared one-hour cooldown and POSIX lock protect the source services and
prevent simultaneous refreshes. Failed collections preserve the previous file.

Configure the initial history window in `.env` using
`ANALYTICS_HISTORY_DAYS=0` (all available, also the default when unset) or a
positive number of completed UTC days. For example, `365` means the latest
365 completed dates through yesterday, not a rolling timestamp interval.

You can adjust the effective window later without restarting Docker:

```bash
set-date-limit              # show effective limit
set-date-limit 365          # persist 365 completed UTC days
set-date-limit 0            # return to all available history
refresh-data                # refresh all three sources (no flags)
```

The command writes an override to the persistent data volume; it cannot
rewrite Docker environment variables. The effective precedence is saved
volume override, then environment, then all-time default. Changing the limit
does not refresh data automatically, and the dashboard warns until new
snapshots match. See [operations](docs/operations.md).

### Refresh diagnostics

`refresh-data` prints UTC-timestamped progress for all three sources, including
fetch duration, snapshot publication and the overall outcome. The dashboard's
**Refresh data** button uses the same logger, visible in the web server's
standard error / container logs. Secrets and raw exceptions are deliberately
omitted. See [refresh logs and diagnostics](docs/operations.md#refresh-logs-and-diagnostics).

## Docker: one service and one persistent volume

```bash
cp .env.example .env  # edit safely; do not commit
# In Docker, only the DB_URL_PANOPTES setting needs to be provided via environment.
docker compose -f deploy/compose.yml up --build -d
# Optional: refresh directly (same behavior as the dashboard button):
docker compose -f deploy/compose.yml exec dashboard refresh-data
```

The container keeps `users.json`, `classifications.json`, `talks.json`,
`refresh_status.json`, and optional `collection_config.json` in `/data`, backed by the named Docker volume `analytics_data`.
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
- ERAS public aggregate counts may be delayed; each refresh retrieves the selected history again.
- The refresh runs synchronously within a Gunicorn worker (120-second timeout).
  Very long ERAS operations should use the CLI rather than the web button.
- All anomaly flags are uncalibrated statistical observations, never accusations
  about user behavior or production security alerts.

### Offline anomaly episodes

The old weekday-MAD chart markers are replaced by selected NB2 episode markers.
The new **Anomalies** navigation page shows episodes, evidence from the
calendar-aware/baseline NB2 model and/or rolling empirical rarity, and links to
the source chart. Clicking a chart diamond highlights the episode on that page.
The anomaly calculations run **outside Dash** and consume only aggregate JSON.

Install the optional analysis dependencies in the environment that can access
`ANALYTICS_DATA_DIR` (the same shared volume used by the dashboard):

```bash
python -m pip install -e '.[analysis]'
# Run after refreshing the data snapshots, or at any time for offline reanalysis:
rebuild-anomalies
# Equivalently:
python -m zooniverse_dashboard.anomalies
```

The catalog is stored at `${ANALYTICS_DATA_DIR}/anomalies.json`; it is rewritten
atomically and rechecked against each source's snapshot modification timestamp.
If you refresh a source but do not rebuild the anomaly catalog, that source's
stale markers disappear until reanalysis; the metric graph still works normally.
In Docker, install the analysis extras in the image and run:
`docker compose -f deploy/compose.yml exec dashboard rebuild-anomalies`.
This may take time; do not run it from a per-request Dash callback. For early
versions, analysis covers **at most 730 recent days**, not the entire platform
history. Insufficient history yields no events and an explicit notice. This is
quick-inspection statistical context, not an incident-alerting system.

### Refresh workflow

Both the **Refresh data** button and the argument-free `refresh-data` command
run the entire pipeline: validate/publish each daily aggregate snapshot,
then rebuild the anomaly catalog. Dash shows a live progress panel instead
of a loading spinner and reloads the current page after completion.
The background worker uses the persistent `ANALYTICS_DATA_DIR` volume.
See [operations](docs/operations.md) for concurrency and failure behavior.
