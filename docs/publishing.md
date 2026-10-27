# Publishing the Sphinx documentation

The repository contains a **documentation-only GitHub Actions workflow** at
`.github/workflows/docs.yml`. It installs the package's documentation extras,
builds Sphinx HTML, and publishes that static output through GitHub Pages.
It **does not** execute the dashboard, collect activity, run NB2 analysis,
or access Panoptes or ERAS.

## Before enabling GitHub Pages

**Publishing risk:** GitHub Pages may serve its content **publicly even if the
GitHub source repository is private**. Confirm with Zooniverse maintainers
that these documentation pages are suitable for public access. Never include
secrets, internal hostnames, private network routes, real `.env` files,
or operational log files in Sphinx sources.

Repository maintainers must enable deployment once in GitHub:

1. Open the repository's **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Check that Actions are enabled and that the `github-pages` environment is
   allowed to deploy from version tags such as `v1.0.0`; update any
   branch-only environment protection rule if required.
4. Push a version tag such as `v0.1.0` to publish the documentation.
   Regular pushes to `main` do not publish the site.

The static site normally becomes available at
`https://zooniverse.github.io/zooniverse-platform-dashboard/` after a
successful deployment, subject to organization Pages configuration.

## Building and publishing

After the reusable test workflow passes, the documentation `build` job
uses Python 3.12, runs
`python -m pip install '.[docs]'`, then:

```bash
sphinx-build -b html -W --keep-going docs docs/_build/html
```

It uploads `docs/_build/html` as a Pages artifact; a separate `deploy` job
publishes the artifact using GitHub's supported Pages actions. It is triggered
only by pushed `v*` tags. No `gh-pages` branch, PAT, or stored credentials
are required.

To check the documentation locally before publishing:

```bash
python -m pip install -e '.[docs]'
sphinx-build -b html docs docs/_build/html
```

The Sphinx build uses Furo, MyST and AutoAPI. AutoAPI parses source files
without importing the Dash runtime or requesting external data. The build
runs without an `.env` file or Panoptes credentials.

## Troubleshooting

- If Pages reports an unavailable publishing source, select **GitHub Actions**
  under Settings → Pages and rerun the workflow.
- If the build cannot find `autoapi`, ensure `sphinx-autoapi` is installed via
  the `docs` extra (`python -m pip install -e '.[docs]'`).
- If the build succeeds but the URL does not resolve, check the deployment
  job, repository Pages permission, and organization policy.
- If the site looks outdated, confirm that a release tag was pushed
  and that its documentation workflow succeeded; a `main` push is not enough.

This workflow is deliberately scoped to documentation. Tests and linting run
in reusable CI before a tagged release publishes documentation or an image.
Application deployment remains a separate future operation.
