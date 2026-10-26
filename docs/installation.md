# Local installation and configuration

This guide sets up the dashboard for local development and connects **only the
aggregate collector** to its upstream sources. The Dash pages themselves read
saved JSON snapshots; they do not make database or ERAS requests on each visit.

## Prerequisites

- Python **3.11 or newer** and Git; Python 3.12 is the Docker image's version.
- Access to the `zooniverse/zooniverse-platform-dashboard` GitHub repository.
- A **read-only** PostgreSQL credential and network access (including any
  required IP allowlist/VPN) to the Panoptes database, *only if you intend to
  collect registrations*. Ask the Panoptes database administrator for the
  authorized hostname, database, and role; do not invent or reuse write-access
  credentials.
- Outbound HTTPS access to public ERAS aggregate endpoints for the
  Classifications and Talk collectors.

A database connection is **not required to inspect already-generated
snapshots**. Without a working Panoptes connection the Users collector cannot
refresh; individual source failures preserve previously validated snapshots.

## 1. Obtain the source and install Python dependencies

```bash
# Use the access method approved for your Zooniverse GitHub account.
git clone https://github.com/zooniverse/zooniverse-platform-dashboard.git
cd zooniverse-platform-dashboard

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,docs,server,analysis]'
```

On Windows use `.venv\\Scripts\\activate` instead of `source`; the runtime uses
POSIX file locks, so local collection is designed for macOS/Linux or a Linux
container (Windows developers can use WSL2).

The extras separate concerns: `dev` for tests and linting, `docs` for Sphinx,
`server` for Gunicorn, and `analysis` for offline NB2 calculations. Dash's
Diskcache background callback dependencies are installed with the application's
normal requirements. There is no separate research package required.

## 2. Configure local settings

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` locally (never commit it):

```dotenv
# Replace the placeholder values with an authorized READ-ONLY connection.
DB_URL_PANOPTES=postgresql+psycopg://READ_ONLY_USERNAME:CHANGE_ME@HOST:5432/DATABASE
ANALYTICS_DATA_DIR=.local/data
ANALYTICS_HISTORY_DAYS=0
DASH_HOST=127.0.0.1
PORT=8050
DASH_DEBUG=false
```

| Setting | Purpose |
|---|---|
| `DB_URL_PANOPTES` | Server-side SQLAlchemy/PostgreSQL URL using a read-only Panoptes role; required for refreshing registrations |
| `ANALYTICS_DATA_DIR` | Persistent, writable directory for daily JSON snapshots, anomaly catalog, refresh status, locks and background-job state |
| `ANALYTICS_HISTORY_DAYS` | `0` or unset = all available history; positive integer = this many **completed UTC days** |
| `DASH_HOST`, `PORT` | Local interface and port (defaults `127.0.0.1:8050`) |
| `DASH_DEBUG` | `false` for normal operation; avoid exposing debug mode publicly |

The history setting can later be overridden using `set-date-limit`. That
command writes a persistent override (`collection_config.json`) to the data
directory; precedence is **saved override → environment → all-time default**.
Changing the setting does not itself run a data refresh.

### The two upstream data connections

**Panoptes registrations:** the collector aggregates `public.users.created_at`
into counts per UTC date using a read-only SQL transaction. It does not select
names, emails, tokens, IP addresses or other personal fields. The SQL source
is treated as UTC, although Panoptes' timezone interpretation still requires
confirmation for formal external reporting. If you use an SSH tunnel or VPN,
follow the database team's instructions rather than placing tunnel settings
in the repository.

**ERAS Classifications and Talk:** these use public HTTPS daily-count summaries
from `https://eras.zooniverse.org/classifications` and
`https://eras.zooniverse.org/comments`. There is **no second database URL** and
no volunteer-level classification or Talk download. All history within the
selected date limit is fetched and validated on each refresh.

## 3. Start the application and collect initial snapshots

```bash
python app.py
```

Open <http://127.0.0.1:8050/>. An empty-state view before the first successful
collection is expected. Use the **Refresh data** button to run the collection
and anomaly rebuild; the progress panel shows which operation is running.
The page updates automatically when the pipeline finishes.

Alternatively, from a second terminal with the same activated environment:

```bash
refresh-data
```

This CLI runs the same ingestion → anomaly-rebuild workflow. Its output goes
to the terminal and its status remains available to the UI. Refresh requests
are protected by a shared lock and a one-hour cooldown. The app does **not**
run scheduled collection jobs. For command examples and failure behavior see
[Command-line reference](command_line.md) and [Operations](operations.md).

With a short history limit the dashboard may display KPI charts but have too
little history for NB2 detection; anomaly analysis typically needs at least
911 daily buckets for training and calibration warm-up.

## 4. Optional: run with Docker Compose

```bash
# Edit .env with the same authorized read-only Panoptes connection.
docker compose -f deploy/compose.yml up --build -d
```

Open <http://127.0.0.1:8050/>. The Compose service mounts the named volume
`analytics_data` at `/data`, and overrides `ANALYTICS_DATA_DIR` accordingly.
The service is intended as **one replica** with a shared POSIX filesystem.

Run the CLI *inside* that container if you want to operate on its snapshots:

```bash
docker compose -f deploy/compose.yml exec dashboard set-date-limit
docker compose -f deploy/compose.yml exec dashboard refresh-data
docker compose -f deploy/compose.yml exec dashboard rebuild-anomalies
```

The container must not be exposed directly to the internet. For any shared
deployment, place an authenticated TLS ingress/SSO/VPN in front of the **entire**
dashboard, including its refresh control. See [Privacy](privacy.md) and
[Operations](operations.md) before deploying.

## 5. Developer checks

```bash
black --check app.py src tests
ruff check app.py src tests
pytest -q
sphinx-build -b html docs docs/_build/html
```

To read the documentation locally:

```bash
python -m webbrowser -t file://"$(pwd)"/docs/_build/html/index.html
```

Do not add live database tests, write credentials into fixtures, or assume
an ERAS zero-filled date proves zero activity.
