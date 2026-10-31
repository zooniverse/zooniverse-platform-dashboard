"""Declare the existing chart-page variants without coupling them to Dash.

The activity pages and the Projects page share a presentation skeleton, but
not their summary periods, supported chart windows or data layers. This module
captures those choices as immutable specifications consumed by the shared
:func:`zooniverse_dashboard.page_components.build_metric_page` renderer.
The activity and Projects figure builders also consult each page's layer
policy. Snapshot loading and metric calculations remain in the source adapters;
this module performs no I/O, Dash rendering or callback registration.

See Also
--------
:func:`zooniverse_dashboard.ui.layout_for`
    Activity-page adapter to the shared renderer.
:func:`zooniverse_dashboard.projects_ui.projects_layout`
    Projects-page adapter to the shared renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

SnapshotKind = Literal["activity", "launches"]
LayerKey = Literal["anomalies", "project_launches", "trailing_average"]


@dataclass(frozen=True)
class Choice:
    """One displayed control option with a stable underlying value.

    Parameters
    ----------
    value : str
        Value passed to the existing Dash callback.
    label : str
        User-visible label.
    """

    value: str
    label: str


@dataclass(frozen=True)
class CardSpec:
    """Identity and display label for a summary metric.

    Parameters
    ----------
    key : str
        Stable semantic identifier, not a source-specific calculation.
    label : str
        Existing label shown on the corresponding KPI card.
    """

    key: str
    label: str


@dataclass(frozen=True)
class LayerSpec:
    """Conditions under which a supplementary chart trace appears.

    The layer declares *when* it can be displayed, not how it obtains data.
    A missing optional project-launch snapshot still results in no markers.

    Parameters
    ----------
    key : LayerKey
        One of anomalies, project launches or trailing average.
    intervals : tuple[str, ...]
        Supported aggregation intervals for this layer.
    plot_types : tuple[str, ...]
        Supported chart types for this layer.
    """

    key: LayerKey
    intervals: tuple[str, ...]
    plot_types: tuple[str, ...]


@dataclass(frozen=True)
class PageSpec:
    """Declarative UI contract for one snapshot-backed chart page.

    This does not merge continuous activity snapshots with sparse launch data.
    The ``snapshot_kind`` field keeps their distinct validation and coverage
    guarantees explicit, while :attr:`layers` describes optional figure traces.

    Parameters
    ----------
    key : str
        Page route/source identifier, also used in Dash component IDs.
    snapshot_kind : SnapshotKind
        ``activity`` for continuous daily counts or ``launches`` for sparse
        approved project launch dates.
    title, chart_title, provider, count_noun : str
        Existing heading, chart heading, data-provider label and Y-axis noun.
    cards : tuple[CardSpec, ...]
        Ordered summary cards; calculation remains with the source provider.
    windows, intervals, plot_types : tuple[Choice, ...]
        Supported control selections and labels in their displayed order.
    layers : tuple[LayerSpec, ...]
        Optional traces and their applicable chart configurations.
    default_window, default_interval, default_plot_type : str
        Existing initially selected control values.

    Raises
    ------
    ValueError
        If a default is unavailable, options are duplicated, a layer refers
        to an unsupported mode, or an identifier is duplicated.
    """

    key: str
    snapshot_kind: SnapshotKind
    title: str
    chart_title: str
    provider: str
    count_noun: str
    cards: tuple[CardSpec, ...]
    windows: tuple[Choice, ...]
    intervals: tuple[Choice, ...]
    plot_types: tuple[Choice, ...]
    layers: tuple[LayerSpec, ...]
    default_window: str
    default_interval: str
    default_plot_type: str

    def __post_init__(self) -> None:
        """Validate the page contract without initializing Dash.

        Raises
        ------
        ValueError
            If any supported option, default, card or layer is inconsistent.
        """
        if not self.key or self.snapshot_kind not in ("activity", "launches"):
            raise ValueError("Invalid page identity or snapshot kind")
        for choices, default in (
            (self.windows, self.default_window),
            (self.intervals, self.default_interval),
            (self.plot_types, self.default_plot_type),
        ):
            values = [choice.value for choice in choices]
            if not values or len(set(values)) != len(values) or default not in values:
                raise ValueError("Page control choices must be unique and include the default")
        if not self.cards or len({card.key for card in self.cards}) != len(self.cards):
            raise ValueError("Page cards must have unique identifiers")
        if len({layer.key for layer in self.layers}) != len(self.layers):
            raise ValueError("Page layers must have unique identifiers")
        intervals = {option.value for option in self.intervals}
        plots = {option.value for option in self.plot_types}
        for layer in self.layers:
            if not layer.intervals or not layer.plot_types:
                raise ValueError("Layer must have at least one interval and chart type")
            if not set(layer.intervals) <= intervals or not set(layer.plot_types) <= plots:
                raise ValueError("Layer uses an unsupported interval or chart type")

    def has_layer(self, key: LayerKey, *, interval: str, plot_type: str) -> bool:
        """Check whether the page permits one optional layer in this view.

        Parameters
        ----------
        key : LayerKey
            Requested supplemental trace.
        interval, plot_type : str
            Current aggregation and figure style.

        Returns
        -------
        bool
            Whether the layer belongs in this page configuration. A ``True``
            result does not imply that its optional data is available.
        """
        return any(
            layer.key == key and interval in layer.intervals and plot_type in layer.plot_types
            for layer in self.layers
        )


ACTIVITY_CARDS: tuple[CardSpec, ...] = (
    CardSpec("yesterday", "Yesterday"),
    CardSpec("week", "Current week"),
    CardSpec("previous-week", "Previous week"),
    CardSpec("month", "Current month"),
    CardSpec("previous-month", "Previous month"),
    CardSpec("year", "Current year"),
    CardSpec("previous-year", "Previous year"),
)
PROJECT_CARDS: tuple[CardSpec, ...] = (
    CardSpec("recorded", "Recorded launches"),
    CardSpec("month", "Current month"),
    CardSpec("previous-month", "Previous month"),
    CardSpec("year", "Current year"),
    CardSpec("previous-year", "Previous year"),
)
ACTIVITY_WINDOWS: tuple[Choice, ...] = tuple(
    Choice(str(days), f"Last {days} days") for days in (7, 28, 90, 180, 365)
) + (
    Choice("all", "All available"),
)
PROJECT_WINDOWS: tuple[Choice, ...] = (
    Choice("12", "Last 12 months"),
    Choice("36", "Last 3 years"),
    Choice("60", "Last 5 years"),
    Choice("all", "All available"),
)
ACTIVITY_INTERVALS: tuple[Choice, ...] = (
    Choice("daily", "Day"),
    Choice("weekly", "Week"),
    Choice("monthly", "Month"),
    Choice("yearly", "Year"),
)
PROJECT_INTERVALS: tuple[Choice, ...] = (
    Choice("monthly", "Month"),
    Choice("yearly", "Year"),
)
PLOT_TYPES: tuple[Choice, ...] = (Choice("line", "Line"), Choice("bars", "Bars"))
ACTIVITY_LAYERS: tuple[LayerSpec, ...] = (
    LayerSpec("anomalies", ("daily",), ("line", "bars")),
    LayerSpec(
        "project_launches",
        tuple(item.value for item in ACTIVITY_INTERVALS),
        ("line", "bars"),
    ),
    LayerSpec("trailing_average", ("daily",), ("line",)),
)


def _activity_spec(key: str, title: str, chart_title: str, noun: str, provider: str) -> PageSpec:
    """Create the same control contract for one existing activity metric.

    Parameters
    ----------
    key, title, chart_title, noun, provider : str
        Page identity and provider-specific labels.

    Returns
    -------
    PageSpec
        Shared activity-card, control and layer policy.
    """
    return PageSpec(
        key=key,
        snapshot_kind="activity",
        title=title,
        chart_title=chart_title,
        provider=provider,
        count_noun=noun,
        cards=ACTIVITY_CARDS,
        windows=ACTIVITY_WINDOWS,
        intervals=ACTIVITY_INTERVALS,
        plot_types=PLOT_TYPES,
        layers=ACTIVITY_LAYERS,
        default_window="90",
        default_interval="daily",
        default_plot_type="line",
    )


PAGE_SPECS: Mapping[str, PageSpec] = MappingProxyType(
    {
        "users": _activity_spec(
            "users", "User registrations", "Registration history", "registrations", "Panoptes"
        ),
        "classifications": _activity_spec(
            "classifications",
            "Classifications",
            "Classification history",
            "classifications",
            "ERAS",
        ),
        "talks": _activity_spec(
            "talks", "Talk activity", "Talk comment history", "comments", "ERAS"
        ),
        "projects": PageSpec(
            key="projects",
            snapshot_kind="launches",
            title="Projects",
            chart_title="Project launch history",
            provider="Panoptes",
            count_noun="projects",
            cards=PROJECT_CARDS,
            windows=PROJECT_WINDOWS,
            intervals=PROJECT_INTERVALS,
            plot_types=PLOT_TYPES,
            layers=(),
            default_window="36",
            default_interval="monthly",
            default_plot_type="bars",
        ),
    }
)
