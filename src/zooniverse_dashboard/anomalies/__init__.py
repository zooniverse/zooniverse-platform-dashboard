"""Expose a lightweight namespace for offline aggregate anomaly analysis.

The :mod:`zooniverse_dashboard.anomalies` package separates the expensive
numerical detector (:mod:`zooniverse_dashboard.anomalies.compute`) from the
small JSON catalog reader (:mod:`zooniverse_dashboard.anomalies.catalog`).
Ordinary dashboard requests import only the catalog; NumPy, SciPy and
statsmodels are loaded on an explicit catalog rebuild. This separation helps
keep request handling fast and avoids performing statistical work on page load.

See Also
--------
:func:`zooniverse_dashboard.anomalies.catalog.visible_events`
    Read the revision-filtered episode list for dashboard rendering.
:func:`zooniverse_dashboard.anomalies.catalog.rebuild`
    Compute and publish the catalog from existing daily snapshots.
"""
