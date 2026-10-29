# Zooniverse Platform Dashboard

An internal, aggregate-only dashboard for understanding Zooniverse platform activity:
**registrations**, **classifications**, and **Talk comments**, alongside approved
**project launches**. Activity metrics have UTC calendar summaries and interactive
histories; Projects tracks launch counts per month and year. A separate **Anomalies** page
provides statistical episode summaries and links back to the relevant charts.

The dashboard reads validated daily-count JSON snapshots; it does not display
volunteer-level records or query the database during page rendering. Authorized
operators can refresh the source aggregates and rebuild anomaly results from the
web interface or command line.

## Documentation

**[Start with the documentation](docs/index.md)** for the project's goals,
architecture, limitations, and navigation to the full guides:

- [Install and configure locally](docs/installation.md) — checkout, Python
  environment, read-only Panoptes connection, ERAS access, and first run.
- [Command-line reference](docs/command_line.md) — `refresh-data`,
  `set-date-limit`, and `rebuild-anomalies`.
- [Projects page](docs/projects.md) — approved launch rates by month/year,
  snapshot coverage, and shared chart zoom.
- [Anomaly detector](docs/anomaly_detection.md) — model assumptions,
  thresholds, calibration, and episode interpretation.
- [Operations](docs/operations.md) and [privacy](docs/privacy.md).
- [Documentation publishing](docs/publishing.md) — GitHub Pages setup.

When enabled by the repository maintainers, the Sphinx site is published at
<https://zooniverse.github.io/zooniverse-platform-dashboard/>. Until then, the
Markdown pages above can be read directly in GitHub.

To build the documentation locally:

```bash
python -m pip install -e '.[docs]'
sphinx-build -b html docs docs/_build/html
```

## Quick local start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,docs,server,analysis]'
cp .env.example .env
# Edit .env: set DB_URL_PANOPTES to a READ-ONLY PostgreSQL connection URL.
python app.py
```

Open <http://127.0.0.1:8050/>. The [installation guide](docs/installation.md)
explains how to create the first snapshots, what connectivity is required, and
how to run the same application with Docker.

**Access control:** this is an internal dashboard. Restrict deployed access at
an authenticated TLS ingress; do not expose its refresh button publicly. Do
not commit `.env`, credentials, data snapshots, or operational logs.
