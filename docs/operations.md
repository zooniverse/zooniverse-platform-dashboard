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
refuses a second refresh attempt within 60 minutes. The refresh button runs
synchronously in a Gunicorn worker. Large ERAS responses may hit API limits or
the web request timeout; the CLI is useful for explicitly running such a
collection, but it is still subject to request validation and the cooldown.

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
steps. Logs go to **standard error** (shown in a terminal and captured by
Docker/Gunicorn logging). Nothing writes a separate log file to the analytics
volume.

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
The existing `refresh_status.json` remains the minimal persistent status file:
it is *not* an exhaustive audit log. For historical troubleshooting, rely on the
container platform's log retention and access controls. Avoid routing logs into
public or unprotected storage.
