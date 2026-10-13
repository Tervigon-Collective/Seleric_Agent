import os
from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate repository root from seleric_swarm package")


def reports_dir() -> Path:
    """Where generated reports (forecast backtests and scores) are written.

    ``SELERIC_REPORTS_DIR`` points it at a writable volume; in the image the repo root is not writable by
    the service user, so the default ``<repo>/reports`` only works from a checkout.
    """
    override = os.getenv("SELERIC_REPORTS_DIR", "").strip()
    return Path(override) if override else repo_root() / "reports"
