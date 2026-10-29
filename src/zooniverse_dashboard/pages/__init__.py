"""Register Dash Pages for the dashboard's snapshot-backed user interface.

Dash discovers modules in this package via the ``pages_folder`` configured by
:func:`zooniverse_dashboard.app.create_app`. Individual page modules register
routes at import time, but only load local JSON snapshots when constructing
pages or rendering chart callbacks. Page navigation does not query remote
Panoptes or ERAS services.
"""
