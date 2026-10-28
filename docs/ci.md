# Continuous integration and Docker image builds

GitHub Actions validates each change and publishes release images only when a
version tag is pushed. The image is intended for a future Zooniverse deployment.
**No CI workflow deploys the application or needs Panoptes/ERAS access.**
The [documentation publishing workflow](publishing.md) also runs only on
version tags, after tests pass.

## Tests and quality checks

`.github/workflows/tests.yml` runs on pull requests, pushes to `main`, and
manual dispatch. It is also reusable by both release workflows to gate
publishing. It checks Python **3.11 and 3.12** with:

```bash
python -m pip install -e '.[dev,docs,server,analysis]'
black --check app.py src tests
ruff check app.py src tests
pytest -q
```

A separate `docker-smoke` job builds the Dockerfile and launches its image
on an ephemeral localhost port. It validates the WSGI startup, main routes,
health endpoint, runtime user, and persistent volume across restart with
`scripts/docker_smoke.sh`; it requires neither upstream credentials nor live
ERAS access. Both Python checks and this Docker smoke test must pass before
a version-tag publishing workflow can proceed.

Tests must run offline using synthetic/aggregate fixtures. They must not depend
on production credentials, service availability, real volunteer records, or a
live Panoptes/ERAS query. Pull-request checks run without deployment secrets.

## Build and publish the container image

`.github/workflows/container.yml` runs only when a version tag such as
`v1.0.0` is pushed. It **first runs the reusable Python and Docker smoke-test workflow**, then
builds the repository's `Dockerfile` for `linux/amd64`:

| Trigger | Test and build | Publish to GHCR |
|---|---|---|
| Pull request | Python checks + Docker build and smoke test | No |
| Push to `main` | Python checks + Docker build and smoke test | No |
| Push a `v*` tag | Yes | Yes |

Images are published to **GitHub Container Registry (GHCR)** under the lower-case
repository name, normally:

```text
ghcr.io/zooniverse/zooniverse-platform-dashboard:sha-<full-commit-sha>
ghcr.io/zooniverse/zooniverse-platform-dashboard:v1.0.0
```

Only eligible tags are published; a specific version tag appears only when that
Git tag is pushed. The immutable-intent `sha-...` reference is best for a
deployment handoff, although registry tag immutability depends on GHCR settings.
For strict image identification and reproducible rollbacks, record the digest
(`@sha256:...`) reported by the registry once the image is published.
No `main` or `latest` tag is published. Builds include provenance and a software
bill of materials. The workflow does **not** build an ARM64 image yet.

GitHub's automatically provided `GITHUB_TOKEN`, with `packages: write` permission
for the image job, authenticates the publication step. It does not require a
personal access token or a database secret. Organization/repository policies
must allow Actions to publish packages; administrators can grant future
deployment systems read access to the appropriate package.

You can inspect the published package in the repository's **Packages** area.
Package visibility and access are separate settings from the repository's
visibility; coordinate any change with the administrators. Do not assume an
image is publicly pullable.

## Runtime configuration: not part of the image

The image contains application code and the `server` and `analysis` Python
dependencies, but **no live `.env` file, database credentials, or snapshot
content**. The deployment system must provide:

- `DB_URL_PANOPTES`, using an approved read-only connection, if the Users
  collector will be refreshed;
- a persistent writable mount for `ANALYTICS_DATA_DIR` (the Dockerfile defaults
  to `/data`), holding aggregate JSON, the anomaly catalog, refresh status,
  and locks;
- ingress authentication/TLS and restricted network access to the dashboard;
- any network allowlisting/VPN requirements for accessing Panoptes.

The service is designed for a single replica with a shared writable filesystem;
it uses POSIX locks and a detached local refresh worker. Do not scale replicas or
expose the refresh endpoint publicly without revisiting the architecture.

Once the administrators select a deployment platform, they can pull the
published image by tag or digest and inject runtime environment variables using
their existing secret-management workflow. **Publishing an image is not a
production deployment.**

See [Local installation](installation.md), [Operations](operations.md), and
[Privacy](privacy.md) for configuration details and operational safeguards.
