# Dash for experienced Python developers

[Dash](https://dash.plotly.com/) is a Python framework for interactive web
applications. It is built around **component trees** (the page layout) and
**reactive callbacks** (functions whose inputs and outputs are component
properties). Dash renders components in the browser and marshals callback
property values between the browser and Python. You do **not** need to write
frontend JavaScript for ordinary dropdowns, buttons, or plots.

This page is a short orientation, not a full framework tutorial. The relevant
official references are [Layout](https://dash.plotly.com/layout),
[Callbacks](https://dash.plotly.com/basic-callbacks),
[Interactive graphs](https://dash.plotly.com/interactive-graphing),
[Multi-page apps](https://dash.plotly.com/urls), and
[CSS/JavaScript assets](https://dash.plotly.com/external-resources).

## The three essential concepts

**Layout.** A Dash layout is a tree of Python component instances. `dash.html`
provides HTML primitives; `dash.dcc` supplies controls and `dcc.Graph`.
A component's **ID** is the address used by callbacks. In this project, the
shared page builder creates IDs from the page key (for example,
`users-interval` and `users-graph`). ID stability matters: renaming one may
break callbacks and browser JavaScript even when the page still renders.

**Callbacks.** A callback declares `Input`, `Output`, and optionally `State`
properties. When an input changes, Dash calls the function to compute output
properties. A tiny, illustrative callback looks like this:

```python
from dash import Input, Output, callback

@callback(Output("example-label", "children"), Input("example-choice", "value"))
def update_label(value: str) -> str:
    return f"Selected: {value}"
```

The example is **not** a project callback. Here the real page callback
`ui.register_history_callback("users")` reads a validated snapshot and
returns a Plotly figure when controls change; `projects_ui.register_projects_callback()`
handles Projects. Callback registration happens when Dash imports the page
modules. Callback functions can be run multiple times, so they must not mutate
shared module state or fetch large remote datasets on navigation.

**Plots.** `plotly.graph_objects.Figure` describes traces, axes, styling,
hover data and metadata. `dcc.Graph(figure=figure)` renders it. The browser
exposes plot interaction events; our date-range persistence and Y-autoscaling
are implemented with lightweight JavaScript because Plotly's grouped monthly
bars have numeric X positions while lines use UTC dates.

## Where Dash lives in *this* repository

- The development launcher is root `app.py`. Production Gunicorn serves the
  Flask WSGI `server` exported by the package's `app.py`.
- `zooniverse_dashboard.app.create_app()` creates `Dash(..., use_pages=True)`
  and sets `pages_folder` and `assets_folder`. It owns the global header,
  navigation, refresh button, progress stores/polling, and `/healthz` endpoint.
- Modules in `pages/` call `register_page(__name__, path=..., name=..., order=...)`.
  Dash Pages discovers them, and `page_container` places the selected page in
  the global layout. The registered route can expose a `layout()` function.
- `page_specs.py` declares the current four chart page variants:
  available cards, windows, bins, chart types, optional layers and defaults.
  `page_components.py` renders common structure from those specifications.
- `ui.py` owns activity-specific figures and callbacks; `projects_ui.py` owns
  the launch-rate figure/callback. `chart_data.py`, `chart_geometry.py`, and
  `figure_layout.py` perform calendar aggregation/axis calculations.
- `pages/anomalies.py` has its own page and callback: it is an episode-inspection
  view, not another metric chart driven by `PageSpec`.
- `assets/styles.css` and `assets/*.js` load automatically. `00_history_runtime.js`
  registers the common graph lifecycle; `history_zoom.js`,
  `history_autoscale.js`, and `episode_pointer.js` add browser behavior.

## Trace a concrete user interaction

When a user opens `/users`, Dash selects the page's `layout()` function. That
calls `ui.layout_for("users")`, which reads `users.json`, calculates KPI periods,
and hands them to `build_metric_page(PAGE_SPECS["users"], ...)`. The graph
callback constructs `history_figure()` from the saved snapshot and selected
controls. Changing **Month** to **Year** triggers a callback; it does *not*
call PostgreSQL.

When a user zooms, Plotly emits a relayout event. The browser assets translate
that range into absolute UTC timestamps and store it in **session storage** for
this browser tab. Another chart can restore that interval, even if it uses
numeric calendar slots. `layout.meta.history_zoom` describes the coverage and
period boundaries used for conversion. The Y-axis is recalculated per metric.
The global refresh button is different: it schedules a detached Python worker
and polls saved status; the long-running collection never executes inside an
HTTP callback.

## Common Dash pitfalls in this application

- **Registration occurs at import time.** A new page module needs a
  `register_page` call and correct callback registration. Avoid circular
  imports and side effects outside Dash setup.
- **IDs are public contracts.** A page key, `*-graph` class, callback input,
  link, or JavaScript selector may depend on the same ID or trace metadata.
- **State is per browser, not a Python global.** Use Dash properties and
  `dcc.Store` for browser state; `sessionStorage` is used only for the chart
  viewport, never for credentials or raw source data.
- **Optional layers still need data validation.** `PageSpec.has_layer(...)`
  permits a trace, but does not make a snapshot available or authorize a
  remote query. Missing optional launch markers must not break activity charts.
- **Dynamic routing has a lifecycle.** Navigation can replace graph DOM nodes.
  Browser listeners are attached idempotently by the shared JS runtime.
- **A Graph's truth value is not existence.** In tests use `is not None` when
  looking up childless Dash components and use `getattr(component, "children", None)`
  when walking a component tree.

To extend the dashboard, start with [Adding and changing pages](extending_pages.md).
For the framework itself, follow the linked official Dash manuals rather than
reproducing them here.
