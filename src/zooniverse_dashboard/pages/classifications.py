"""Classifications page, backed exclusively by local aggregate snapshots."""
from dash import register_page
from zooniverse_dashboard.ui import layout_for, register_history_callback

register_page(__name__, path="/classifications", name="Classifications", order=2)


def layout(**_kwargs):
    return layout_for("classifications")


register_history_callback("classifications")
