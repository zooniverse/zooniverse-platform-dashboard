"""Snapshot-backed anomaly episode browser; never fits a statistical model."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode

from dash import Input, Output, callback, dcc, html, register_page

from zooniverse_dashboard.anomalies.catalog import ID_PATTERN, visible_events

register_page(__name__, path="/anomalies", name="Anomalies", order=5)

LABELS = {
    "users": "User registrations",
    "classifications": "Classifications",
    "talks": "Talk comments",
}


def layout(**_kwargs):
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


def episode_card(event: dict, *, selected: bool = False):
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
    scores = [
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
def render_anomalies(search):
    event_id = parse_qs((search or "").lstrip("?")).get("event", [None])[0]
    if not isinstance(event_id, str) or not ID_PATTERN.fullmatch(event_id):
        event_id = None
    events, statuses = visible_events()
    selected = next((e for e in events if e["id"] == event_id), None)
    if selected:
        events = [selected] + [e for e in events if e["id"] != event_id]
    notices = [
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
