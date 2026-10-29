"""Read-only, snapshot-backed Zooniverse platform analytics.

The package collects aggregate registration, classification, and Talk counts,
plus approved project-launch dates, using explicit operator-triggered refresh
commands. It persists validated JSON snapshots and computes anomaly episodes
from those aggregates alone. Page rendering reads local snapshots rather than
reaching Panoptes or ERAS on navigation, and individual volunteer data are
outside the data model.

Importing the package root has no side effects; it neither creates the Dash
application nor opens remote connections. Application creation lives in
:mod:`zooniverse_dashboard.app`, while the storage and collection boundaries
are documented in :mod:`zooniverse_dashboard.snapshots`,
:mod:`zooniverse_dashboard.project_launches`, and
:mod:`zooniverse_dashboard.refresh`.
"""
