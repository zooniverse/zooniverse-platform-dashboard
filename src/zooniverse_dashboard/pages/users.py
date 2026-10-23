"""User registrations page, backed exclusively by local aggregate snapshots."""

from dash import register_page

from zooniverse_dashboard.ui import layout_for, register_history_callback

register_page(__name__, path="/users", name="User registrations", order=1)


def layout(**_kwargs):
    return layout_for("users")


register_history_callback("users")
