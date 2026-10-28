"""Gunicorn and local entrypoint for the Zooniverse platform dashboard."""

import os

from zooniverse_dashboard.app import app

# WSGI callable used by Gunicorn in the Docker image.
server = app.server

if __name__ == "__main__":
    app.run(
        host=os.getenv("DASH_HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8050")),
        debug=os.getenv("DASH_DEBUG", "false").lower() == "true",
    )
