# Development conventions and verification

These conventions describe the application’s module boundaries, data
contracts, and verification strategy.

## Responsibility boundaries

Keep **I/O at the edges**: `sources.py`, `eras.py`, and `project_launches.py`
connect to remote systems only in explicit collection paths. Snapshot readers,
calendar aggregation, detector calculations and figure builders should remain
independently testable using in-memory values and temporary files.

Favor small functions with explicit contracts over a new abstraction merely to
reduce file length. `PageSpec` describes controls and allowed layers, while
source-specific adapters still calculate totals; `Figure` layout helpers own
geometry, while figure builders choose traces and meaning. Do not make the
data layer import Dash or UI specifications.

Treat configuration (`.env`, saved history limit), user-visible state
(`refresh_job.json`), canonical data (`users.json`, `project_launches.json`),
and derived analysis (`anomalies.json`) as different things. Document whether a
new file is authoritative input, generated data, or an optional display cache.

## Module, function and class documentation

- Start **every Python module** with a short but meaningful docstring: purpose,
  side effects (especially database/network/Dash registration), assumptions,
  and major collaborating modules.
- Use **NumPy-style docstrings** for public and nontrivial internal classes and
  functions. Include `Parameters`, `Returns`/`Yields`, `Raises`, `Attributes`,
  `Notes`, and `See Also` as applicable; avoid empty boilerplate sections.
- Add explicit parameter and return annotations, `dataclass`/`Protocol` field
  types, and types for important constants and intermediate values. Types
  communicate data contracts; do not annotate every trivial local solely to
  increase annotation density.
- Use Sphinx role references in docstrings for our own objects, such as
  ``:class:`zooniverse_dashboard.snapshots.Snapshot` `` and
  ``:func:`zooniverse_dashboard.snapshots.load_snapshot` ``. MyST narrative
  guides should link to other guide files using ordinary relative links.
- Explain *why* when behavior is surprising: UTC cutoff, partial coverage,
  privacy projections, immutable page specifications, per-source isolation,
  stale anomaly catalogs, and browser numeric-versus-date axes.
- Document actual possible errors and side effects; do not promise exceptions
  merely because a library dependency could theoretically raise them.

The Sphinx build uses `docs/conf.py`, MyST Markdown, AutoAPI static source
inspection, and Napoleon. The API inventory is generated from the package under
`src/`; root `app.py` and scripts need narrative coverage, because they are
not scanned by AutoAPI.

## Test the *contract*, not the implementation detail

| Type of change | Required checks |
|---|---|
| Source collector | Mock upstream responses; assert aggregate-only projections, time bounds, malformed/oversized responses and zero-fill semantics |
| Snapshot format/storage | Test round-trip, corrupt input, size/symlink checks, failed fsync/rename, old-file preservation and atomic replacement |
| Refresh coordinator/worker | Test cooldown, lock contention, partial source failure, interrupted worker and sanitized progress |
| Aggregation/Plotly | Test week/month/year boundaries, partial periods, each supported chart style and shared-zoom metadata |
| Anomaly algorithm | Test historical-only fitting, expected-score ranges, sensitivity, stability and catalog revision handling |
| Browser assets | Run the Node tests, then manually test page switching/zoom/hover in Dash |
| Docs | Warnings-as-errors Sphinx HTML build and navigation/link review |

Look first for an existing focused test or fixture. Add a new assertion to it
when appropriate; avoid duplicating the full 3-source × 4-interval × 2-chart
matrix just to test a common renderer. Keep source isolation and unusual
failure paths covered explicitly. Temporary directories and synthetic counts
are preferred to network access. Some Dash-dependent tests skip if Dash isn't
installed; **a partially skipped run is not a full release validation**.

Existing regression areas include `tests/test_snapshots.py`,
`test_project_launches.py`, `test_storage.py`, `test_refresh.py`,
`test_refresh_job.py`, `test_anomaly_integration.py`,
`test_page_characterization.py`, and the `test_history_*_js.py` tests.

## Review checklist for changes

1. Is the source data authorization and privacy scope unchanged or explicitly
   reviewed? Is the SQL projection bounded and read-only?
2. Do `first`, `last`, `as_of` and `history_days` still mean the same thing?
   Are empty/partial periods correctly distinguished from unknown coverage?
3. Can a failed source/model/write preserve other valid saved data?
4. Are browser callback IDs, event payloads, Plotly metadata and optional
   layers still consistent with the page specification?
5. Are the tests focused but sufficient, and do the Markdown guides and
   NumPy docstrings reflect the implementation?
6. Does deployment require recalculating JSON or restarting the worker?
   Record those steps explicitly rather than creating hidden migrations.

For code navigation and operations, see [Maintainer handbook](maintainers.md),
[Architecture](architecture.md), and [Operations](operations.md).
