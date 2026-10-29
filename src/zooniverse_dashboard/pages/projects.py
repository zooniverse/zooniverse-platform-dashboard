"""Expose monthly and yearly project launches through the Dash Pages router.

The ``/projects`` route delegates snapshot-backed page layout and chart
callbacks to :mod:`zooniverse_dashboard.projects_ui`. The existing launch
snapshot is reused rather than re-querying Panoptes on navigation.
"""

from typing import Any

from dash import register_page
from dash.development.base_component import Component

from zooniverse_dashboard.projects_ui import projects_layout, register_projects_callback

register_page(__name__, path="/projects", name="Projects", order=4)


def layout(**_kwargs: Any) -> Component:
    """Render the approved Projects launch page.

    Dash Pages may pass route keywords; this layout does not interpret them.
    The underlying page reads only previously collected aggregate snapshots.

    Parameters
    ----------
    **_kwargs : typing.Any
        Unused Dash Pages route keyword arguments.

    Returns
    -------
    dash.development.base_component.Component
        Snapshot-driven ``projects`` page content or a missing-snapshot notice.

    See Also
    --------
    :func:`zooniverse_dashboard.projects_ui.projects_layout`
        Build the shared layout for this route.
    """
    return projects_layout()


register_projects_callback()
