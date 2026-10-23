"""Sphinx build: importable modules must not perform network access."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
project = "Zooniverse Platform Dashboard"
author = "Zooniverse contributors"
release = "0.1.0"
root_doc = "index"
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.autosummary", "sphinx.ext.napoleon"]
autosummary_generate = True
exclude_patterns = ["_build"]
html_theme = "alabaster"
