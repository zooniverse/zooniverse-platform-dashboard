# Command-line reference

The installed commands operate on the same persistent snapshot directory as
the dashboard. Activate the installed Python environment before running them,
or execute them inside the Docker Compose service.

## Commands at a glance

| Command | Arguments | Access required | Result |
|---|---|---|---|
| `refresh-data` | None | Read-only Panoptes SQL and public ERAS HTTPS | Re-fetch selected history, validate/publish three activity snapshots and the independent project-launch snapshot, then rebuild the anomaly catalog |
| `set-date-limit` | Optional `DAYS` | Writable local snapshot directory only | Print effective history limit or persist a new one |
| `rebuild-anomalies` | None | Existing aggregate JSON snapshots only | Rebuild the anomaly catalog without contacting upstream systems |

These are the packaged console scripts defined by `pyproject.toml`.
The application always re-fetches the selected history window; it does not
incrementally backfill or reconcile individual recent days.

## `refresh-data`

```bash
refresh-data
```

Runs the same pipeline as clicking **Refresh data** in the UI:

1. Apply the effective date limit to completed UTC dates (through yesterday).
2. Query daily Panoptes registrations and public ERAS classifications and Talk
   totals for the **full selected window**, rather than appending only recent days.
   Collect approved Panoptes project launches separately, including publicly
   shareable project titles, for the same completed-day cutoff.
3. Validate and atomically publish each successful activity snapshot and the
   independent `project_launches.json` file; a failed stream keeps its prior
   valid snapshot.
4. Rebuild `anomalies.json` from available validated snapshots, then report the
   overall outcome.

The command writes progress to terminal logs. The UI starts a detached Python worker and polls durable status to display
progress and automatically reload after full success. A durable **60-minute cooldown** and cross-process lock prevent repeated/concurrent
refreshes. On source failure, the previous validated snapshot remains in place;
anomaly results that no longer match a source snapshot are withheld as stale.
Any of the four collection stages can fail independently; the overall refresh
can partially succeed. Inspect the reported state and
[Operations](operations.md) for diagnostics.

**Example:** after cloning, installing and configuring `.env`, run
`refresh-data` to populate your initial local dataset. A full-history request
may be substantially larger than a short-window request.

## `set-date-limit`

```bash
set-date-limit          # Print the currently effective setting
set-date-limit 365      # Keep the most recent 365 completed UTC dates
set-date-limit 28       # Keep the most recent 28 completed UTC dates
set-date-limit 0        # Restore all available history
```

A positive integer means that number of **completed UTC calendar days**, not
hours back from the current moment. Zero means all available history; unset
`ANALYTICS_HISTORY_DAYS` also defaults to all-time.

The command saves an override in `collection_config.json` under
`ANALYTICS_DATA_DIR`. The override takes precedence over the process's
environment variable and persists across container restarts when the volume
is retained. This command **does not** collect data or recompute anomalies:
run `refresh-data` afterward to apply it.

A short history window may lack the 730 training and 180 calibration warm-up
days required to display anomaly episodes. Source KPI charts can still work
with a shorter window.

## `rebuild-anomalies`

```bash
rebuild-anomalies
# Equivalent module entry point:
python -m zooniverse_dashboard.anomalies
```

Reads only `users.json`, `classifications.json`, and `talks.json` from the
configured local data directory. It fits the production NB2 variants, ranks
historical predictive surprises, groups related days into episodes, and
atomically publishes `anomalies.json` for `/anomalies` and clickable chart
markers. There are **no database or network requests**.

`refresh-data` already calls this operation after data collection, so a manual
rebuild is only needed when, for example, you changed the analysis code,
restored snapshots, or need to recover a stale catalog.

The detector can require substantial CPU time on longer series. Keep this
separate from the Dash page-rendering path; see [Anomaly detection](anomaly_detection.md)
for the exact model, time windows, assumptions and failure behavior.

## Docker command equivalents

```bash
docker compose -f deploy/compose.yml exec dashboard refresh-data
docker compose -f deploy/compose.yml exec dashboard set-date-limit 365
docker compose -f deploy/compose.yml exec dashboard rebuild-anomalies
```

The commands above update **the container's `/data` volume**, not your local
`.local/data`. Do not refresh two separate deployments expecting their files
to be synchronized.
