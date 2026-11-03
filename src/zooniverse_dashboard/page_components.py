"""Render the shared presentation skeleton of snapshot-backed metric pages.

Activity and project-launch pages have different data semantics, but identical
Dash card, filter, and chart structures. :class:`~zooniverse_dashboard.page_specs.PageSpec`
controls the available cards, choices, defaults, and component IDs. Source
modules still own loading, aggregating, optional figure layers, and callbacks.
This module performs no I/O and registers no callbacks.

See Also
--------
:class:`zooniverse_dashboard.page_specs.PageSpec`
    Immutable description of a supported page.
:func:`zooniverse_dashboard.ui.layout_for`
    Activity snapshot adapter.
:func:`zooniverse_dashboard.projects_ui.projects_layout`
    Project launch snapshot adapter.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable, Protocol

from dash import dcc, html
from dash.development.base_component import Component

from zooniverse_dashboard.page_specs import Choice, PageSpec


class SummaryRow(Protocol):
    """The common display fields of a calendar or project summary metric.

    Attributes
    ----------
    label : str
        User-visible card heading.
    start, end : datetime.date
        Inclusive UTC interval, potentially empty for a new period.
    count : int or None
        Known total or ``None`` when history is insufficient.
    """

    label: str
    start: date
    end: date
    count: int | None


class PageSnapshot(Protocol):
    """Only the snapshot metadata needed by the common page renderer.

    Attributes
    ----------
    first, last, as_of : datetime.date
        Validated UTC coverage bounds and next-day boundary.
    last_modified : datetime.datetime
        Timestamp of the most recent published snapshot.
    history_days : int
        Required collection window in days; zero means all available history.
    """

    first: date
    last: date
    as_of: date
    last_modified: datetime
    history_days: int


def _choices(options: tuple[Choice, ...]) -> list[dict[str, str]]:
    """Convert immutable choices into Dash-compatible option dictionaries.

    Parameters
    ----------
    options : tuple[Choice, ...]
        Immutable page specification options in display order.

    Returns
    -------
    list[dict[str, str]]
        Dash mappings with ``value`` and ``label`` fields.
    """
    return [{"value": option.value, "label": option.label} for option in options]


def metric_cards(rows: Iterable[SummaryRow]) -> Component:
    """Render validated metric summaries as the existing seven/five-card grid.

    Parameters
    ----------
    rows : iterable of SummaryRow
        Ordered card values calculated by the corresponding data module.

    Returns
    -------
    dash.development.base_component.Component
        Section containing card titles, values, coverage, and warnings.
    """
    cards: list[Component] = []
    for row in rows:
        range_label = (
            f"{row.start.isoformat()} – {row.end.isoformat()} UTC"
            if row.start <= row.end
            else "No completed days"
        )
        cards.append(
            html.Div(
                [
                    html.Div(row.label, className="metric-title"),
                    html.Div(
                        "—" if row.count is None else f"{row.count:,}",
                        className="metric-value",
                    ),
                    html.Div(range_label, className="metric-period"),
                    html.Div(
                        "Insufficient historical coverage" if row.count is None else "",
                        className="metric-pending",
                    ),
                ],
                className="metric-card registration-metric",
            )
        )
    return html.Section(cards, className="registration-metrics")


def missing_snapshot_page(spec: PageSpec, message: str, *, include_provider: bool) -> Component:
    """Show the original missing-snapshot placeholder without loading remote data.

    Parameters
    ----------
    spec : PageSpec
        Page title and provider information.
    message : str
        Source-specific explanation of how to populate the snapshot.
    include_provider : bool
        Whether the original placeholder included an uppercase provider name.

    Returns
    -------
    dash.development.base_component.Component
        Missing-data notice, preserving the existing page shape.
    """
    kicker = "PLATFORM METRICS"
    if include_provider:
        kicker += f" · {spec.provider.upper()}"
    return html.Div(
        [
            html.Div(kicker, className="kicker"),
            html.H1(spec.title),
            html.Div(message, className="notice"),
        ]
    )


def chart_controls(spec: PageSpec) -> Component:
    """Render the configured window, aggregation and chart-type selectors.

    Window control IDs derive from ``spec.key``; the aggregation and chart-type
    radio IDs are shared across metric pages. Clientside callbacks restore
    their values from the app-level session-scoped ``dcc.Store``. The radios themselves have no
    persistence, preventing a second source of state. Unsupported intervals on
    Projects fall back to Month without changing the saved preference.

    Parameters
    ----------
    spec : PageSpec
        Validated page contract with displayed options and defaults.

    Returns
    -------
    dash.development.base_component.Component
        Controls in their existing order with stable Dash component IDs.
    """
    return html.Div(
        [
            html.Div(
                [
                    html.Label("Visible time window", htmlFor=f"{spec.key}-window"),
                    dcc.Dropdown(
                        id=f"{spec.key}-window",
                        options=_choices(spec.windows),
                        value=spec.default_window,
                        clearable=False,
                        searchable=False,
                        style={"minWidth": "220px"},
                    ),
                ],
                className="registration-date-control",
            ),
            dcc.Store(
                id="shared-chart-page",
                data={
                    "source": spec.key,
                    "intervals": [item.value for item in spec.intervals],
                    "plot_types": [item.value for item in spec.plot_types],
                    "default_interval": spec.default_interval,
                    "default_plot_type": spec.default_plot_type,
                },
            ),
            dcc.Store(id="shared-chart-ready"),
            html.Div(
                [
                    html.Label("Group by", htmlFor="shared-chart-interval"),
                    dcc.RadioItems(
                        id="shared-chart-interval",
                        options=_choices(spec.intervals),
                        value=spec.default_interval,
                        inline=True,
                        className="history-radio-group",
                    ),
                ],
                className="registration-date-control",
            ),
            html.Div(
                [
                    html.Label("Chart type", htmlFor="shared-chart-plot-type"),
                    dcc.RadioItems(
                        id="shared-chart-plot-type",
                        options=_choices(spec.plot_types),
                        value=spec.default_plot_type,
                        inline=True,
                        className="history-radio-group",
                    ),
                ],
                className="registration-date-control",
            ),
        ],
        className="registration-filters",
    )


def chart_section(spec: PageSpec, *, eyebrow: str, footnote: str) -> Component:
    """Render the common graph container and configurable controls.

    Parameters
    ----------
    spec : PageSpec
        Source ID, section heading, and permitted controls.
    eyebrow : str
        Small uppercase section label.
    footnote : str
        Source-specific explanation of layers, zoom, and completeness.

    Returns
    -------
    dash.development.base_component.Component
        Plotly graph scaffold; the source still supplies its figure callback.
    """
    return html.Section(
        [
            html.Div(eyebrow, className="section-label"),
            html.H2(spec.chart_title),
            chart_controls(spec),
            dcc.Loading(
                dcc.Graph(
                    id=f"{spec.key}-graph",
                    className="history-graph",
                    config={
                        "displaylogo": False,
                        "responsive": True,
                        "scrollZoom": False,
                        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                    },
                    style={"height": "500px"},
                ),
                type="circle",
            ),
            html.P(footnote, className="muted"),
        ],
        className="section-card",
    )


def build_metric_page(
    spec: PageSpec,
    snapshot: PageSnapshot,
    rows: Iterable[SummaryRow],
    *,
    configured_days: int | None,
    lead: str,
    eyebrow: str,
    footnote: str,
    mismatch_message: str,
    stale_prefix: str,
    stale_suffix: str,
    route: bool = False,
    uppercase_provider: bool = False,
) -> Component:
    """Build the common full page from a source-specific validated snapshot.

    Parameters
    ----------
    spec : PageSpec
        Identity, cards, options and default chart controls.
    snapshot : PageSnapshot
        Common coverage and freshness metadata, already validated by caller.
    rows : iterable of SummaryRow
        Source-computed metric summaries, in the page's configured order.
    configured_days : int or None
        Current history setting, ``None`` on invalid configuration.
    lead, eyebrow, footnote : str
        Descriptive text unique to this page's underlying data.
    mismatch_message : str
        The existing source-specific configuration warning.
    stale_prefix, stale_suffix : str
        Fragments surrounding the number of outdated completed UTC days.
    route : bool, optional
        Include a :class:`dash.dcc.Location` for activity anomaly navigation.
    uppercase_provider : bool, optional
        Preserve the Projects page's uppercase provider kicker.

    Returns
    -------
    dash.development.base_component.Component
        Fully configured Dash layout with no I/O or callback registration.
    """
    stale_days = max(0, (datetime.now(timezone.utc).date() - snapshot.as_of).days)
    if snapshot.history_days == 0:
        collected_range = "All available"
    else:
        collected_range = f"Last {snapshot.history_days} days"
    provider = spec.provider.upper() if uppercase_provider else spec.provider
    return html.Div(
        ([dcc.Location(id=f"{spec.key}-route", refresh=True)] if route else [])
        + [
            html.Div(f"PLATFORM METRICS · {provider}", className="kicker"),
            html.H1(spec.title),
            html.P(lead, className="lead"),
            html.Div(
                [
                    html.Strong("Snapshot last modified: "),
                    snapshot.last_modified.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    " · Available data: ",
                    snapshot.first.isoformat(),
                    " – ",
                    snapshot.last.isoformat(),
                    " · Collected range: ",
                    collected_range,
                ],
                className="notice",
            ),
            (
                html.Div(mismatch_message, className="notice notice-warning")
                if configured_days is None or snapshot.history_days != configured_days
                else None
            ),
            (
                html.Div(
                    f"{stale_prefix}{stale_days}{stale_suffix}",
                    className="notice notice-warning",
                )
                if stale_days
                else None
            ),
            metric_cards(rows),
            chart_section(spec, eyebrow=eyebrow, footnote=footnote),
        ]
    )
