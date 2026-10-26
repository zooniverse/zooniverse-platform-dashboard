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

## Offline anomaly inspection

The dashboard no longer uses the weekday-MAD detector. A separate, offline
`zooniverse_dashboard.anomalies` package reads existing validated aggregate JSON
snapshots and creates an atomic `anomalies.json` catalog on the same volume.
Run `rebuild-anomalies` or `python -m zooniverse_dashboard.anomalies` after each
manual aggregate refresh. The `/anomalies` page and existing history charts
**only read the catalog**; neither navigation nor plotting fits NB2 or makes
remote requests. A changed source snapshot makes its cached anomaly results
stale and hides them until rebuilding, avoiding misleading old markers.

The initial inspection catalog evaluates at most the last 730 completed UTC days
per source, after up to 730 training days and 365 earlier out-of-sample days for
empirical calibration. A shorter snapshot may be insufficient. Model fitting
uses 28-day anchored, past-only refits, profile-likelihood NB2, weekday/trend
predictors, and Christmas/New Year indicators for Users only. NB2 tail and
historical-rank scores are shown separately. Events can be marked by one or
both; adjoining candidate days become episodes. These scores have no guaranteed
false-positive rate and do not establish an incident, campaign, or outage.
Shared drops are context only; simultaneous spikes are never corroboration.
Source zero-filled daily counts cannot establish provider bucket completeness.

Marker clicks navigate to `/anomalies?event=<id>` and highlight the episode at
the top of the list. Each episode's `Show on graph` link returns to the matching
source page with a URL query parameter that zooms around the episode. No
server-side sessions or user-specific state are retained.
