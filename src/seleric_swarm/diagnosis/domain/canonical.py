"""Canonical serialisation and fingerprints.

Replay (D-21) must reproduce a run byte for byte, so everything that is hashed or stored goes through one
serialisation: sorted keys, no whitespace, floats in ``repr`` form (shortest round-trip), ``-0.0`` as ``0.0``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date
from typing import Any

import numpy


def _plain(value: Any) -> Any:
    if isinstance(value, numpy.generic):  # numpy scalars (int64, bool_, float32 ...) are not JSON types
        value = value.item()
    if isinstance(value, numpy.ndarray):
        value = value.tolist()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number cannot be fingerprinted: {value!r}")
        return 0.0 if value == 0.0 else value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(value: Any, *, length: int = 12) -> str:
    """Short stable digest of any JSON-like value (dates allowed)."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def series_fingerprint(values: Mapping[date, float]) -> str:
    """Digest of a daily series, independent of insertion order."""
    return fingerprint([[d.isoformat(), v] for d, v in sorted(values.items())], length=16)


def segments_fingerprint(segments: Mapping[str, Mapping[date, float]]) -> str:
    """Digest of a split series: each segment's digest under its name, independent of order."""
    return fingerprint({name: series_fingerprint(values) for name, values in segments.items()})
