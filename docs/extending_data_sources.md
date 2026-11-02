# Adding an aggregate data source

A new **page** and a new **source** are not interchangeable. Pages may reuse
existing snapshots. A new source changes remote collection, storage contracts,
refresh coordination and perhaps anomaly analysis. This guide shows the
current integration points; the codebase does **not** have a generic plug-in
registry that automatically wires them together.

Read [Architecture](architecture.md), [Privacy](privacy.md), and
[Operations](operations.md) before modifying a collector.

## Start with the data contract

Before writing code, document:

- **Question and unit:** events per UTC day, projects launched per UTC date,
  or something else? Does it represent people, actions or records?
- **Source and authorization:** read-only SQL or documented aggregate HTTP
  endpoint; precise fields permitted to leave the source; rate/size/time limits.
- **Time:** timezone assumption, `through` cutoff (last completed UTC date),
  earliest available date and whether missing dates actually mean zero.
- **Coverage:** dense daily counts versus sparse observations. The latter
  requires explicitly validated first/last coverage to distinguish zero from
  unavailable history.
- **Persistence:** exact canonical JSON schema, bounded payload size, path
  and permissions, and whether regeneration can replace a current file.
- **Analysis eligibility:** Should anomaly detection treat the new data as
  a continuous activity count? Projects intentionally does **not** participate.
- **Operator behavior:** failure messages, partial refresh, cooldown, status,
  data freshness, and how to rebuild after an upgrade.

The privacy rule is not negotiable: **do not pull individual activity rows into
Python to aggregate them later**. Panoptes queries must return grouped counts,
using fixed SQL shapes, bound parameters and a read-only role. The project's
special public-title allowance does not extend to other fields or sources.

## Trace existing implementations

| Concern | Activity streams | Approved project launches |
|---|---|---|
| Retrieval | `sources.fetch_panoptes`, ERAS `fetch_full_*`/`fetch_window_*` | `project_launches.fetch_project_launches` |
| Validation and schema | `snapshots.Snapshot`, `publish_snapshot`, `load_snapshot` | `LaunchSnapshot`, `publish_launches`, `load_launches` |
| Coverage | Continuous UTC daily buckets including zeros | Sparse nonzero launch dates plus explicit first/last |
| Refresh dispatch | `refresh.refresh_source`, `REFRESH_STREAMS` | `refresh.refresh_launches`, `LAUNCH_STREAM` |
| UI data | `periods.calendar_periods`, `ui.history_figure` | `projects_data.project_metrics`, `projects_ui.projects_figure` |
| Anomalies | `anomalies/catalog.py` for `snapshots.SOURCES` | Excluded |

`SOURCES = ("users", "classifications", "talks")` in `snapshots.py` is the
current **activity** registry. `refresh.REFRESH_STREAMS` combines it with the
independent `project_launches` stream. Adding a new source is a deliberate
multi-module change; placing it in `SOURCES` without a matching collector,
parser, figure and analysis decision is incorrect.

## Implementation sequence

1. **Prove source semantics with read-only discovery.** For SQL, inspect
   metadata and aggregate completeness without fetching people or project
   owners. For HTTP, validate the published aggregate contract and bounded
   response with synthetic fixtures. No production writes.
2. **Implement one typed collector** with `through: date` and a validated
   `history_days` policy as appropriate. Use fixed SQL/URLs, network timeouts,
   strict validation and well-defined exceptions. Return only aggregate data.
3. **Choose a snapshot model.** Reuse `Snapshot` for a *continuous* daily event
   count only when its semantics truly match. For sparse or other shapes,
   define a separate validated format. Use `storage.atomic_publish_json` or
   the existing shared storage mechanism; avoid bespoke unsafe file writes.
4. **Wire collection and status explicitly** in `refresh.py`: dispatch,
   `REFRESH_STREAMS`, per-source status, logging and failure isolation. Then
   inspect `refresh_job.py`, `pipeline.py`, and the global status display
   in `app.py` for hard-coded counts or labels that need updating. The
   coordinator must retain its shared lock and 60-minute cooldown.
5. **Decide analysis eligibility.** If included in NB2, update
   `snapshots.SOURCES`, source checks in `anomalies.compute.score_days`,
   `anomalies.catalog`, and UI episode labels/routes. Do not assume that any
   daily sequence is statistically comparable to the three current streams.
6. **Add its presentation** via `PageSpec`, a page module and an appropriate
   loader/figure/callback. Use [Adding/changing pages](extending_pages.md).
7. **Test a full lifecycle:** synthetic fetch → validation → atomic JSON →
   offline analysis when applicable → graph → refresh partial failures.
8. **Document re-collection** and required credentials/ingress rules. This
   project supports one canonical data format, not silent format migrations.

## Why there are two snapshot representations

`Snapshot.daily` is a dense, consecutive sequence of date/count pairs and
requires each day in the validated range. An absent date is a validation
failure unless the *collector* explicitly fills a justified zero. For
`LaunchSnapshot`, only positive launch dates need rows, but `first` and `last`
still bound the range in which absent days mean zero. The common calendar
aggregator can accept sparse rows **only with coverage supplied**. Do not
conflate sparse source records with missing reporting days.

Calendar KPIs use `periods.covered_period_total` to reject incomplete
historical intervals; charts do not extrapolate incomplete boundary periods.
`as_of` represents midnight UTC after the final completed date in an activity
snapshot, not the wall-clock time at which a refresh was requested.

## Reliability and safety tests

- Incorrect timezones/dates, duplicate buckets, negative/noninteger totals,
  oversized responses, missing coverage and invalid source IDs are rejected.
- Every write is atomic, private and bounded. Test simulated failures before
  replacement; other sources retain their previous snapshot.
- Status and logs disclose stage, source and sanitized error **class**, not
  raw SQL messages, URLs with credentials, response bodies or records.
- The source works with all-history (`0`) and finite limits, including an
  intentionally empty bounded date range when the schema allows it.
- A failed source cannot block other streams, nor let old analysis silently
  masquerade as current (catalog snapshot-revision checks).
- No SQL or HTTP runs when a user navigates to a chart or changes controls.

Relevant tests include `test_sources.py`, `test_eras_full_history.py`,
`test_project_launches.py`, `test_snapshots.py`, `test_refresh.py`,
`test_refresh_pipeline.py`, and `test_storage.py`. Keep these local and
synthetic; an authorized live refresh is an *additional* operator acceptance
test, not a pytest fixture.
