"""Errors of the diagnosis core. Every failure is typed so it can be shown as an event, never swallowed."""

from __future__ import annotations


class DiagnosisError(Exception):
    """Base class: carries a stable ``code`` for events and tests."""

    code = "diagnosis_error"


class GraphRejected(DiagnosisError):
    """The dependency graph cannot be used at all (wrong schema, corrupt, inconsistent with its own counts)."""

    code = "graph_rejected"

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


class InvalidRequest(DiagnosisError):
    """A window, scope or series request that cannot mean anything."""

    code = "invalid_request"


class LibraryUnavailable(DiagnosisError):
    """A third-party analysis library the operation needs is not installed in this image."""

    code = "library_unavailable"


class LibraryFailed(DiagnosisError):
    """The library ran and could not produce a result (singular design, too few rows, ...). Never swallowed."""

    code = "library_failed"
