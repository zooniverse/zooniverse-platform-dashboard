"""Server-side paths and environment configuration; never expose database URLs to Dash."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def data_dir() -> Path:
    """A persistent POSIX directory shared by all web workers."""
    return Path(os.environ.get("ANALYTICS_DATA_DIR", ".local/data")).expanduser()


def panoptes_url() -> str:
    """A PostgreSQL URL supplied via the server environment, never written to JSON."""
    url = os.environ.get("DB_URL_PANOPTES", "")
    if not url.startswith(("postgresql+psycopg://", "postgresql://")):
        raise RuntimeError("Panoptes PostgreSQL connection is not configured")
    return url
