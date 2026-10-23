# Operations and deployment

Use one Dockerized Dash/Gunicorn service, a persistent named volume at `/data`, and
an authenticated TLS ingress. Production `DB_URL_PANOPTES` is injected as a Docker
secret/environment value when CI/deployment is configured later. It must never be
committed or written to aggregate snapshots.

Click **Refresh data** to collect completed UTC days through yesterday. The coordinator
has a non-blocking cross-worker flock and a durable 60-minute cooldown. Both are stored
on the same volume as the JSON, so multiple users cannot start overlapping refreshes.
One failed stream retains its previous file; the dashboard displays freshness per stream.

A first ERAS refresh starts with 28 days. For more history, issue the one-time command:

```bash
zooniverse-dashboard backfill --source classifications --start 2025-01-01
zooniverse-dashboard backfill --source talks --start 2025-01-01
```

Run it against the deployed container volume, not an ephemeral one. Explicit backfills
also use the shared lock, and may take much longer than web requests. Do not scale to
multiple isolated replicas. The health route `/healthz` shows process availability,
**not** database or analytics freshness. No scheduler, cron, extra database, bucket,
or GitHub workflow is included.

### Date semantics

Panoptes `created_at` is a timestamp without time zone. We assume UTC calendar
interpretation but it requires confirmation from the source maintainers before using
these figures in formal external reporting. ERAS provides UTC daily buckets.

### Production access control

All visitors can refresh; authentication and HTTPS belong at the trusted ingress
(SSO/VPN/reverse proxy). Never expose this HTTP app without ingress-level access
control. Consider a separate refresh worker only if normal ERAS requests exceed
Gunicorn's bounded request timeout.
