"""Build the offline Sphinx and AutoAPI documentation for this package.

AutoAPI statically parses the modules in ``src/zooniverse_dashboard`` instead
of importing them, so generating API pages does not create the Dash app, open
a Panoptes connection, or request ERAS data. The narrative guides use MyST
Markdown alongside the reStructuredText API index; Napoleon interprets the
NumPy-style sections in source docstrings. The ``release`` setting is kept in
sync with the distribution version in ``pyproject.toml`` when a release is cut.

The root development launcher ``app.py`` and the opt-in script under ``scripts``
are outside AutoAPI's package scan. Their operator documentation lives in the
installation and command-line guides instead.
"""

from pathlib import Path

project: str = "Zooniverse Platform Dashboard"
author: str = "Zooniverse contributors"
release: str = "0.1.0"
root_doc: str = "index"
source_suffix: dict[str, str] = {".rst": "restructuredtext", ".md": "markdown"}
extensions: list[str] = ["myst_parser", "autoapi.extension", "sphinx.ext.napoleon"]
# Render the detector's inline and display equations in MyST Markdown.
myst_enable_extensions: list[str] = ["dollarmath"]
exclude_patterns: list[str] = ["_build"]

html_theme: str = "furo"
html_title: str = "Zooniverse Platform Dashboard"

# One page per module/class instead of a flat list of automodule directives.
# Static parsing avoids imports that could initialize Dash or remote data clients.
autoapi_type: str = "python"
autoapi_dirs: list[str] = [
    str(Path(__file__).resolve().parents[1] / "src" / "zooniverse_dashboard")
]
autoapi_root: str = "generated"
autoapi_add_toctree_entry: bool = False
autoapi_own_page_level: str = "class"
autoapi_options: list[str] = ["members", "show-inheritance", "show-module-summary"]
autoapi_keep_files: bool = False
