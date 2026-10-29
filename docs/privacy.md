# Privacy and data boundaries

Panoptes users contain restricted fields such as emails, password hashes,
reset/confirmation tokens, API keys, IP addresses, names and sign-in history.
The entire application is prohibited from selecting or logging these fields.

The production Panoptes database is queried using two reviewed, read-only
projections. Registration counts use:

```sql
SELECT created_at::date AS day, COUNT(*)::bigint AS registrations
FROM public."users"
WHERE created_at IS NOT NULL AND created_at < :end_exclusive
GROUP BY created_at::date ORDER BY day;
```

The query runs inside a read-only PostgreSQL transaction with a 20-second statement
timeout, a 250-ms lock timeout, and no retry. It returns aggregated buckets, not
individual user timestamps. The configured database account must also be read-only.

Approved project launches use a separate read-only aggregate query on
`public.projects`:

```sql
SELECT launch_date::date AS day,
       COUNT(*)::bigint AS launches,
       COALESCE(array_agg(LEFT(BTRIM(display_name), 160) ORDER BY display_name)
         FILTER (WHERE private IS FALSE AND display_name IS NOT NULL
                 AND BTRIM(display_name) <> ''), ARRAY[]::text[]) AS public_names
FROM public.projects
WHERE launch_approved IS TRUE AND launch_date IS NOT NULL
  AND launch_date < :end_exclusive
GROUP BY launch_date::date ORDER BY day;
```

A bounded variant additionally restricts `launch_date >= :start_inclusive`.
The query returns one grouped row per date, not individual project records.
Counts include all approved launches, but only currently non-private project
`display_name` values are retrieved. No project IDs, owners, private titles or
individual project timestamps are retrieved. The new V2 `project_launches.json`
contains daily totals and optional public titles. Existing V1 count-only files
remain readable. Titles are bounded, validated, HTML-escaped in graph tooltips,
and excluded from logs/anomaly models. Since titles are user-authored and project
privacy can change, **do not distribute snapshots outside the access-controlled
dashboard**; the next successful refresh replaces names according to current
privacy flags.

ERAS uses public unauthenticated endpoints for classifications and Talk comment
daily totals. It never requests volunteer-level records or discussion text. Response sizes and returned daily bucket counts are bounded; initial
all-time requests have no start date but enforce a maximum response size,
calendar span and reported-total consistency. Missing ERAS date buckets are zero-filled
as provisional zero counts, not proof of no activity. Error details, credentials,
response bodies and SQL messages are not stored or displayed.

Only aggregate activity counts, contextual launch dates/counts, vetted public
project titles and generic refresh states persist in the mounted volume. Files
are validated and atomically replaced; secrets never enter JSON.
The source ZIP, synthetic fixtures, diagnostic data and exploratory scripts are
not part of this project.
