# Architecture

Three activity sources plus an independent project-launch overlay, one Dash
application, one persistent POSIX Docker volume:

```
Read-only Panoptes PostgreSQL    Public ERAS counts
  users + project launches      classifications + Talk
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

The web button starts a detached refresh worker; that worker is the **only**
web-triggered path allowed to reach remote sources.
Page functions and Plotly callbacks operate exclusively on JSON snapshots. A source
failure does not replace the last valid snapshot for that source. A separate small
status file records generic success/failure states without exception text.

Each `users.json`, `classifications.json`, `talks.json` uses the same schema:
`format`, `source`, `first`, `last`, `as_of`, `last_modified_utc`,
`history_complete`, `history_days`, `daily`.
The timestamp is generated at successful publication time on every refresh, even when counts have not changed.
`as_of` denotes midnight UTC immediately after the last completed date.

An independent `project_launches.json` stores sparse, validated daily counts
and available non-private public titles for approved Panoptes launches. It is
collected in a fourth refresh stage, atomically published and plotted as dated
markers on all three activity charts without entering the anomaly detector or
affecting activity totals. V1 count-only snapshots remain readable; missing
overlays cannot prevent normal graphs from rendering. See
[Project launch markers](project_launch_markers.md).

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
refresh-then-rebuild pipeline. For the browser, Dash schedules a detached worker that writes durable progress
to `refresh_job.json`; the UI polls status and reloads the current page only
on success. `refresh_status.json` also records each completed source. `rebuild-anomalies` also supports analysis without refreshing
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

## Historical charts

All chart styles read only the validated daily snapshots. The **Day / Week /
Month / Year** controls sum daily counts into UTC calendar intervals (ISO weeks
start Monday). The first and last bucket may represent only a partial calendar
period; they are never extrapolated into a full interval. **Line / Bars** changes
the rendering, not the totals. Seven-day trailing averages appear in daily line
mode only. The KPI cards remain unchanged by the chart controls.

Plotly starts in rectangular **Zoom** mode; **Pan** is available in the toolbar.
The Y axis initially fits the selected time window, and browser-side Plotly
listeners recompute it after horizontal panning or range-slider changes. A manual
2D box zoom preserves the Y range selected by the user. This adjustment happens
entirely in the browser, without reloading snapshots or running server callbacks.

Anomaly detection remains daily. Its clickable episode diamonds appear on daily
charts (line or bars), rather than being assigned potentially misleading
weekly/monthly/yearly detection dates.

Daily charts and all line charts use a true UTC date X-axis. **Grouped bar
charts (Week / Month / Year)** instead use equal-width, discrete calendar slots
with readable period labels, independent of how many days a month or year has.
The first and last *partially covered* calendar buckets occupy only the
corresponding fraction of their slots, so they are not drawn as full periods.
The range slider and Plotly pan/zoom continue to work; the browser chooses
period-label density for the visible range rather than printing daily ticks.

The project-launch overlay uses true daily dates on line charts. On grouped
bar charts, a launch is placed at its proportional calendar-day position
*inside* the corresponding bucket (not shifted to its start or center), and
still shows its exact UTC date and public project names on hover. Compact
violet triangles sit at Y=0 on every chart, drawn in front of the teal bars
without white outlines to avoid merged blobs when launches cluster. Neither
interpretation changes launch counts, rolling averages, or anomaly detection.
