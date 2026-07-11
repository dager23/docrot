"""docrot — deterministic doc-to-code drift checker.

Public API: :func:`check` runs the full pipeline programmatically and
returns a :class:`docrot.model.Report`.
"""

from __future__ import annotations

from pathlib import Path

from docrot.model import Finding, Report

__version__ = "0.1.0.dev0"

__all__ = ["Finding", "Report", "__version__", "check"]


def check(root: str | Path = ".") -> Report:
    """Check the repository at `root` with configuration discovered there."""
    from docrot.config import load_config
    from docrot.engine import run_check

    return run_check(load_config(Path(root).resolve()))
