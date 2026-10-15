"""One registry type for every extension point (plan P-3, D-29): operators, evidence ports, edge policies, detectors,
estimators, refuters. Adding one is a new module that registers a class; no scheduler or core edit."""

from __future__ import annotations

from collections.abc import Callable

from seleric_swarm.diagnosis.domain.errors import DiagnosisError


class RegistryError(DiagnosisError):
    code = "registry_error"


class Registry[T]:
    """Classes keyed by their ``kind`` string. Listing order is by kind, so it never depends on import order."""

    def __init__(self, what: str) -> None:
        self.what = what
        self._items: dict[str, type[T]] = {}

    def register(self, item: type[T]) -> type[T]:
        """Class decorator. Refuses a class without a ``kind`` and a kind that is already taken."""
        kind = getattr(item, "kind", None)
        if not isinstance(kind, str) or not kind:
            raise RegistryError(f"{self.what} {item.__name__} has no string `kind`")
        if kind in self._items:
            raise RegistryError(f"{self.what} kind {kind!r} is already registered by {self._items[kind].__name__}")
        self._items[kind] = item
        return item

    def get(self, kind: str) -> type[T]:
        try:
            return self._items[kind]
        except KeyError as exc:
            raise RegistryError(f"no {self.what} registered for kind {kind!r}") from exc

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def create_all(self, factory: Callable[[type[T]], T] | None = None) -> tuple[T, ...]:
        make = factory or (lambda cls: cls())
        return tuple(make(self._items[k]) for k in self.kinds())
