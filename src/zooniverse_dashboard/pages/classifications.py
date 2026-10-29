"""Expose the ERAS classifications chart through the Dash Pages router.

The ``/classifications`` route delegates layout generation and chart callbacks
to :mod:`zooniverse_dashboard.ui`. Both use validated local aggregate JSON,
without querying ERAS in response to page navigation.
"""

from typing import Any

from dash import register_page
from dash.development.base_component import Component

from zooniverse_dashboard.ui import layout_for, register_history_callback

register_page(__name__, path="/classifications", name="Classifications", order=2)


def layout(**_kwargs: Any) -> Component:
    """Render the locally saved Classifications activity page.

    Dash Pages may pass route keywords; this layout does not interpret them.
    The underlying page reads only previously collected aggregate snapshots.

    Parameters
    ----------
    **_kwargs : typing.Any
        Unused Dash Pages route keyword arguments.

    Returns
    -------
    dash.development.base_component.Component
        Snapshot-driven ``classifications`` page content or a missing-snapshot notice.

    See Also
    --------
    :func:`zooniverse_dashboard.ui.layout_for`
        Build the shared layout for this route.
    """
    return layout_for("classifications")


register_history_callback("classifications")
