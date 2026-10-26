# Zooniverse Platform Dashboard

**Understand how the Zooniverse platform is being used, without examining individual volunteers.**

The Zooniverse Platform Dashboard is an internal, lightweight application for
exploring platform-wide activity over time. It brings together three daily
aggregate series: new user registrations from Panoptes, classification activity
from the public ERAS service, and Talk comments from ERAS. They describe
*different kinds of activity*, not interchangeable counts of active people.

The immediate goal is practical visibility. Staff should be able to answer
questions such as: How many registrations happened yesterday? Has classification
volume changed since last month? Is a sudden decrease consistent with historical
activity? And what do the data actually support when a day looks unusual?

## What the dashboard provides

- **Users, Classifications, and Talk pages:** seven UTC-calendar KPI periods
  (yesterday; current/previous week, month, and year), a pannable history
  chart, and a seven-day moving average.
- **Anomalies page:** a compact list of statistically unusual *episodes*,
  including the source, duration, observed versus expected activity, and
  evidence from the detector. Chart markers and episode cards link to one
  another, with a zoomed-in view of the corresponding dates.
- **An explicit refresh workflow:** a user-initiated collection of aggregate
  counts followed by an offline anomaly-catalog rebuild, with progress shown
  in the interface. There is no automatic scheduled collection.
- **A small and auditable data boundary:** the UI reads validated JSON
  snapshots on a persistent volume. It does not query Panoptes or ERAS for
  every page view, and the anomaly detector does not need individual records.

## Purpose and direction

This dashboard is meant to become a dependable **platform-health and activity
inspection workspace** for the Zooniverse team: easy to operate, transparent
about data freshness, cautious about privacy, and useful when investigating
changes in aggregate behavior. It is deliberately *not* a complete business
intelligence platform, an individual-user analytics product, or an automated
security/incident monitoring service.

The current anomaly analysis is statistical context for quick inspection.
A simultaneous decrease across sources can motivate investigation into a shared
service or reporting issue; coincident increases are merely interesting
context. Neither proves the cause of an event. Incomplete upstream data can
also create apparent drops, so uncertainty must remain visible.

For now, accuracy, maintainability and a clear operator workflow matter more
than adding more charts or algorithms. New data sources or capabilities can
be introduced later without breaking the aggregate-only and independently
rebuildable design.

## Start here

New contributors should begin with **[Local installation](installation.md)**
and then the **[Command-line reference](command_line.md)**. Operators can read
**[Operations](operations.md)**; developers interested in implementation
choices can begin with **[Architecture](architecture.md)** and the
**[Anomaly detector](anomaly_detection.md)**. The **[Privacy](privacy.md)**
page explains what must never be queried or stored.

```{toctree}
:maxdepth: 1

installation
command_line
architecture
anomaly_detection
operations
privacy
publishing
api
```
