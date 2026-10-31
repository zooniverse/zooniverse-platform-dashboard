# Page architecture refactor: behavioral baseline

This document tracks the UI consolidation that follows the `v0.1.0` release.
Phase 1 recorded the four page variants and established characterization tests.
Phase 2 extracted shared presentation code. Phase 3 consolidated calendar
aggregation and coverage checks without blending continuous activity data and
sparse project-launch data. Phase 4 consolidated atomic filesystem operations
and simplified refresh-source normalization. Phase 5A removes duplicated
calendar arithmetic and documents configuration consistency.

## One page skeleton, different specifications

Users, Classifications, Talk, and Projects already use the same broad layout:

1. Heading, source attribution and snapshot freshness/coverage notices.
2. Ordered metric cards with coverage-aware totals.
3. Visible-window selector, aggregation selector and chart-type selector.
4. A Plotly graph with a range slider, browser-side Y autoscaling and shared
   UTC zoom state.
5. Optional trace layers appropriate to that source and chart mode.

The differences are now recorded in
:class:`zooniverse_dashboard.page_specs.PageSpec`, with one immutable entry per
page in :data:`zooniverse_dashboard.page_specs.PAGE_SPECS`. For example:

```python
from zooniverse_dashboard.page_specs import PAGE_SPECS

users = PAGE_SPECS["users"]
projects = PAGE_SPECS["projects"]

assert users.default_interval == "daily"
assert projects.default_interval == "monthly"
assert users.has_layer("anomalies", interval="daily", plot_type="bars")
assert not projects.has_layer("anomalies", interval="monthly", plot_type="bars")
```

The model separates:

- **`cards`**: ordered card identities and labels, *not* computations. Activity
  counts come from `calendar_periods`; Projects counts come from
  `project_metrics`.
- **`windows`**: available **visible time ranges** (activity: last 7/28/90/180/365
  days or all; Projects: 12/36/60 calendar months or all).
- **`intervals`**: allowed **aggregation bins** (activity: day/week/month/year;
  Projects: month/year).
- **`plot_types`**: line or bars.
- **`layers`**: additional traces and their permitted interval/plot combinations.
  Activity pages allow daily anomaly diamonds, launch triangles in every view,
  and the seven-day moving average on daily line charts. Projects does not
  need those layers because launches are the measured quantity.
- **`snapshot_kind`**: continuous activity histories versus sparse approved
  project launches. The two kinds have different validation/coverage semantics.

The page specification drives common Dash controls, IDs, card scaffolding,
and graph layout through `page_components.py`. Activity and Projects adapters
continue to own their independently validated snapshot loads, aggregate card
calculations, text about data provenance, and callbacks. The activity figure
builder also checks `PageSpec.has_layer()` for anomaly diamonds, project-launch
triangles, and the trailing-average trace. Each layer still owns its own
trace-building logic; there is no generic trace dispatcher.

## Behaviors to preserve

| Contract | Activity pages | Projects page |
| --- | --- | --- |
| Snapshot source | Validated continuous daily activity JSON | Validated sparse launch JSON |
| KPI cards | Seven UTC calendar cards | Five launch cards |
| Initial chart | Last 90 days, daily line | Last 36 months, monthly bars |
| Calendar buckets | Day/week/month/year | Month/year, including zero-launch bins |
| Grouped bars | Numeric calendar slots; partial widths | Same geometry |
| Lines/daily bars | Real UTC date axis | Real UTC date axis for lines |
| Launch marker layer | Optional, violet baseline triangles | None |
| Anomaly layer | Daily diamonds only | None |
| Seven-day average | Daily line only | None |
| Hover and privacy | UTC weekday, public names escaped | Launch totals only |
| Shared zoom | UTC dates in `layout.meta.history_zoom` | Same metadata contract |
| Upstream requests in page render | None | None |

**Non-negotiable invariants:** preserve the existing Dash component IDs,
route paths and callbacks; use the same UTC boundaries and zoom metadata;
preserve source-specific Y-axis scaling; never derive missing historical data
or treat private launch titles as public; and preserve the existing behavior
when optional snapshot data is absent.

## Phase 2 extraction boundaries

- `page_components.py`: reusable page skeleton, cards, controls, and missing-data
  messages, all built from an immutable `PageSpec` without source I/O.
- `ui.layout_for()` and `projects_ui.projects_layout()`: small source adapters
  which supply already validated snapshots and source-specific text.
- `chart_geometry.py`: shared, Dash-free calendar-axis labels, tick selection,
  partial-bucket widths, and fractional launch positions; Projects no longer
  imports private functions from the activity UI.
- Figure builders remain independent, retaining their callbacks and browser
  zoom contract. Activity optional traces now honor `PageSpec.layers` for
  anomaly diamonds, project launch markers, and the trailing average.

## Phase 3: calendar aggregation and coverage semantics

- `chart_data.group_daily()` now aggregates both kinds of validated daily
  series. With no explicit `coverage`, it retains the activity-series contract:
  only observed periods are returned. With an inclusive `coverage=(first, last)`,
  it fills *calendar periods* with no events, even when sparse launch snapshots
  omit every day in that period. It does not materialize a daily zero row for
  every missing date, which keeps all-history launch aggregation efficient.
- `chart_data.next_bucket_start()` supplies the following UTC boundary for day,
  ISO week, month, or year. `projects_data.next_period()` remains as a
  month/year-only compatibility adapter for the existing Projects chart.
- `periods.covered_period_total()` centralizes the rule for determining whether
  a summary interval has sufficient coverage. It returns zero for an interval
  containing no completed UTC days, `None` for an incomplete nonempty interval,
  and delegates the actual sum to the caller only when all dates are covered.
  Activity counts still use the existing prefix-sum calculation, while Projects
  still totals approved launches in the sparse snapshot.
- Coverage and counts have deliberately separate responsibilities: a date
  outside the launch snapshot is **unknown**, not zero; a missing launch date
  *within* the validated coverage is zero. This distinction also applies to
  partial first/last months or years. Neither chart or KPI calculation
  extrapolates an incomplete calendar interval.
- The existing figure functions, snapshot JSON formats, collector queries,
  anomaly detection, browser zoom metadata, and UI callbacks remain unchanged.
  Characterization tests protect all 28 figure variants and their layouts.

## Phase 1 characterization and ongoing regression testing

- `tests/test_page_specs.py` runs without Dash and verifies the page contracts,
  permitted overlays, options, defaults, and consistency with the actual KPI
  aggregation functions.
- `tests/test_page_characterization.py` uses synthetic in-memory snapshots to
  exercise the existing Dash layouts and **all 24 activity figure variants**
  (three sources, four intervals and two plot types) and **all four Projects
  figure variants** (two intervals, two plot types). It checks control IDs and
  defaults, trace identities, tooltip escaping, zero-launch periods, baseline
  markers, calendar axes and Plotly zoom metadata.
- Existing `tests/test_history_zoom_js.py` and
  `tests/test_history_autoscale_js.py` protect browser-side behavior; the
  refresh, snapshot and anomaly test suites remain in place.

Run `pytest -q`, `black --check src tests`, `ruff check .`, and
`sphinx-build -b html -W --keep-going docs docs/_build/html` before committing.
The Dash characterization tests require the project's regular dependencies;
they are skipped in minimal installations without Dash.

## Phase 4: persistence and refresh infrastructure

JSON files have format-specific validation in their owner modules, but share
identical filesystem durability requirements. The `storage.py` module handles
private temporary files, file and parent-directory syncing, atomic replacement,
and temporary cleanup. Schema validation, existing-file checks, upper size
bounds and error presentation remain in the activity, launch, anomaly and
refresh-status modules. In particular, anomaly catalogs retain their own size
limit rather than inheriting the activity snapshot limit.

An atomic rename prevents readers seeing partially written files. It cannot
roll back an already-completed rename if the subsequent directory sync fails:
callers receive that failure and should not assume the previous file survived.
The shared writer is intended for a single access-controlled POSIX volume,
not cross-host coordination or a defense against a hostile directory owner.

The refresh coordinator retains its advisory lock and cooldown. Activity
source fetch normalization now lives in one private helper, keeping full- and
bounded-history ERAS reconciliation distinct from snapshot publication.
Project-launch collection remains an independent stream, and only the three
activity streams feed anomaly analysis.

## Phase 5A: calendar boundaries and configuration consistency

`chart_data.next_bucket_start()` is the single implementation of daily,
Monday-starting weekly, monthly, and yearly calendar-boundary arithmetic.
`chart_geometry.next_bucket_start()` delegates to it but retains the stricter
week/month/year validation required by numeric grouped-bar axes. The Projects
`next_period()` compatibility helper also delegates to the same calendar
function, with its own month/year restriction. These adapters deliberately
preserve their caller-facing validation policies rather than making the chart
generators or Projects data layer depend on one another.

The allowed Projects chart windows and intervals are enforced in the domain
module (`projects_data.py`) and displayed by the declarative page spec
(`page_specs.py`). Rather than importing UI specifications into the domain
layer, regression tests assert that the two sets remain identical. They also
verify that all activity-page interval options belong to the chart aggregation
contract. Presentation labels and defaults remain the responsibility of
`PageSpec`, while domain validation remains independent of Dash.

No persisted data formats, default windows, chart types, hover content, zoom
metadata, queries, or refresh behavior change in this phase.
