"""Display validated offline anomaly episodes and source-detail links.

This Dash Page reads the existing anomaly catalog through
:func:`zooniverse_dashboard.anomalies.catalog.visible_events`; no statistical
model fitting or remote data collection occurs when visiting the route.
Episodes are statistical inspection hints rather than confirmed incidents.
The route query string may contain an ``event`` ID; only IDs matching the
catalog's expected format are accepted, and a selected episode is shown first.
Links back to source graphs use ``episode`` query parameters to focus their
initial viewport.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode

from dash import Input, Output, callback, dcc, html, register_page
from dash.development.base_component import Component

from zooniverse_dashboard.anomalies.catalog import ID_PATTERN, visible_events

register_page(__name__, path="/anomalies", name="Anomalies", order=5)

LABELS: dict[str, str] = {
    "users": "User registrations",
    "classifications": "Classifications",
    "talks": "Talk comments",
}


def layout(**_kwargs: Any) -> Component:
    """Build the Anomalies page header and episode-list placeholder.

    The page explicitly cautions that statistical outliers do not establish the
    cause of a spike or drop. Content is populated by :func:`render_anomalies`.

    Parameters
    ----------
    **_kwargs : typing.Any
        Unused route values supplied by Dash Pages.

    Returns
    -------
    dash.development.base_component.Component
        Layout with explanatory text, route state and episode-list container.
    """
    return html.Div(
        [
            html.Div("PLATFORM METRICS · INSPECTION", className="kicker"),
            html.H1("Anomaly episodes"),
            html.P(
                "Statistically unusual aggregate activity, not confirmed attacks or outages. "
                "Episodes group adjacent unusual days. Clicking Show on graph returns to "
                "the source chart with the period in view.",
                className="lead",
            ),
            html.P(
                "Caution: calendar-filled zero counts can also reflect missing reporting "
                "buckets. The dashboard cannot verify the real-world cause of a dip.",
                className="muted",
            ),
            html.P(
                "The current inspection covers at most 730 scored days per source. "
                "Older history remains available in the source charts.",
                className="muted",
            ),
            dcc.Location(id="anomalies-route", refresh=False),
            html.Div(id="anomalies-list"),
        ]
    )


def episode_card(event: dict[str, Any], *, selected: bool = False) -> Component:
    """Render one offline anomaly episode with a source-graph link.

    The card compares observed counts against model expectations, formats NB2
    and empirical rarity values, and exposes optional cross-source context.
    Clicking ``Show on graph`` opens the originating metric chart with its
    anomaly date range in view.

    Parameters
    ----------
    event : dict[str, typing.Any]
        Validated catalog episode containing source, direction, dates, counts,
        scores, algorithms, context and a stable event identifier.
    selected : bool, optional
        Whether to emphasize this episode after direct URL navigation.

    Returns
    -------
    dash.development.base_component.Component
        Article including a deep link to the appropriate source chart.
    """
    name = LABELS[event["source"]]
    period = (
        event["start"]
        if event["start"] == event["end"]
        else (event["start"] + " – " + event["end"])
    )
    expectation = event["expected"]
    ratio = event["observed"] / expectation if expectation else None
    detail = f"Observed {event['observed']:,} vs expected {expectation:,.0f} " + (
        f"({ratio:.2f}× expected)" if ratio is not None else ""
    )
    scores: list[str] = [
        f"NB2 two-sided p ≈ {event['raw_p']:.3g}",
        "Historical rarity ≈ "
        + (f"{event['empirical_p']:.3g}" if event["empirical_p"] is not None else "n/a"),
    ]
    graph_url = f"/{event['source']}?{urlencode({'episode': event['id']})}"
    return html.Article(
        [
            html.Div(
                [
                    html.H3(f"{name} · {event['direction'].title()} · {period}"),
                    html.Span(
                        "Selected episode" if selected else f"Detected {event['detected']}",
                        className="episode-meta",
                    ),
                ],
                className="episode-header",
            ),
            html.P(detail, className="episode-count"),
            html.P("Flagged by: " + ", ".join(event["algorithms"]), className="episode-algorithms"),
            html.P(" · ".join(scores), className="muted"),
            *(html.P(context, className="episode-context") for context in event.get("context", [])),
            dcc.Link("Show on graph", href=graph_url, className="episode-graph-link"),
        ],
        className="episode-card" + (" episode-selected" if selected else ""),
        id=event["id"],
    )


@callback(Output("anomalies-list", "children"), Input("anomalies-route", "search"))
def render_anomalies(search: str | None) -> list[Component]:
    """Load eligible episodes and render the anomaly inspection list.

    The optional ``event`` URL query value is accepted only if it matches
    ``ID_PATTERN``. A matching episode is moved to the top; stale source analyses
    are rendered as notices. If no events remain, display instructions for an
    offline rebuild rather than performing statistical fitting inside Dash.

    Parameters
    ----------
    search : str or None
        URL query string containing the optional selected ``event`` ID.

    Returns
    -------
    list[dash.development.base_component.Component]
        Status notices, event count, episode cards, and optional empty state.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.visible_events`
        Filter saved episodes against source snapshot revisions.
    """
    event_id = parse_qs((search or "").lstrip("?")).get("event", [None])[0]
    if not isinstance(event_id, str) or not ID_PATTERN.fullmatch(event_id):
        event_id = None
    events, statuses = visible_events()
    selected = next((e for e in events if e["id"] == event_id), None)
    if selected:
        events = [selected] + [e for e in events if e["id"] != event_id]
    notices: list[Component] = [
        html.Div(f"{LABELS[source]}: {status}", className="notice notice-warning")
        for source, status in statuses.items()
        if status != "ready"
    ]
    return [
        *notices,
        html.P(
            f"{len(events)} episodes in the latest available offline analysis.", className="muted"
        ),
        *[episode_card(e, selected=e["id"] == event_id) for e in events],
        *(
            [
                html.Div(
                    "No anomaly episodes to display. Compute the offline anomaly catalog "
                    "after collecting aggregate snapshots: python -m "
                    "zooniverse_dashboard.anomalies",
                    className="notice",
                )
            ]
            if not events
            else []
        ),
    ]
