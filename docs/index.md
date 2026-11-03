# Zooniverse Platform Dashboard

**Understand how the Zooniverse platform is being used, without examining individual volunteers.**

The Zooniverse Platform Dashboard is an internal, lightweight application for
exploring platform-wide activity over time. The dashboard includes:

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
The [Architecture](architecture.md) and [Anomaly model](anomaly_detection.md)
explain data semantics; [Projects](projects.md) covers launch dates, public
titles, and the month/year charts.

**Maintenance and implementation:** [Maintainer handbook](maintainers.md),
[Quick look at Dash](dash_development.md), and
[Development conventions](development_practices.md) describe the module
boundaries, application framework, and verification contracts.

**Extending the application:** [Adding or changing pages](extending_pages.md),
[Adding an aggregate data source](extending_data_sources.md), and
[Extending anomaly identification](extending_anomalies.md) identify the
relevant modules, interfaces, and validation requirements.

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
