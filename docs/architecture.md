# Architecture

Three activity sources plus one project-launch snapshot, one Dash
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
     users / classifications / talks / projects
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
markers on all three activity charts and is also summarized on
the **Projects** page without entering the anomaly detector or affecting activity totals.

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
are preserved separately and grouped into episodes.

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

**Shared chart viewport:** Browser-side `assets/history_zoom.js` listens for
horizontal Plotly relayout events (rectangle zoom, pan and range slider),
converts the X range into an **absolute UTC timestamp interval**, and stores
only those two timestamps in the current browser tab's `sessionStorage`.
Each figure publishes its source, coverage, window preset and calendar bucket
boundaries as Plotly layout metadata. When the user changes Day/Week/Month/Year
or Line/Bars, the new figure restores the same dates: numeric grouped-bar
positions are converted via their actual period boundaries (including leap
years and partial periods). The same stored dates are restored on Users,
Classifications, Talk and Projects, including after normal page navigation or reload.
A source with shorter coverage clips the *displayed* range without changing
what is stored, and Y scaling remains specific to each chart. Explicitly
changing **Visible time window** clears the shared manual zoom; Plotly's
**Reset axes** also clears it. An anomaly's **Show on graph** deep link takes
precedence and adopts that episode's view as the new shared range. When
session storage is unavailable, charts retain their original independent
behavior. No remote queries or new snapshot fields are required, and only
two dates (never per-user or project data) are persisted in the browser.

The project-launch overlay uses true daily dates on line charts. On grouped
bar charts, a launch is placed at its proportional calendar-day position
*inside* the corresponding bucket (not shifted to its start or center), and
still shows its exact UTC date and public project names on hover. Compact
violet triangles sit at Y=0 on every chart, drawn in front of the teal bars
without white outlines to avoid merged blobs when launches cluster. Neither
interpretation changes launch counts, rolling averages, or anomaly detection.

The **Projects** page uses `project_launches.json` directly to assemble a dense
series of **calendar months or years**, including zero-launch periods between
the snapshot boundaries. No daily project rows or second Panoptes query are
needed. The resulting bar/line charts share the browser-tab UTC viewport with
the activity charts using the same Plotly `history_zoom` metadata. The first
and last calendar periods retain their actual, potentially incomplete coverage.
See [Projects](projects.md).


## Maintenance and extension boundaries

The page refactor produced a deliberate distinction between **specification**
and **implementation**. `page_specs.PageSpec` declares a chart page's cards,
windows, aggregation options, chart types and permitted optional layers.
`page_components.build_metric_page` renders common Dash structure. The
source adapters in `ui.py` and `projects_ui.py` still load their respective
snapshot models, compute summary counts and construct different Plotly traces.
This avoids coupling UI presentation to the dense activity and sparse launch
counting contracts.

`chart_data.group_daily` performs shared calendar bucketing but requires
explicit validated coverage for sparse launch series. `periods.covered_period_total`
provides common coverage semantics, not a generic count provider.
`chart_geometry.py` and `figure_layout.py` share Plotly geometry, X-axis
selection and zoom metadata without merging unrelated figure builders.

`storage.py` supplies atomic, private filesystem publication. Each source
continues to own its own schema, validation and size limits. Refresh remains
serialized and can report partial failures; independently valid snapshots
are never discarded because another source is unavailable.

There is **no generic data-source or anomaly-detector plugin framework**.
Adding a new source requires explicit collection, schema, refresh and UI
integration; adding a detector requires changes to the offline catalog and
potentially its episode format. The authoritative deployment supports one
canonical persisted schema per data type, not chains of old-format readers.

For implementation procedures, follow [Maintainer handbook](maintainers.md),
[Dash introduction](dash_development.md), [Pages](extending_pages.md),
[Data sources](extending_data_sources.md), and
[Anomaly extensions](extending_anomalies.md). For code and test conventions,
see [Development practices](development_practices.md).
