# Project launch markers (Users, Classifications, and Talk)

Panoptes `public.projects` was assessed on 2026-10-28 using the aggregate-only
`python scripts/probe_project_launches.py` diagnostic. The observed results were:

- 29,966 projects; 575 with `launch_approved IS TRUE`.
- **All 575 approved projects had `launch_date` populated**. No approved
  launches had a future date through the previous completed UTC day.
- 27 dated projects were not approved and are therefore excluded.
- 26 approved records had `launch_date` **before** `created_at`. These are
  historical data-quality caveats, not automatic evidence of bad dates;
  do not reinterpret `launch_date` as the project creation date.
- Observed approved launch dates span 2010-02-22 through 2026-10-27.

The implementation uses `launch_approved = TRUE` and `launch_date` as the best
available historical launch signal. **A marker means that an approved Panoptes
project has a recorded launch date**, not proof of the moment it became
publicly visible or evidence that its launch caused a registration spike.
Canceled/delisted/unapproved projects are excluded. Deleted project rows cannot
be reconstructed from this table. Consult the Panoptes maintainers before
asserting that old timestamps are all UTC.

Upstream references:

- <https://github.com/zooniverse/panoptes/blob/master/app/models/project.rb>
- <https://github.com/zooniverse/panoptes/blob/master/app/serializers/project_serializer.rb>

## Read-only collection

The source runs as the **fourth, independent refresh stage**, after Users,
Classifications and Talk. Unlike these three activity sources, it does **not**
enter `SOURCES` or the anomaly model. The query is a single grouped SQL statement
on Panoptes; the database connection is a new short-lived PostgreSQL read-only
transaction with a 20-second statement timeout and 250-ms lock timeout.

```sql
SELECT launch_date::date AS day,
       COUNT(*)::bigint AS launches,
       COALESCE(
         array_agg(LEFT(BTRIM(display_name), 160) ORDER BY display_name)
           FILTER (WHERE private IS FALSE AND display_name IS NOT NULL
                   AND BTRIM(display_name) <> ''),
         ARRAY[]::text[]
       ) AS public_names
FROM public.projects
WHERE launch_approved IS TRUE
  AND launch_date IS NOT NULL
  AND launch_date < :end_exclusive
GROUP BY launch_date::date
ORDER BY day;
```

When `ANALYTICS_HISTORY_DAYS` (or a `set-date-limit` persisted override) is
nonzero, the query also includes `launch_date >= :start_inclusive` with
parameterized, inclusive midnight UTC bounds. The upper bound is exclusive,
at midnight UTC after the last completed date. It sets `SET LOCAL TIME ZONE 'UTC'`
for consistent date grouping; Panoptes columns use `timestamp without time zone`,
so interpreting values as UTC remains a source-data assumption. A bounded
period with zero launches is valid. The daily count includes *all* approved
projects; names are returned only where `private IS FALSE`, including for old
projects that are no longer live. The Project model defines `title` as an alias
for the `display_name` column. The grouped query returns **one row per launch
date**, not one row per project. No project IDs, owners, private titles, or
individual timestamps leave PostgreSQL. Public names are user-authored text:
they are not logged or sent to anomaly analysis, and are HTML-escaped before
being displayed in Plotly tooltips.

## Snapshot contract

A separate `/data/project_launches.json` (or configured data directory) is
published **atomically**, with file mode 0600. Its versioned format is
`ZOONIVERSE_PROJECT_LAUNCHES_V2`. Example (illustrative data):

```json
{
  "format": "ZOONIVERSE_PROJECT_LAUNCHES_V2",
  "first": "2026-10-01",
  "last": "2026-10-27",
  "as_of": "2026-10-28",
  "last_modified_utc": "2026-10-28T15:00:00+00:00",
  "history_days": 27,
  "launches": [
    ["2026-10-13", 1, ["Example Project"]],
    ["2026-10-27", 3, ["Galaxy Survey", "Planet Search"]]
  ]
}
```

`launches` is a sorted, sparse series of **nonzero** daily aggregates: each day
appears at most once. Each entry is `[UTC date, approved count, public titles]`.
The titles list can be shorter than the count because private or unnamed projects
are excluded, and is limited to 1,000 names with a maximum of 160 characters
per title. No fake zeros are stored for dates without launches.
`first`/`last` delimit the collected coverage, while `as_of` is the next UTC
date; `history_days = 0` indicates all available history, with `first` set to
the earliest observed approved launch (or `last` if there are none). Bounded
windows use the full requested date range even if no launches occurred.

On load the app validates the exact schema, coverage, timestamp, positive integer
counts, uniqueness, strict ascending dates, title lengths/counts, file size and
symlink safety. Existing V1 count-only snapshots remain readable and are
upgraded to V2 on the next successful refresh. Invalid or missing files do not
affect any of the activity charts. If fetch or publication
fails, an existing valid launch snapshot is **not overwritten** and the
refresh is reported as a `partial_failure` with `project_launches: failed`.
Older three-source `refresh_status.json` files are still loadable.

## Chart behavior

Users, Classifications, and Talk read the same local launch snapshot (no
network/database calls on navigation). The violet triangular markers display
one point per calendar **launch date**, with up to eight HTML-escaped public
titles and the approved launch count in the hover tooltip. Missing or private
titles are not displayed. Hover dates use `Mon 25 May 2026` (UTC); grouped
bin tooltips display the weekday of both observed boundary dates.

**Line charts** retain a continuous UTC date X-axis. **Daily bars** also
use the date axis. **Weekly, monthly and yearly bars** use equally spaced
calendar period slots with their own labels (e.g. `05 Jan '26`, `Oct 2026`,
`2026`) rather than daily date ticks. Partial first/last calendar periods
occupy the corresponding fraction of a slot and show the observed dates in
bar hovers. Launch triangles retain the **real launch day within each calendar
slot** and always show original dates and public names on hover.

On **all chart types**, launch markers sit at the baseline (Y=0), drawn in
front of the bars. The smaller, saturated violet triangles have no white
outline: clustered launches remain less visually intrusive than outlined
symbols placed above bars. The horizontal range slider, pan, zoom, and
visible-range Y autoscaling continue to work. Label density adapts to zoom
and screen width client-side.

The plotting change does not move or re-bin the stored launches, modify
activity totals, rolling averages, anomaly analysis, or clickable anomaly
diamonds. Launch markers are clipped independently to each activity snapshot;
invalid or unavailable launch overlays simply result in no launch markers.

The **Projects** navigation page visualizes launch *counts per month/year*
from this exact same file, with zeros inserted only for fully covered dates
without launches. See [Projects](projects.md).

## Verification

Run the existing Python tests (including `tests/test_project_launches.py`).
After deploy, use **Refresh data** (or `refresh-data` CLI) and check the
`project_launches` refresh stage completes. Then verify `project_launches.json`
is present in the configured data directory and violet markers appear on **all
three** activity pages for known launch dates, e.g. 2026-10-27. Check that the
hover shows the weekday and public titles; compare the counts with the original
probe. Check grouping controls, restart the app, and confirm markers persist.
Keep this internal dashboard access-controlled; public project names may change
or become private after a snapshot was collected. Never copy credentials into
this file or publish its contents as an open dataset.
