"""Seeded random streams: the one place a generator is derived from (seed, stream name)."""

from __future__ import annotations

import hashlib

import numpy


def stream_key(stream: str) -> int:
    # Not ``hash()``: Python salts string hashes per process, which would break replay.
    return int.from_bytes(hashlib.sha256(stream.encode("utf-8")).digest()[:8], "big")


def stream_rng(seed: int, stream: str) -> numpy.random.Generator:
    return numpy.random.default_rng([seed, stream_key(stream)])
