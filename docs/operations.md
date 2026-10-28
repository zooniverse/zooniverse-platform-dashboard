# Refreshing analytics and selecting a date range

This internal dashboard uses **manual, complete-window refreshes**. There is
no cron job and no incremental/backfill logic. Each refresh queries Panoptes
and both ERAS endpoints for the *entire configured history window* and
atomically replaces each validated daily-count JSON file. Dash pages read only
the published JSON files, never production sources.

## Configured history window

Set `ANALYTICS_HISTORY_DAYS` in the environment (or `.env`) to select the
**number of completed UTC calendar days** to retrieve. If it is unset or
`0`, all available history is requested, with no lower date bound.

| Setting | UTC history requested when refreshed on October 23, 2026 |
|---|---|
| `0` (default) | All available days through October 22 |
| `7` | October 16–22 (inclusive) |
| `28` | September 25–October 22 (inclusive) |
| `365` | October 23, 2025–October 22, 2026 (inclusive) |

Only nonnegative integers up to the snapshot storage bound are accepted;
invalid values cause an explicit error and **never** silently trigger an
all-history fetch. The setting applies identically to Users, Classifications,
and Talk; the actual first available date may differ between sources in
all-time mode.

### Set the limit without rebuilding the container

In the installed environment:

```bash
set-date-limit             # display effective setting
set-date-limit 365         # store a 365-day limit
set-date-limit 0           # remove the date restriction: all available history
refresh-data               # refresh all three sources (no arguments)
```

In Docker:

```bash
docker compose -f deploy/compose.yml exec dashboard set-date-limit 365
docker compose -f deploy/compose.yml exec dashboard refresh-data
```

**Important:** A running process cannot change its own Docker environment
variables persistently. `set-date-limit` writes a validated
`collection_config.json` override to the same persistent `/data` volume as
the snapshots. The precedence is **volume override >
`ANALYTICS_HISTORY_DAYS` > default `0`**. Changing an environment value alone
does not override an existing file; `set-date-limit 0` means *all-time*, not
"fall back to the environment". A subsequent `refresh-data` or web refresh
is required to regenerate snapshots under the new limit. The dashboard warns
when its snapshot was built using a different limit or an older schema.

The command takes the same POSIX lock as the web refresh, so no configuration
change can occur in the middle of a refresh. The refresh retains its one-hour
cooldown across web and CLI calls; after changing the limit, wait for the
cooldown if necessary.

## Collection and safety

- `users`: an approved read-only PostgreSQL aggregate query on
  `public.users.created_at`, optionally bounded below by the selected date.
  Only daily counts leave PostgreSQL; individual user records are never read
  by Python. The timestamp's UTC interpretation remains to be confirmed with
  the Panoptes maintainers.
- `classifications` and `talks`: a single public ERAS daily aggregate request
  per source. For `0`, the request omits `start_date`; for a bounded window it
  includes both `start_date` and `end_date`. Totals, bucket dates, response
  size and date spans are validated. No user-level data is requested.
- Snapshots are published atomically and independently on the mounted volume.
  A failed source leaves its previous JSON intact; the generic status file
  shows partial failure without exposing credentials or raw server errors.
- Each JSON records `last_modified_utc`, `history_days`, coverage, daily
  counts and `history_complete` (`true` only for all-time requests). Missing
  calendar buckets within the requested interval are filled with zero.
- One dashboard instance must use one persistent, POSIX locking-capable
  volume; multiple isolated replicas are not supported.

The cross-worker lock rejects simultaneous refreshes, and the coordinator
refuses a second refresh attempt within 60 minutes. The refresh button starts
a detached worker rather than performing collection in the HTTP request.
Large ERAS responses may still hit API limits; the CLI is useful for running a
collection independently, but remains subject to validation and the cooldown.

## Access and deployment

Use one Dockerized Dash/Gunicorn service, a persistent named volume at `/data`,
and an authenticated TLS ingress. Configure `DB_URL_PANOPTES` as a server-side
secret or environment variable; never place it in source control. The refresh
button is available to visitors, so restrict **the entire dashboard** at the
ingress (SSO/VPN/reverse proxy).

`/healthz` indicates process availability, not snapshot freshness. No
scheduled collection service, separate bucket, or CI deployment workflow is
installed.

## Refresh logs and diagnostics

Both entry points—`refresh-data` in the terminal and **Refresh data** in the
web dashboard—use the same refresh coordinator and log the same operational
steps. CLI logs go to **standard error** (shown in a terminal); the detached web
worker writes sanitized diagnostics to `ANALYTICS_DATA_DIR/refresh-worker.log`
(mode 0600) because it is not attached to the Gunicorn output stream. Retain
and rotate this private file according to the operator's log-retention policy.

```bash
refresh-data
# Or observe the web application's output:
docker compose -f deploy/compose.yml logs -f dashboard
```

Each line has a UTC timestamp, severity and an `event=` key. A refresh is given
an ephemeral `run=` identifier to correlate the source events. Expected events
include `refresh_requested`, `refresh_started`, `source_fetch_started`,
`source_fetch_completed`, `snapshot_published`, `source_succeeded` and
`refresh_finished`. Recorded fields include the UTC reporting cutoff, effective
history limit, source name, bucket count, **aggregate** total, snapshot
coverage, and elapsed milliseconds. A successful snapshot publication is logged
*after* atomic replacement of its JSON file.

On source failure, logging records its **stage** (`fetch_and_validate` versus
`snapshot_publication`), exception **class** and a sanitized chain of Python
source-file/function/line locations. These are useful when investigating failures
without capturing production user records. The logger **does not** print raw
exception messages, SQL parameters, database URLs, HTTP response bodies,
credentials, or Python locals. For example, a low-level PostgreSQL error may
appear as `error_type=OperationalError`, not its potentially sensitive message.
If the exact database error is needed, investigate it through an authorized DBA
workflow rather than enabling unsanitized exception logging.

A second click during the one-hour cooldown logs `refresh_rejected`. Failed
sources retain their previous valid JSON; an attempt with partial failures logs
`state=partial_failure` and `refresh-data` returns exit status 1. A rejected
CLI attempt returns exit status 2. Successful CLI runs return exit status 0.
The existing `refresh_status.json` records the collection stage and is updated
**after each individual source**. The separate `refresh_job.json` reports the
end-to-end worker state and progress, including anomaly rebuilding. Neither is
an exhaustive audit log; keep the worker log on private storage.

## One-click refresh and anomaly rebuild

Click **Refresh data** to run the same pipeline as `refresh-data`: refresh all
three aggregate sources, then rebuild the local anomaly catalog. The web
request schedules a **detached Python subprocess**. Dash polls its small,
atomically published `refresh_job.json` every second **only while a job is
queued or running**, to render the step-by-step progress panel. Fast polling
is disabled when idle or after the job reaches a terminal state. An existing
60-second status check can discover a refresh started in another browser
session and turn fast polling on; opening the dashboard during an active job
also enables it immediately. The job does not depend on browser navigation,
Dash callback polling, or a live page. The browser reloads the same URL **only after the job
reports full success**. After reloading, the detailed progress panel is
replaced by a compact green success banner for about one minute. With fast
polling stopped, the next regular status check clears it once expired. Failures and partial successes retain their details in
the panel instead of hiding stale-data warnings. No automatic collection
schedule is enabled.

The job uses the same interpreter and runtime environment as the web server.
`ANALYTICS_DATA_DIR` must be writable, and the worker needs the same read-only
Panoptes access as the CLI. Single-host POSIX file locks coordinate admission,
worker lifetime and existing 60-minute collection cooldown. If the worker
vanishes without finalizing its status, a stale `queued`/`running` job is
reported as **interrupted** after a brief startup grace, provided no worker
still holds its job lock. Read-only snapshot loading remains unchanged.

The private `refresh-worker.log` contains sanitized operational logging; it may
be inspected after an interruption without printing SQL, URL strings, provider
responses or exception messages. File access must be restricted to operators.
Do not expose the refresh button to untrusted visitors; use authenticated
ingress. This implementation assumes a **single application replica and one
shared POSIX-locking volume**. A distributed deployment would require a
proper external job queue and shared job status store.

If a source fails, its prior validated snapshot remains available, and the
anomaly builder runs on the snapshots currently present. Catalog publishing
is atomic. Rejected overlapping/cooldown requests do not launch analysis.
The progress panel never prints raw exception strings. The saved refresh status
shows source failures, and the dashboard status line indicates how many anomaly
analyses match their current snapshots. A failed anomaly rebuild leaves any previous catalog
untouched, and stale episode markers are withheld until rebuilt.

The anomaly method, thresholds, catalog revision rules, limitations, and
meaning of cross-source notes are documented in
[Anomaly detection and episode inspection](anomaly_detection.md).
