"""Talk activity page, backed exclusively by local aggregate snapshots."""

from dash import register_page

from zooniverse_dashboard.ui import layout_for, register_history_callback

register_page(__name__, path="/talks", name="Talk activity", order=3)


def layout(**_kwargs):
    return layout_for("talks")


register_history_callback("talks")
