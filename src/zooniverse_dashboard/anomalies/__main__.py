"""Command-line entry point for a snapshot-only anomaly catalog rebuild.

Run ``python -m zooniverse_dashboard.anomalies`` or the installed
``rebuild-anomalies`` command to recompute the aggregate anomaly catalog from
existing validated snapshots. No Panoptes or ERAS fetch is performed and the
Dash server does not start. The command prints per-source status and episode
counts and uses the configured aggregate data directory.

See Also
--------
:func:`zooniverse_dashboard.anomalies.__main__.main`
    Execute and report the offline rebuild.
:func:`zooniverse_dashboard.anomalies.catalog.rebuild`
    Snapshot-only analysis and atomic catalog publication.
"""

from typing import Any

from zooniverse_dashboard.anomalies.catalog import rebuild


def main() -> None:
    """Build an offline catalog and print a per-source status report.

    Uses :func:`zooniverse_dashboard.anomalies.catalog.rebuild` with a progress
    callback that prints each safe status line, then prints the final source
    status, episode counts and catalog-publication confirmation. Failures are not
    suppressed: the CLI exits with an error if rebuilding or writing fails.

    Returns
    -------
    None
        Prints aggregate analysis progress and summary to standard output.

    Raises
    ------
    ValueError
        If publication fails input or destination validation.
    OSError
        If the catalog cannot be read or written due to filesystem errors.
    RuntimeError
        If a rebuild fails outside the handled per-source analysis errors.

    See Also
    --------
    :func:`zooniverse_dashboard.anomalies.catalog.rebuild`
        The underlying offline computation and catalog writer.
    """
    report: dict[str, Any] = rebuild(progress=lambda line: print(line, flush=True))
    for source, status in report["sources"].items():
        print(
            f"{source}: {status['status']} "
            f"{status.get('episode_count', 0)} episodes "
            f"{status.get('message', '')}"
        )
    print("Saved anomaly catalog to the configured aggregate data directory")


if __name__ == "__main__":
    main()
