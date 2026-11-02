# Maintainer handbook

This handbook is for an experienced Python developer inheriting the Zooniverse
Platform Dashboard. Familiarity with Dash, Plotly, or the project's history is
**not** assumed. The codebase is intentionally small enough to follow without a
separate service platform, but several boundaries protect production data and
must be understood before making changes.

## What you own

The dashboard offers four chart pages (Users, Classifications, Talk, Projects),
an Anomalies page, and an operator-triggered refresh. Its purpose is to display
**aggregate platform activity**, not track volunteers. A successful HTTP health
check does not imply that upstream data are fresh. No automatic collection or
incident-response workflow is configured.

**Read first:** [Architecture](architecture.md) explains the runtime boundaries;
[Dash for Python developers](dash_development.md) explains the web framework;
[Operations](operations.md) explains refresh, deployment and recovery; and
[Privacy](privacy.md) sets the nonnegotiable data-access rules.

## Mental model: follow one count through the application

1. An authorized operator clicks **Refresh data** or runs `refresh-data`.
2. `refresh_job.py` admits and runs a detached browser job; the CLI can run the
   pipeline directly. `refresh.py` obtains the shared POSIX lock, applies the
   60-minute cooldown and invokes the configured collectors.
3. `sources.py` fetches **daily registration counts** with read-only SQL;
   `eras.py` fetches Classifications/Talk aggregates over HTTPS; and
   `project_launches.py` fetches approved launches with public titles.
4. `snapshots.py` and `project_launches.py` validate their respective formats.
   `storage.py` atomically publishes files; each source may fail independently.
5. `pipeline.py` then requests an offline anomaly rebuild through
   `anomalies/catalog.py` and `anomalies/compute.py`. The catalog uses activity
   snapshots only; it never analyzes project titles or launch counts.
6. `app.py` constructs the global Dash shell. `pages/` registers routes;
   `page_specs.py` declares controls/cards/layers; `page_components.py` renders
   common structure; `ui.py` and `projects_ui.py` calculate Plotly figures.
7. Browser assets under `assets/` provide zoom persistence, axis autoscaling,
   episode navigation, and styling. Page navigation and chart interactions
   read *saved files*, not upstream services.

The key dependency rule is **remote data → validated snapshots → offline
analysis → display**. Do not move remote queries or model fitting into Dash
callbacks to make a new page easier to implement.

## Repository map

| Location | Responsibility and modification boundary |
|---|---|
| `src/zooniverse_dashboard/config.py`, `collection_config.py` | Runtime settings, data directory, date-limit override |
| `sources.py`, `eras.py`, `project_launches.py` | Source-specific, privacy-constrained retrieval and validation |
| `snapshots.py`, `storage.py` | Canonical activity snapshot schema and atomic publication |
| `refresh.py`, `refresh_job.py`, `pipeline.py` | Serialized collection, durable job progress and offline rebuild |
| `anomalies/compute.py`, `anomalies/catalog.py` | Statistical scores, episodes and revision-aware saved catalog |
| `page_specs.py`, `page_components.py` | Immutable page choices and shared Dash layout |
| `periods.py`, `chart_data.py`, `projects_data.py` | Coverage-aware calendar summaries and grouping |
| `chart_geometry.py`, `figure_layout.py` | Calendar-axis and common Plotly layout primitives |
| `ui.py`, `projects_ui.py`, `pages/` | Metric-specific figures, route registration and callbacks |
| `assets/` | JavaScript graph behaviors and CSS automatically served by Dash |
| `tests/`, `docs/`, `scripts/`, `deploy/` | Verification, handbooks, diagnostics, local Compose setup |

The API pages are generated from source docstrings, but they are not a substitute
for the design rationale and procedures in these guides.

## Nonnegotiable design decisions

- **No PII:** keep query projections restricted to aggregate counts and the
  currently public, bounded project titles. Never store or log identities,
  email addresses, IPs, individual classification rows, tokens or raw errors.
- **Read-only upstreams:** no SQL writes; no live requests from UI callbacks.
  Validate types, bounds, continuity, response size and source-specific coverage.
- **One canonical format per snapshot:** this app does not maintain old JSON
  readers or migration chains. Schema changes require an explicit regeneration
  and deployment procedure; avoid silently interpreting corrupted files.
- **Single instance / POSIX volume:** job locks, cooldown, atomic replacement,
  snapshot persistence and status depend on a shared locking-capable filesystem.
  Do not horizontally scale without redesigning coordination/storage.
- **Incomplete means unknown:** a partial calendar period is not a full
  period; an uncovered historical interval is not zero. Sparse project launch
  days can be interpreted as zero only inside validated launch coverage.
- **Statistical flags are not incidents:** NB2 forecasts and rarity rules help
  prioritize investigation; they cannot establish cause, attack, or outage.

## First day as maintainer

1. Read [Installation](installation.md) and work on macOS/Linux (or WSL2).
   Install `.[dev,docs,analysis,server]`. Use approved read-only credentials
   only if you need to refresh live aggregates.
2. Start `python app.py` against existing *local* snapshots. Missing snapshots
   produce the expected empty state. Inspect all four charts, anomaly links,
   refresh status, and shared zoom. Do not trigger a real refresh casually:
   it has a cooldown, upstream cost and network/privacy requirements.
3. Read [Adding and changing pages](extending_pages.md), [Adding a data
   source](extending_data_sources.md), or [Extending anomaly detection](extending_anomalies.md)
   before implementing a feature in those areas.
4. For a release, run `bash scripts/docker_smoke.sh <image>` after building it,
   then test an authorized collection, anomaly rebuild, persistence/restart, and
   actual browser behavior. Coordinate any GHCR publication with repository
   maintainers; pushing a release tag may publish a container image.

## Ownership and change control

There is **one authoritative deployed version**, not independently supported
client installations. This permits removing obsolete implementation/API paths,
but *not* skipping careful deployment transitions. If on-disk schema changes,
stop running workers, decide which generated files need to be archived or
cleared, deploy, and recollect from authorized upstreams. See the reset
procedure in [Installation](installation.md). Preserve the configuration
file unless the history selection itself is intentionally changing.

Make reviewable commits: a source or model change, its tests, its docs, and any
operator migration steps belong together. Keep credentials and snapshot files
out of Git. A new contributor should be able to answer **what changed, why it
is safe, how to test it, and how to recover** from the pull request alone.

## More detailed guides

- [Code conventions and verification](development_practices.md)
- [Dash essentials and this app's callback flow](dash_development.md)
- [Adding/changing pages, controls, layers and Plotly behavior](extending_pages.md)
- [Adding another source and snapshot](extending_data_sources.md)
- [Adding another anomaly criterion or model](extending_anomalies.md)
- [Operations, failure handling and deployment](operations.md)
- [Numerical detector rationale](anomaly_detection.md)
