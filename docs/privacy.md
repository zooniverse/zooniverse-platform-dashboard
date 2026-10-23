# Privacy and data boundaries

Panoptes users contain restricted fields such as emails, password hashes,
reset/confirmation tokens, API keys, IP addresses, names and sign-in history.
The entire application is prohibited from selecting or logging these fields.

The **only** production SQL row projection is:

```sql
SELECT created_at::date AS day, COUNT(*)::bigint AS registrations
FROM public."users"
WHERE created_at IS NOT NULL AND created_at < :end_exclusive
GROUP BY created_at::date ORDER BY day;
```

The query runs inside a read-only PostgreSQL transaction with a 20-second statement
timeout, a 250-ms lock timeout, and no retry. It returns aggregated buckets, not
individual user timestamps. The configured database account must also be read-only.

ERAS uses public unauthenticated endpoints for classifications and Talk comment
daily totals. It never requests volunteer-level records or discussion text. Response sizes and returned daily bucket counts are bounded; initial
all-time requests have no start date but enforce a maximum response size,
calendar span and reported-total consistency. Missing ERAS date buckets are zero-filled
as provisional zero counts, not proof of no activity. Error details, credentials,
response bodies and SQL messages are not stored or displayed.

Only daily aggregate counts and generic refresh states persist in the mounted
volume. Files are validated and atomically replaced; secrets never enter JSON.
The source ZIP, synthetic fixtures, diagnostic data and exploratory scripts are
not part of this project.
