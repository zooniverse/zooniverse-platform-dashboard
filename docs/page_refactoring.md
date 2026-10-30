# Page architecture refactor: behavioral baseline

This document tracks the UI consolidation that follows the `v0.1.0` release.
The **first phase does not change rendering**: it records what the four existing
metric pages do and establishes tests that must continue to pass when their
shared layout is extracted.

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

The specification is **not yet connected to the Dash renderers**. The existing
`ui.py` and `projects_ui.py` still own the behavior, including snapshots,
callbacks and figure creation. Keeping both implementations unchanged is
intentional during Phase 1; the new tests compare them to the specification so
that drift is detected before reuse.

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

## Phase 1 testing strategy

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

## Next refactor phase

Use `PageSpec` to extract shared **presentation-only** building blocks:
metric-card rendering, freshness/coverage notices, controls, graph scaffolding,
calendar-axis helpers, and common `history_zoom` metadata construction.
Keep each source's snapshot loader, KPI calculator, and figure-specific layer
construction explicit; avoid an overly generic renderer that obscures when
anomaly detection or project-launch data is actually available. The active
figure functions should remain public, with their existing inputs and outputs,
while implementation details migrate to shared modules in small tested patches.
