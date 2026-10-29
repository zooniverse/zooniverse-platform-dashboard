"""Expose ERAS Talk comment activity through the Dash Pages router.

The ``/talks`` route reuses the shared activity layout and chart callbacks in
:mod:`zooniverse_dashboard.ui`, reading local aggregate JSON rather than making
remote ERAS requests on navigation.
"""

from typing import Any

from dash import register_page
from dash.development.base_component import Component

from zooniverse_dashboard.ui import layout_for, register_history_callback

register_page(__name__, path="/talks", name="Talk activity", order=3)


def layout(**_kwargs: Any) -> Component:
    """Render the locally saved Talk comment history page.

    Dash Pages may pass route keywords; this layout does not interpret them.
    The underlying page reads only previously collected aggregate snapshots.

    Parameters
    ----------
    **_kwargs : typing.Any
        Unused Dash Pages route keyword arguments.

    Returns
    -------
    dash.development.base_component.Component
        Snapshot-driven ``talks`` page content or a missing-snapshot notice.

    See Also
    --------
    :func:`zooniverse_dashboard.ui.layout_for`
        Build the shared layout for this route.
    """
    return layout_for("talks")


register_history_callback("talks")
