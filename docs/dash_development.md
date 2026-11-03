# Quick look at Dash

[Dash](https://dash.plotly.com/) is a Python framework for interactive web
applications. Python components define the layout, and callbacks connect changes
in component properties to updates elsewhere in the page. Dash renders the
component tree in a browser and exchanges callback values with the Python
server. Plotly supplies the interactive figures.

Framework references: [Layout](https://dash.plotly.com/layout),
[Callbacks](https://dash.plotly.com/basic-callbacks),
[Interactive graphs](https://dash.plotly.com/interactive-graphing),
[Multi-page apps](https://dash.plotly.com/urls), and
[CSS/JavaScript assets](https://dash.plotly.com/external-resources).

## Layout, callbacks, and figures

**Layouts** are trees of Python components. `dash.html` represents HTML elements;
`dash.dcc` provides controls, browser stores, and `dcc.Graph`. Component IDs
connect the layout to callbacks. In this dashboard, the shared page renderer
uses page-specific IDs for windows and graphs (`users-window`, `users-graph`),
while the interval and plot-style controls have shared IDs on every metric page.

**Callbacks** map one or more `Input` properties to `Output` properties;
`State` supplies values without triggering execution. For example:

```python
from dash import Input, Output, callback

@callback(Output("example-label", "children"), Input("example-choice", "value"))
def update_label(value: str) -> str:
    return f"Selected: {value}"
```

Dash invokes the callback when `example-choice.value` changes. In this
application, `ui.register_history_callback("users")` registers the callbacks
that read a validated activity snapshot and return Plotly figures;
`projects_ui.register_projects_callback()` provides the corresponding Projects
behavior. These callbacks run separately from the collection pipeline.

**Figures** use `plotly.graph_objects.Figure` to represent traces, axes, hover
content, and layout metadata. A `dcc.Graph` displays a figure and exposes
interaction events. The dashboard adds JavaScript for shared time-range zoom
and automatic Y-axis adjustment, where browser-side Plotly events are more
appropriate than additional server callbacks.

## Dash in this repository

- Root `app.py` starts the development application. Production Gunicorn serves
  the Flask WSGI `server` exported by that launcher.
- `zooniverse_dashboard.app.create_app()` configures Dash Pages, the global
  header, navigation, refresh controls, progress polling, and `/healthz`.
- `pages/` contains routes registered with `register_page()`; Dash's
  `page_container` displays the selected layout.
- `page_specs.py` declares the cards, windows, aggregation intervals, chart
  styles, defaults, and permitted overlays for the four metric pages.
  `page_components.py` renders their shared structure.
- `ui.py` generates the Users, Classifications, and Talk activity figures.
  `projects_ui.py` generates monthly/yearly approved-launch figures for the
  [Projects page](projects.md). Their aggregation and axis helpers are in
  `chart_data.py`, `chart_geometry.py`, and `figure_layout.py`.
- `pages/anomalies.py` provides a separate inspection layout and callbacks,
  outside the metric-page specification.
- `assets/styles.css` and `assets/*.js` load automatically.
  `chart_preferences.js` implements Dash clientside callbacks for shared chart
  controls; `00_history_runtime.js` attaches the zoom/autoscale listeners to
  Plotly graphs created during navigation. `episode_pointer.js` handles
  anomaly links separately.

## Interaction flow

Opening `/users` invokes `pages/users.py`'s layout, which calls
`ui.layout_for("users")`. That loads `users.json`, calculates the summary cards,
and passes the results and `PAGE_SPECS["users"]` to
`page_components.build_metric_page()`. The registered graph callback constructs
`ui.history_figure()` from the same snapshot and selected controls. Changing
an aggregation interval updates the figure without querying Panoptes.

The `/projects` route follows the analogous path through
`projects_ui.projects_layout()` and `projects_ui.projects_figure()`, using the
sparse `project_launches.json` snapshot. It offers only monthly and yearly
aggregation and does not draw a second launch-marker layer. Activity charts
can display those launches as dated overlays; see [Projects](projects.md).

Plotly emits a relayout event when the horizontal range changes. The browser
assets translate that range to absolute UTC dates and store it in the tab's
`sessionStorage`. `layout.meta.history_zoom` provides the period boundaries
needed to translate between date axes and equal-width numeric calendar slots.
The saved viewport carries across activity and Projects pages; each chart
scales its Y-axis independently. The common ``RadioItems`` IDs use Dash
session persistence for binning and chart type. ``dcc.Store`` and clientside
callbacks synchronize the selected properties when a new metric page appears,
so controls do not depend on Plotly's rendering lifecycle. A Projects page
cannot display Day or Week: it uses Month without overwriting those saved
activity-page preferences. Choosing Year on Projects updates the shared choice.

Refreshing is a separate path: the global refresh callback admits a detached
Python worker and polls saved progress. Remote collection and anomaly fitting
do not run in HTTP callbacks.

## Integration details

- **Page registration:** `register_page()` executes when a route module is
  imported. Registration and callback imports need to avoid cycles.
- **Stable component IDs:** callbacks, JavaScript selectors, links, and graph
  metadata depend on specific IDs; changes may span Python and browser assets.
- **Browser state:** Dash components and `dcc.Store` carry reactive state;
  `sessionStorage` holds the shared UTC viewport, while Dash session
  persistence and `dcc.Store` retain chart preferences. None contains source
  records or credentials.
- **Optional layers:** `PageSpec.has_layer()` governs eligibility; the figure
  builder still supplies validated data and the actual trace.
- **Page navigation:** Dash replaces graph elements when routes change. The
  shared JavaScript lifecycle attaches listeners to newly created graphs
  without duplicating them.

For application-specific changes, see [Adding or changing pages](extending_pages.md).
