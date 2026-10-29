"""Expose Panoptes user registration history through the Dash Pages router.

The ``/users`` route uses the shared activity layout and chart callbacks from
:mod:`zooniverse_dashboard.ui`. Only locally persisted daily aggregate counts
are rendered; no individual user records are read by the page.
"""

from typing import Any

from dash import register_page
from dash.development.base_component import Component

from zooniverse_dashboard.ui import layout_for, register_history_callback

register_page(__name__, path="/users", name="User registrations", order=1)


def layout(**_kwargs: Any) -> Component:
    """Render the locally saved Users registration page.

    Dash Pages may pass route keywords; this layout does not interpret them.
    The underlying page reads only previously collected aggregate snapshots.

    Parameters
    ----------
    **_kwargs : typing.Any
        Unused Dash Pages route keyword arguments.

    Returns
    -------
    dash.development.base_component.Component
        Snapshot-driven ``users`` page content or a missing-snapshot notice.

    See Also
    --------
    :func:`zooniverse_dashboard.ui.layout_for`
        Build the shared layout for this route.
    """
    return layout_for("users")


register_history_callback("users")
