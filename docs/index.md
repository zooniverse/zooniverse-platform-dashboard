# Zooniverse Platform Dashboard

**Understand how the Zooniverse platform is being used, without examining individual volunteers.**

The Zooniverse Platform Dashboard is an internal, lightweight application for
exploring platform-wide activity over time. At first glance, the dashboard
provides:

- **Users, Classifications, and Talk pages:** seven UTC-calendar KPI periods
  (yesterday; current/previous week, month, and year), a pannable history
  chart, and a seven-day moving average.
- **Projects page:** approved project launches by month or year, including
  zero-launch periods, current/previous calendar summaries, and shared UTC
  chart zoom. It reads the existing project-launch snapshot, not a new query.
- **Anomalies page:** a compact list of statistically unusual *episodes*,
  including the source, duration, observed versus expected activity, and
  evidence from the detector. Chart markers and episode cards link to one
  another, with a zoomed-in view of the corresponding dates.

## Where to go next

**Running the service:** [Installation](installation.md),
[Command-line reference](command_line.md), [Operations](operations.md), and
[Privacy](privacy.md) cover setup, refreshes, deployment and recovery.
The [Architecture](architecture.md), [Anomaly model](anomaly_detection.md),
and [Projects page](projects.md) explain data semantics in depth.

**New maintainer:** start with the [Maintainer handbook](maintainers.md),
then [Dash for Python developers](dash_development.md) and
[Development conventions](development_practices.md). These explain *why* the
modules are separated and how to make safe changes without assuming prior Dash
experience.

**Extending the application:** see [Adding a page](extending_pages.md),
[Adding a data source](extending_data_sources.md), and
[Extending anomaly identification](extending_anomalies.md). Each guide names
real integration points, tests, and privacy/reliability constraints.

```{toctree}
:maxdepth: 1
:caption: Install and operate

installation
command_line
operations
privacy
```

```{toctree}
:maxdepth: 1
:caption: Maintain and extend

maintainers
dash_development
development_practices
extending_pages
extending_data_sources
extending_anomalies
```

```{toctree}
:maxdepth: 1
:caption: Architecture and API

architecture
anomaly_detection
projects
api
```
