# Adding or changing dashboard pages

The four **metric pages** use the same basic layout. Their differences are
expressed as `PageSpec` data, source-specific KPI calculations and traces.
The **Anomalies** page is a different kind of view and is registered directly
through Dash Pages. Do not copy a whole page file just to change cards, bins,
or graph layers.

Start with [Dash essentials](dash_development.md) if you haven't worked with
Dash, then read `page_specs.py`, `page_components.py`, `ui.py`, `projects_ui.py`
and the existing `pages/*.py` modules. The relevant functions are
`ui.layout_for()`, `ui.history_figure()`, `ui.register_history_callback()`,
`projects_ui.projects_layout()`, and `projects_ui.projects_figure()`.

## Choose the scope of your change

| Goal | Primary location | Other integration points |
|---|---|---|
| Rename a card/option, change defaults | `page_specs.py` | Existing callback must accept the new value; tests |
| Add a KPI card | `page_specs.py` | Corresponding period/metric provider and renderer rows |
| Add a plot type or bin | `page_specs.py` | Aggregation logic, figure builders, axis metadata, browser tests |
| Change marker/hover/axis style | `ui.py` or `projects_ui.py` | `chart_geometry.py`, `figure_layout.py`, Plotly tests |
| Add an optional overlay | `PageSpec.layers`, figure builder | Validated local data, safe hover, source-specific applicability |
| Add a new chart route for an **existing** snapshot | `pages/`, `page_specs.py` | Adapter, callbacks and navigation IDs |
| Add a **new upstream metric** | Data-source guide first | Collector, canonical snapshot, refresh, anomaly eligibility, page |
| Add a non-chart inspection page | `pages/` | Own layout and callbacks; `PageSpec` may not be appropriate |

`PageSpec` is a **declarative contract**, not a plugin registry. Adding an entry
doesn't create its loader, chart callback, collector or API endpoint
implicitly. `__post_init__()` rejects duplicate cards/controls/layers and
invalid defaults. Supported `LayerKey` values currently are `anomalies`,
`project_launches`, and `trailing_average`.

## Anatomy of a metric page

The existing activity specification is defined in `page_specs.py`:

```python
# Representative excerpt, simplified from _activity_spec(...)
PageSpec(
    key="users",
    snapshot_kind="activity",
    title="User registrations",
    chart_title="Registration history",
    provider="Panoptes",
    count_noun="registrations",
    cards=ACTIVITY_CARDS,
    windows=ACTIVITY_WINDOWS,
    intervals=ACTIVITY_INTERVALS,
    plot_types=PLOT_TYPES,
    layers=ACTIVITY_LAYERS,
    default_window="90",
    default_interval="daily",
    default_plot_type="line",
)
```

`CardSpec(key, label)` defines display identity and order; it doesn't calculate
values. `Choice(value, label)` defines a stable callback value. `LayerSpec`
limits a trace to selected intervals and plot types (daily anomaly diamonds,
for instance). The `snapshot_kind` value distinguishes continuous daily
activity from sparse project-launch data.

`page_components.build_metric_page()` takes **already computed rows** and a
specification. For the activity pages, `ui.layout_for(source)` obtains a
validated `Snapshot` and calls `periods.calendar_periods(snapshot)`; for
Projects, `projects_ui.projects_layout()` obtains a `LaunchSnapshot` and calls
`projects_data.project_metrics()`. This intentional separation prevents a
shared renderer from inventing source-specific counting rules.

## Adding a route for an existing activity metric

The small page modules show the supported pattern. For example,
`pages/users.py` has this structure:

```python
from typing import Any
from dash import register_page
from dash.development.base_component import Component
from zooniverse_dashboard.ui import layout_for, register_history_callback

register_page(__name__, path="/users", name="User registrations", order=1)

def layout(**_kwargs: Any) -> Component:
    return layout_for("users")

register_history_callback("users")
```

This code is an **existing** source example, not a copy/paste implementation
for a new source. For another activity source you would need a corresponding
registered `PageSpec`, supported snapshot source key, loader, collector and
refresh integration (see [Adding a data source](extending_data_sources.md)).
The current `ui.register_history_callback()` explicitly rejects non-activity
specifications.

Dash discovers registered page modules from the `pages_folder` supplied to
`Dash(use_pages=True)` in `app.py`. The global navigation reads `page_registry`;
you ordinarily do not edit `app.py` just to add a registered route.

## Changing summary cards and grouping

1. Define the card's stable key and label in `page_specs.py`.
2. Update the source-specific summary calculation (`calendar_periods` or
   `project_metrics`) to return a matching row. Missing historical coverage
   must yield an unavailable value, not a misleading zero.
3. If adding a grouping interval, update its accepted values in the relevant
   data and geometry functions, and in the figure builder. Change both weekly
   and partial-bucket tests if needed. The Projects chart currently accepts
   only **month and year**; don't expose a daily selection without implementing
   a coherent design for it.
4. Update tests for spec/renderer synchronization and chart behavior.

Windows and intervals are two different things: **window** chooses the initial
visible range; **interval** determines how counts are binned. The stored
history limit is a separate, operator-controlled collection setting. Changing
a UI preset never authorizes a deeper upstream fetch.

## Adding an optional visual layer

A layer consists of an eligibility rule **and** a rendering implementation.
The existing `LayerSpec` tells the activity figure whether a trace is permitted
for a given `interval` and `plot_type`; `history_figure()` contains the actual
logic for generating that trace. An extension therefore must:

1. Decide the source and validated local format of the overlay. Do not query
   upstream systems inside the figure callback.
2. Add an allowed layer key and rules to `page_specs.py`, then update the
   appropriate figure builder to consult `spec.has_layer(...)`.
3. Place points at meaningful dates. Weekly/monthly/yearly **bars** use equal
   numeric slots, not Unix timestamps; use `chart_geometry.period_position()`
   and the figure's `history_zoom` metadata to preserve real UTC dates.
4. Escape or omit free-form strings in Plotly hover HTML. Preserve the
   existing missing-optional-snapshot behavior where appropriate.
5. Test layer eligibility on all supported modes, hover output, overlapping
   markers and the browser zoom/axis behavior.

Adding a new item to `LayerKey` alone does **not** add a working layer, and
new layers on the Projects page need their own rationale: its bars already
represent the launch dates that activity graphs show as markers.

## Cross-page chart behavior is a contract

`figure_layout.py` supplies shared Plotly layouts and
`history_zoom_metadata()`; `chart_geometry.py` handles calendar labels, widths
and marker offsets. `assets/history_zoom.js` stores **absolute UTC dates**
per browser tab and translates numeric bar positions using each figure's
period boundaries. `history_autoscale.js` rescales each metric independently.
Never replace the numeric grouped-bar axis with a date axis, or remove
`layout.meta.history_zoom`, without testing navigation through all pages.

Component IDs derive from page keys (`<source>-window`, `-interval`,
`-plot-type`, `-graph`, and for activity routes `-route`). A link into an
anomaly episode uses URL arguments interpreted by the activity callback.
Changing IDs or route names may also require updating JavaScript selectors
and episode deep links.

## Acceptance checklist

- A fresh browser renders the route with valid and missing snapshots.
- All controls have supported values and defaults, and each KPI has the
  intended coverage semantics.
- Graph generation never makes a remote call or triggers NB2 fitting.
- Plotly hover strings are safe; partial periods, leap years and zero-count
  intervals behave as specified.
- Zoom survives bin changes, line/bar switches, navigation and reload; the
  Visible time window control and Reset axes clear it intentionally.
- `test_page_specs.py`, `test_page_characterization.py`, relevant figure tests
  and JavaScript tests pass. Run Sphinx and update these guides.

For a genuinely new metric, follow [Adding a data source](extending_data_sources.md)
before treating it as a page-only change.
