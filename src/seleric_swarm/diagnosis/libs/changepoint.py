"""Change-point detection with PELT (``ruptures``), the WHEN operator's engine (plan §5.7).

``ruptures`` is a Phase 4 dependency and is not installed in the current image: the import is lazy and its absence
is a typed error, not a crash at import time."""

from __future__ import annotations

from collections.abc import Sequence

from seleric_swarm.diagnosis.domain.errors import LibraryFailed, LibraryUnavailable


def detect_changepoints(
    values: Sequence[float],
    *,
    penalty: float,
    min_size: int = 7,
    model: str = "l2",
) -> tuple[int, ...]:
    """Indices at which a new regime starts (0-based, the end of the series excluded), in increasing order."""
    try:
        import numpy as np
        import ruptures
    except ImportError as exc:
        raise LibraryUnavailable(f"ruptures is required for change-point detection: {exc}") from exc
    if len(values) < 2 * min_size:
        raise LibraryFailed(f"{len(values)} points are too few for regimes of at least {min_size}")
    try:
        found = ruptures.Pelt(model=model, min_size=min_size, jump=1).fit(np.asarray(values, dtype=float)).predict(pen=penalty)
    except Exception as exc:
        raise LibraryFailed(f"PELT failed: {type(exc).__name__}: {exc}") from exc
    return tuple(int(i) for i in found if i < len(values))
