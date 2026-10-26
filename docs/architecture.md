# Architecture

Three sources, one Dash application, one persistent POSIX Docker volume:

```
Read-only Panoptes PostgreSQL    Public ERAS counts
           \                  /
         Refresh coordinator (manual; full configured range)
          lock + 60-min cooldown
                   |
        validated daily aggregates
       atomic JSON in mounted /data
                   |
              Dash pages
     users / classifications / talks
```

The refresh callback is the **only** web path allowed to reach remote sources.
Page functions and Plotly callbacks operate exclusively on JSON snapshots. A source
failure does not replace the last valid snapshot for that source. A separate small
status file records generic success/failure states without exception text.

Each `users.json`, `classifications.json`, `talks.json` uses the same schema:
`format`, `source`, `first`, `last`, `as_of`, `last_modified_utc`,
`history_complete`, `history_days`, `daily`.
The timestamp is generated at successful publication time on every refresh, even when counts have not changed.
`as_of` denotes midnight UTC immediately after the last completed date.

Dashboard snapshots cannot be shared across independent container replicas unless
those replicas share the **same** POSIX locking-capable volume; deploy one replica.

All three sources are completely refetched on each manual refresh, for
`ANALYTICS_HISTORY_DAYS` completed UTC days or all available history when set
to zero. The persistent `collection_config.json` override (managed by
`set-date-limit`) takes precedence over the environment default. Snapshots
record `history_days`, and the UI warns if configuration has changed since
publication. There is no incremental reconciliation or historical backfill
workflow.

## Anomaly analysis and inspection

The separate `zooniverse_dashboard.anomalies` package performs rolling NB2
analysis on validated daily JSON snapshots, **not** on API responses or
individual records. Users use a calendar-aware model; Classifications and
Talk use baseline NB2. Daily model surprise and rolling historical rarity
are preserved separately and grouped into episodes. The old weekday-MAD
chart detector is no longer used.

The `refresh-data` CLI and Dash **Refresh data** button share a single
refresh-then-rebuild pipeline. For the browser, Dash runs the longer operation
as a Diskcache background callback and displays progress before reloading the
current page. `rebuild-anomalies` also supports analysis without refreshing
remote data. The analysis writes an atomic `anomalies.json` on the same
volume; Dash only reads the catalog, hides results whose source revision
no longer matches, and never fits the numerical model during navigation.

Chart diamonds link to a selected episode on `/anomalies`; **Show on graph**
returns to the originating source chart zoomed around that episode. Shared
drops provide unverified context, not an outage determination. Concurrent
spikes do not count as cross-source corroboration.

See [Anomaly detection and episode inspection](anomaly_detection.md) for the
model, equations, past-only fitting protocol, thresholds, assumptions,
interpretation, failure handling and implementation references.
