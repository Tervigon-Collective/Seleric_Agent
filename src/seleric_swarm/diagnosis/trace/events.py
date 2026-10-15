"""The kinds of trace event and what each must carry (plan §5.2, D-21).

A kind is registered with the payload keys it requires; the emitter refuses an event that lacks them, so a
malformed event is a loud bug in the producer, never a silent gap in the trace. New operators add kinds with
``register_kind`` (P-3); the UI ignores kinds it does not know."""

from __future__ import annotations

from collections.abc import Iterable

from seleric_swarm.diagnosis.domain.errors import DiagnosisError

_REQUIRED: dict[str, frozenset[str]] = {}


class UnknownEventKind(DiagnosisError):
    code = "unknown_event_kind"


class InvalidEvent(DiagnosisError):
    code = "invalid_event"


def register_kind(kind: str, required: Iterable[str] = ()) -> str:
    if kind in _REQUIRED:
        raise ValueError(f"event kind {kind!r} is already registered")
    _REQUIRED[kind] = frozenset(required)
    return kind


def required_keys(kind: str) -> frozenset[str]:
    try:
        return _REQUIRED[kind]
    except KeyError as exc:
        raise UnknownEventKind(f"event kind {kind!r} is not registered") from exc


def registered_kinds() -> tuple[str, ...]:
    return tuple(sorted(_REQUIRED))


INVESTIGATION_STARTED = register_kind("investigation_started", ("outcome", "scope"))
WINDOW_RESOLVED = register_kind("window_resolved", ("start", "end"))
GRAPH_LOADED = register_kind("graph_loaded", ("graph_version", "catalogue_version", "nodes", "edges", "issues"))
GRAPH_ISSUE = register_kind("graph_issue", ("code", "subject", "detail"))
SUBGRAPH_BUILT = register_kind("subgraph_built", ("nodes", "edges", "excluded"))
NODE_VISITED = register_kind("node_visited", ("node",))
EDGE_TRAVERSED = register_kind("edge_traversed", ("source", "target", "kind"))
OPERATOR_STARTED = register_kind("operator_started", ("operator", "inputs"))
OPERATOR_FINISHED = register_kind("operator_finished", ("operator", "query_count", "duration_ms", "result_digest"))
OPERATOR_FAILED = register_kind("operator_failed", ("operator", "error_type", "message", "inputs_digest"))
QUERY_ISSUED = register_kind("query_issued", ("metric", "cache_hit"))
HYPOTHESIS_OPENED = register_kind("hypothesis_opened", ("hypothesis",))
HYPOTHESIS_TESTED = register_kind("hypothesis_tested", ("hypothesis",))
HYPOTHESIS_RESOLVED = register_kind("hypothesis_resolved", ("hypothesis", "status"))
REFUTER_RESULT = register_kind("refuter_result", ("hypothesis", "refuter", "passed"))
EXCLUSION = register_kind("exclusion", ("metric", "reason_code", "check_value"))
UNEXPLAINED_SHARE_UPDATED = register_kind("unexplained_share_updated", ("share",))
BOUND_CONSUMED = register_kind("bound_consumed", ("bound", "used", "limit"))
INVESTIGATION_CANCELLED = register_kind("investigation_cancelled", ("reason",))
INVESTIGATION_FINISHED = register_kind("investigation_finished", ("termination_reason",))
NARRATION_STARTED = register_kind("narration_started")
