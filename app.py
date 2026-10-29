"""Start the dashboard locally or expose its Flask server to Gunicorn.

This root-level module is the deployment entry point, distinct from
:mod:`zooniverse_dashboard.app`, which constructs the Dash application and
registers the dashboard callbacks. Importing ``app`` here exposes the Flask
WSGI callable as ``server`` for the Docker image's ``gunicorn app:server``
command; the development server starts only when the file is executed directly.

The development invocation ``python app.py`` binds to ``DASH_HOST`` (default
``127.0.0.1``) and ``PORT`` (default ``8050``); ``DASH_DEBUG=true`` enables
Dash debugging. These settings do not inject a database connection or refresh
upstream data. Secrets are provided separately at runtime, and page views read
only local validated snapshots.

The root-level file is intentionally outside the Sphinx AutoAPI source package;
for the application's reusable API see
:func:`zooniverse_dashboard.app.create_app`.
"""

import os

from flask import Flask

from zooniverse_dashboard.app import app

# The WSGI callable used by Gunicorn in the Docker image.
server: Flask = app.server

if __name__ == "__main__":
    app.run(
        host=os.getenv("DASH_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8050")),
        debug=os.getenv("DASH_DEBUG", "false").lower() == "true",
    )
