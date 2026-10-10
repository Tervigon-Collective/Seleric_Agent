"""Verified examples store (M1.3). Public surface only — see ``__all__``."""

from seleric_swarm.understand.examples._store import (
    ExampleBackend,
    ExampleStore,
    InMemoryExampleBackend,
    VerifiedExample,
    check_no_collision,
)

__all__ = [
    "ExampleBackend",
    "ExampleStore",
    "InMemoryExampleBackend",
    "VerifiedExample",
    "check_no_collision",
]
