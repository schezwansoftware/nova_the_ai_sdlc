"""Single source of truth for the ai-sdlc CLI's reported version, shared by
the `--version` flag (main.py) and the startup banner (formatters.py)."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    CLI_VERSION = version("ai-sdlc")
except PackageNotFoundError:
    # Running from source without `pip install -e .` having been run --
    # keep in sync with pyproject.toml's [project].version.
    CLI_VERSION = "0.1.0"
