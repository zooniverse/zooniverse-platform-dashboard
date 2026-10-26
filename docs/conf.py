"""Sphinx configuration: AutoAPI parses source without importing runtime modules."""

from pathlib import Path

project = "Zooniverse Platform Dashboard"
author = "Zooniverse contributors"
release = "0.1.0"
root_doc = "index"
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
extensions = ["myst_parser", "autoapi.extension", "sphinx.ext.napoleon"]
# Render the detector's inline and display equations in MyST Markdown.
myst_enable_extensions = ["dollarmath"]
exclude_patterns = ["_build"]

html_theme = "furo"
html_title = "Zooniverse Platform Dashboard"

# One page per module/class instead of a flat list of automodule directives.
# Static parsing avoids imports that could initialize Dash or remote data clients.
autoapi_type = "python"
autoapi_dirs = [str(Path(__file__).resolve().parents[1] / "src" / "zooniverse_dashboard")]
autoapi_root = "generated"
autoapi_add_toctree_entry = False
autoapi_own_page_level = "class"
autoapi_options = ["members", "show-inheritance", "show-module-summary"]
autoapi_keep_files = False
