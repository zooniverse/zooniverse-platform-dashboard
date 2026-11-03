# Projects: approved launch history

The **Projects** page presents approved Panoptes project launches by
**Month** or **Year**. It reads the same `project_launches.json` snapshot
used for launch markers on the Users, Classifications, and Talk pages. It does
not query Panoptes during page navigation.

## Launch definitions and snapshot data

The collector in `project_launches.py` counts projects where
`launch_approved IS TRUE` and `launch_date IS NOT NULL`, grouped by
`launch_date::date`. The date comes from the Panoptes launch field, not
`created_at` or the first classification. Counts include approved projects
regardless of their current privacy status. The snapshot stores dates and
counts, plus bounded display names for projects currently marked non-private;
private project titles and project-owner details are excluded.

The snapshot uses the canonical `ZOONIVERSE_PROJECT_LAUNCHES_V2` format.
Launch entries are sparse: dates with no approved launches are omitted, and
missing dates represent zero only within the snapshot's validated coverage.
Public project names are available to launch-marker tooltips on activity
charts; the Projects chart displays launch totals rather than individual
project titles. Collection and validation details are documented in
`project_launches.py` and [Architecture](architecture.md).

## Page controls and chart interpretation

- **Group by:** Month (default) or Year only. A calendar period's total is
  the sum of approved launches recorded on dates in that calendar period.
  This is **projects per period**, not a percentage or a continuous daily rate.
- **Chart type:** Bars (default) or Line. Calendar months/years have evenly
  spaced bar slots, with appropriately labelled X-axis ticks. Line charts
  use a UTC date axis, retaining the same monthly/yearly totals.
- **Visible time window:** Last 12 months, Last 3 years (default), Last 5 years,
  or All available. A window is based on **calendar months**, not an
  approximation of 30 or 365 days. The range slider supports finer exploration.
- **Shared zoom:** the selected absolute UTC range persists when switching
  monthly/yearly, bar/line, or navigating to Users, Classifications and Talk.
  Explicitly changing the visible-window preset or resetting Plotly axes clears
  the shared manual range for that browser tab.

Periods without approved launches are shown as **zero** when covered by the
snapshot; if months or years lie outside its coverage, they are **not** silently
filled. The first/last periods might contain only a handful of observed days:
they are not scaled up to full periods. Bars show proportional edge widths;
hover gives the exact observed dates in UTC and the approved launch count.
**No separate launch triangles** are drawn on the Projects page because its
primary series already represents the launches.

## Summary cards

The page shows the count of all **recorded launches in the collected range**, plus
current/previous calendar month and current/previous calendar year. The current
period is **to date**, through the most recent completed UTC day. The previous
period needs full snapshot coverage; otherwise it displays **Insufficient
historical coverage** instead of an incorrect zero. A current period that has
no completed days yet displays zero. The page includes snapshot timestamp,
coverage and staleness notices, and warns when the active history limit has
changed since collection.

## Safety and operation

The page reads only the validated JSON file, never Panoptes or ERAS during
navigation, hovering, grouping or zooming. Only approved daily counts are
used in these aggregate charts. Project titles appear only in the launch-marker
tooltips on activity charts, with HTML escaping. The Projects page does not
affect anomaly analysis. Invalid or missing snapshots display an empty-state
notice directing operators to **Refresh data**.

For full launch history starting with the earliest observed approved launch,
configure `ANALYTICS_HISTORY_DAYS=0` and refresh. A restricted collection
window will only support summaries and chart periods covered by that range.

## Local verification

Run `pytest -q tests/test_projects_page.py`, then `python app.py`. Visit
`/projects`, toggle **Month/Year**, toggle **Line/Bars**, check a month without
launches, and zoom into a past interval. Visit Users or Talk and verify the
same UTC range. No data refresh is necessary if `project_launches.json`
already exists and is valid.
