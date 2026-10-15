"""Everything non-deterministic an investigation may touch, behind one port (plan P-4, D-30).

The core never reads the wall clock, never draws from a global random generator and never checks a cancel flag
directly: it asks the ``Runtime``. A replay supplies a runtime that returns the recorded values."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import numpy


class Runtime(Protocol):
    def now(self) -> datetime: ...

    def rng(self, stream: str) -> numpy.random.Generator:
        """A generator for the named stream, derived from the run seed: the same (seed, stream) always yields the
        same sequence, and different streams do not disturb each other."""
        ...

    def cancelled(self) -> bool: ...
