"""Versions of the analysis libraries, recorded in every run record so a replay can tell what produced a result."""

from __future__ import annotations

from importlib import metadata

TRACKED = ("dowhy", "ruptures", "statsmodels", "networkx", "numpy", "scipy", "pandas", "scikit-learn")
NOT_INSTALLED = "not installed"


def library_versions(names: tuple[str, ...] = TRACKED) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = NOT_INSTALLED
    return out
