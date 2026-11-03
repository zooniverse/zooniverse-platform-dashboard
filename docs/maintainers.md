# Maintainer handbook

The Zooniverse Platform Dashboard presents aggregate registration, classification,
Talk, and project-launch activity. It uses a single Dash application, saved JSON
snapshots, and an offline anomaly catalog. This guide describes the relationships
between those components and the constraints that matter when changing them.

## Application structure

The dashboard has four metric pages—**Users**, **Classifications**, **Talk**, and
**Projects**—plus an **Anomalies** inspection page. The first three display
continuous daily activity counts; Projects displays approved launches grouped
by month or year. The Anomalies page reports statistical episodes computed from
the three activity streams, not project launches.

The [Architecture](architecture.md) guide describes the data flow, the
[Projects](projects.md) guide documents launch semantics, and
[Anomaly detection](anomaly_detection.md) covers the statistical model.
[Quick look at Dash](dash_development.md) explains the presentation framework;
[Operations](operations.md) covers refresh, deployment, and recovery.

## Data flow

1. An operator triggers **Refresh data** or runs `refresh-data`. A browser
   request starts a detached worker via `refresh_job.py`; the CLI runs the
   collection pipeline directly.
2. `refresh.py` serializes collection with a POSIX lock and applies the
   60-minute cooldown. `sources.py` retrieves daily Panoptes registration
   counts; `eras.py` retrieves Classifications and Talk aggregates; and
   `project_launches.py` retrieves approved launch counts and public titles.
3. `snapshots.py` and `project_launches.py` validate their respective JSON
   schemas. `storage.py` publishes files atomically. A failed source does not
   replace that source's last valid snapshot or prevent other sources from
   completing.
4. `pipeline.py` invokes the offline NB2 analysis in `anomalies/compute.py`
   and publishes a revision-aware catalog through `anomalies/catalog.py`.
5. `app.py` constructs the Dash shell; `pages/` registers routes.
   `page_specs.py` declares card, control, and optional-layer choices, while
   `page_components.py` renders common layouts. `ui.py` and `projects_ui.py`
   construct the source-specific figures and callbacks.
6. JavaScript and CSS in `assets/` handle graph zoom, autoscaling, episode
   interactions, and styling. Page rendering reads saved snapshots only.

The dependency direction is **remote aggregates → validated snapshots → offline
analysis → dashboard**. Neither page navigation nor Plotly interactions perform
upstream collection or model fitting.

## Module map

| Location | Responsibility |
|---|---|
| `config.py`, `collection_config.py` | Runtime settings, storage path, history window |
| `sources.py`, `eras.py`, `project_launches.py` | Source-specific aggregate collection and validation |
| `snapshots.py`, `storage.py` | Activity schema and atomic persistence |
| `refresh.py`, `refresh_job.py`, `pipeline.py` | Collection, worker status, offline analysis orchestration |
| `anomalies/compute.py`, `anomalies/catalog.py` | NB2 scoring, episode construction, saved catalog |
| `page_specs.py`, `page_components.py` | Declarative metric-page configuration and shared Dash layout |
| `periods.py`, `chart_data.py`, `projects_data.py` | Calendar summaries, coverage rules, grouping |
| `chart_geometry.py`, `figure_layout.py` | Shared Plotly axis geometry and layout |
| `ui.py`, `projects_ui.py`, `pages/` | Metric figures, callbacks, and Dash routes |
| `assets/` | Browser graph behavior and CSS |
| `tests/`, `docs/`, `scripts/docker_smoke.sh`, `deploy/` | Verification, documentation, Docker smoke test, Compose deployment |

Source docstrings supply the generated API reference. The guides describe the
cross-module contracts and operational behavior.

## Architectural constraints

- **Aggregate-only access.** Collectors retrieve approved counts and narrowly
  bounded public project titles. They do not retrieve volunteer identities,
  individual classifications, owner records, or private project titles. See
  [Privacy](privacy.md).
- **Read-only upstreams.** Data collection uses read-only Panoptes SQL or
  aggregate ERAS endpoints. Dash callbacks do not make upstream requests.
- **Canonical snapshots.** Each saved data type has one supported schema.
  Schema changes require a coordinated deployment and recollection, rather
  than old-format readers or implicit migrations.
- **Single-instance coordination.** Locks, cooldowns, worker state, and atomic
  writes rely on a shared POSIX filesystem. Multiple replicas would require
  redesigning coordination and persistence.
- **Explicit coverage.** A partial calendar period is not extrapolated; an
  uncovered period is unavailable rather than zero. Missing days in sparse
  project-launch data represent zero only within validated coverage.
- **Statistical interpretation.** Anomaly episodes indicate unusual behavior
  relative to a model, not an established cause or operational incident.

## Local verification and deployment

The [Installation](installation.md) guide covers environment setup and local
snapshot initialization. With saved snapshots present, `python app.py` serves
all dashboard pages without requiring upstream connections.

For presentation changes, verify all four metric pages and Anomalies, including
missing-snapshot states, chart aggregation and type controls, episode links,
and shared zoom. For data or analysis changes, use the synthetic tests and
then an authorized refresh and anomaly rebuild where applicable. The
[Development conventions](development_practices.md) guide maps changes to
relevant tests.

For a container release, `scripts/docker_smoke.sh` verifies startup, routes,
non-root execution, persistence, and restart. A deployment check additionally
covers authenticated access, upstream connectivity, refresh results, and
snapshot persistence. Pushing a version tag may trigger GHCR publication;
release coordination is described in [Operations](operations.md).

There is one authoritative deployed version rather than independently
supported local installations. For a snapshot-schema change, stop workers,
identify generated files to clear or replace, deploy, and recollect from
approved upstreams. Preserve `collection_config.json` unless the history
selection is changing. The [Installation](installation.md) guide documents
the reset procedure.

## Extension guides

- [Quick look at Dash](dash_development.md) — layout, callbacks, routing, and graph state
- [Development conventions](development_practices.md) — module contracts, docstrings, and verification
- [Adding or changing pages](extending_pages.md) — cards, controls, figures, and layers
- [Adding an aggregate data source](extending_data_sources.md) — collection, schemas, and refresh
- [Extending anomaly identification](extending_anomalies.md) — model and catalog integration
- [Operations](operations.md) — deployment, failure handling, and recovery
