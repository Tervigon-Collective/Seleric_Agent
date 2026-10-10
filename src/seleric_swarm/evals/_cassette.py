"""Record/replay MCP cassettes for deterministic evals (M0.4).

A cassette is the recorded ``capability + arguments -> response`` traffic of one
mission run, keyed by the canonical hash of the request. ``ReplayMcpClient``
serves those entries and *fails on a miss* — a silent default would let a case
pass while the underlying query never actually ran.

Both clients duck-type the ``MCPGateway`` surface the runner uses
(``capabilities``, ``async call(*, agent_id, capability, arguments)``), so a
runtime can be given a replay client with no other change.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CASSETTE_SCHEMA_VERSION = 1


class CassetteMiss(LookupError):
    """A replay client was asked for a request that was not recorded."""

    def __init__(self, capability: str, arguments: dict[str, Any]) -> None:
        self.capability = capability
        self.arguments = arguments
        super().__init__(
            f"cassette miss for {capability!r} with arguments "
            f"{json.dumps(_canonical(arguments), sort_keys=True)}"
        )


def _canonical(value: Any) -> Any:
    """Normalise a value to a JSON-comparable form (tuples -> lists)."""

    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def request_hash(capability: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"capability": capability, "arguments": _canonical(arguments)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CassetteEntry:
    capability: str
    arguments: dict[str, Any]
    response: Any


@dataclass
class Cassette:
    """The recorded traffic of one or more missions."""

    entries: dict[str, CassetteEntry] = field(default_factory=dict)

    def record(self, capability: str, arguments: dict[str, Any], response: Any) -> None:
        key = request_hash(capability, arguments)
        self.entries[key] = CassetteEntry(
            capability=capability,
            arguments=_canonical(arguments),
            response=response,
        )

    def lookup(self, capability: str, arguments: dict[str, Any]) -> Any:
        key = request_hash(capability, arguments)
        entry = self.entries.get(key)
        if entry is None:
            raise CassetteMiss(capability, arguments)
        return copy.deepcopy(entry.response)

    def capabilities(self) -> set[str]:
        return {entry.capability for entry in self.entries.values()}

    def merge(self, other: Cassette) -> None:
        self.entries.update(other.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CASSETTE_SCHEMA_VERSION,
            "entries": {
                key: {
                    "capability": entry.capability,
                    "arguments": entry.arguments,
                    "response": entry.response,
                }
                for key, entry in sorted(self.entries.items())
            },
        }

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        source = Path(path)
        text = source.read_text(encoding="utf-8")
        if source.suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cassette:
        version = data.get("schema_version", CASSETTE_SCHEMA_VERSION)
        if version != CASSETTE_SCHEMA_VERSION:
            raise ValueError(f"unsupported cassette schema_version {version!r}")
        cassette = cls()
        for key, entry in (data.get("entries") or {}).items():
            cassette.entries[key] = CassetteEntry(
                capability=str(entry["capability"]),
                arguments=_canonical(entry.get("arguments") or {}),
                response=entry.get("response"),
            )
        return cassette

    @classmethod
    def load_dir(cls, directory: str | Path) -> Cassette:
        cassette = cls()
        for path in sorted(Path(directory).glob("*.json")):
            cassette.merge(cls.load(path))
        return cassette


class RecordingMcpClient:
    """Wrap a live client and record every call into a cassette."""

    def __init__(self, inner: Any, cassette: Cassette | None = None) -> None:
        self._inner = inner
        self.cassette = cassette if cassette is not None else Cassette()
        self.invocations: list[dict[str, Any]] = []

    @property
    def capabilities(self) -> set[str]:
        return set(getattr(self._inner, "capabilities", set()))

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        response = await self._inner.call(
            agent_id=agent_id, capability=capability, arguments=arguments
        )
        self.cassette.record(capability, arguments, response)
        self.invocations.append(
            {"agent_id": agent_id, "capability": capability, "arguments": arguments}
        )
        return response

    def save(self, path: str | Path) -> None:
        self.cassette.save(path)

    async def aclose(self) -> None:
        closer = getattr(self._inner, "aclose", None)
        if closer is not None:
            await closer()


class ReplayMcpClient:
    """Serve recorded responses; raise ``CassetteMiss`` on anything unrecorded."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self.invocations: list[dict[str, Any]] = []

    @property
    def capabilities(self) -> set[str]:
        return self.cassette.capabilities()

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        response = self.cassette.lookup(capability, arguments)
        self.invocations.append(
            {"agent_id": agent_id, "capability": capability, "arguments": arguments}
        )
        return response

    async def aclose(self) -> None:
        return None


def merge_cassettes(paths: Iterable[str | Path], target: str | Path) -> Cassette:
    """Merge many cassette files into one — the per-suite replay fixture."""

    merged = Cassette()
    for path in paths:
        merged.merge(Cassette.load(path))
    merged.save(target)
    return merged
