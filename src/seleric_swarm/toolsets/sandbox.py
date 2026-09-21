"""SandboxToolset — run agent-authored python over already-fetched evidence.

Generalises ``AnalyticsToolset``'s six fixed calculators: instead of a frozen
function per aggregation, the model writes arbitrary arithmetic over the same
``EvidenceArtifact``s and gets a ``Finding`` back. Same non-negotiable rules:

- Rule 5: computes on evidence already in the ``ArtifactStore`` (loaded by
  ``evidence_ids``) — it never fetches its own data.
- Rule 4: never calls another tool.
- Rule 6: the output ``Finding`` carries the ``evidence_ids`` it computed over.

Intermediate files the script writes under ``WORKDIR`` persist on disk
(``.data/sandbox/<mission_id>/``) and are listed back so a later tool/LLM call
can reference them — the "short memory" the user asked for.

# ponytail: in-process exec, no hard kill / no true isolation — a runaway
# script is abandoned (daemon thread) but not killed, and file I/O is confined
# only by convention (WORKDIR). Move to subprocess+rlimits or a container if
# untrusted-code risk rises.
"""

from __future__ import annotations

import builtins as _builtins
import contextlib
import io
import os
import threading
import traceback
from pathlib import Path
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.paths import repo_root
from seleric_swarm.toolsets.analytics import _load_evidence, _provenance, _write_finding

_DEFAULT_TIMEOUT_S = 10.0

# stdlib modules the sandbox may import; numpy/pandas added only if installed.
_ALLOWED_IMPORTS: set[str] = {
    "math", "statistics", "json", "datetime", "itertools",
    "functools", "collections", "decimal", "fractions", "re", "time",
}
for _opt in ("numpy", "pandas"):
    with contextlib.suppress(Exception):  # optional dependency absent — skip
        __import__(_opt)
        _ALLOWED_IMPORTS.add(_opt)

# Everything from builtins except the process-escape hatches. exec/eval/compile
# and open-by-default stay out of the base set; open is re-added scoped to
# WORKDIR usage by convention (see ceiling note above).
_UNSAFE_BUILTINS = frozenset({
    "eval", "exec", "compile", "__import__", "globals", "locals",
    "vars", "input", "exit", "quit", "help", "breakpoint", "memoryview",
})
_SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(_builtins, name)
    for name in dir(_builtins)
    if not name.startswith("_") and name not in _UNSAFE_BUILTINS
}


def _timeout_s() -> float:
    try:
        return float(os.environ.get("SELERIC_SANDBOX_TIMEOUT_S", _DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _enabled() -> bool:
    return os.environ.get("SELERIC_SANDBOX_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def _make_guarded_import(os_facade: _WorkdirOs) -> Any:
    """Per-call importer: ``import os`` yields the WORKDIR facade, not real os."""

    def _guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        root = name.split(".")[0]
        if root == "os":
            return os_facade
        if root not in _ALLOWED_IMPORTS:
            raise ImportError(
                f"import of '{name}' is blocked in the sandbox; allowed: "
                f"{sorted(_ALLOWED_IMPORTS)} (os is pre-provided)"
            )
        return _builtins.__import__(name, *args, **kwargs)

    return _guarded_import


def _workdir(mission_id: str) -> Path:
    safe = "".join(c for c in mission_id if c.isalnum() or c in "-_") or "mission"
    path = repo_root() / ".data" / "sandbox" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


async def run_python(
    ctx: RunContext[SelericDeps],
    code: str,
    evidence_ids: list[str],
    purpose: str = "",
) -> ToolResult:
    """Run a short python script to aggregate/transform already-fetched evidence.

    Use this for computation the fixed analytics tools don't cover (custom
    ratios, rankings, multi-step arithmetic). It never fetches data — pass the
    ``evidence_ids`` returned by ``query_metrics``/``drilldown`` and read them
    inside the script.

    In scope, the script sees:
      - ``evidence``: list of dicts, one per evidence id (metric_id, value,
        unit, dimensions, period_start/period_end, grain).
      - ``WORKDIR``: str path to a per-mission dir; write intermediate files
        there (they persist for later steps). Use ``open(os.path.join(...))``.
      - stdlib math/statistics/json/collections (+ numpy/pandas if available).
    Set a ``result`` variable to the value(s) you computed — numeric entries of
    a ``result`` dict are recorded on the Finding. Blocked: network, arbitrary
    imports, eval/exec/open outside WORKDIR.

    Returns a Finding artifact id. On a code error you get the traceback back
    to fix and retry.
    """
    if not _enabled():
        return ToolResult(
            success=False,
            summary="python sandbox is disabled (SELERIC_SANDBOX_ENABLED=0)",
            error_code="SANDBOX_DISABLED",
        )

    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    try:
        compiled = compile(code, "<sandbox>", "exec")
    except SyntaxError as exc:
        raise ModelRetry(f"sandbox code has a syntax error: {exc}. Fix it and retry.") from exc

    workdir = _workdir(ctx.deps.mission_id)
    before = {p.name for p in workdir.iterdir()} if workdir.exists() else set()
    evidence_dicts = [e.model_dump(mode="json") for e in evidence]

    os_facade = _WorkdirOs(str(workdir))
    safe_builtins = dict(_SAFE_BUILTINS)
    safe_builtins["__import__"] = _make_guarded_import(os_facade)
    sandbox_globals: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "evidence": evidence_dicts,
        "WORKDIR": str(workdir),
        "os": os_facade,
    }

    holder: dict[str, Any] = {}
    stdout = io.StringIO()

    def _run() -> None:
        try:
            with contextlib.redirect_stdout(stdout):
                exec(compiled, sandbox_globals)  # noqa: S102 — see ceiling note
            holder["result"] = sandbox_globals.get("result")
        except Exception as exc:  # captured, re-raised as ModelRetry below
            holder["error"] = exc
            holder["tb"] = traceback.format_exc()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(_timeout_s())
    if thread.is_alive():
        return ToolResult(
            success=False,
            summary=f"sandbox script exceeded {_timeout_s():.0f}s and was abandoned",
            error_code="SANDBOX_TIMEOUT",
            retryable=True,
        )

    if "error" in holder:
        raise ModelRetry(
            f"sandbox code raised {type(holder['error']).__name__}: {holder['error']}. "
            f"Fix the script and retry.\n{holder.get('tb', '')[-800:]}"
        )

    result = holder.get("result")
    new_files = sorted(
        {p.name for p in workdir.iterdir()} - before
    ) if workdir.exists() else []

    if result is None and not new_files:
        raise ModelRetry(
            "sandbox script produced no output — set a `result` variable or write "
            "a file under WORKDIR, then retry."
        )

    metrics: dict[str, float] = {}
    if isinstance(result, dict):
        for key, val in result.items():
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                metrics[str(key)] = float(val)

    printed = stdout.getvalue().strip()
    statement_bits = [purpose.strip() or "sandbox computation"]
    if result is not None:
        statement_bits.append(f"result={_truncate(result)}")
    if new_files:
        statement_bits.append(f"files={', '.join(new_files)}")
    if printed:
        statement_bits.append(f"stdout={_truncate(printed)}")
    statement = " | ".join(statement_bits)

    finding_id = _write_finding(
        ctx,
        finding_type="sandbox_computation",
        statement=statement,
        evidence_ids=list(evidence_ids),
        metrics=metrics,
    )

    warnings = [f"wrote {len(new_files)} file(s) to {workdir}"] if new_files else []
    return ToolResult(
        success=True,
        artifact_ids=[finding_id],
        summary=statement,
        provenance=_provenance(list(evidence_ids)),
        warnings=warnings,
    )


def _truncate(value: Any, limit: int = 400) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


class _WorkdirOs:
    """Minimal ``os``-like facade exposing only path joins + the workdir.

    The real ``os`` module is not injected (it exposes ``system``/``environ``);
    scripts only need ``os.path.join`` and ``os.listdir`` against WORKDIR.
    """

    def __init__(self, workdir: str) -> None:
        self.path = os.path
        self._workdir = workdir

    def listdir(self, path: str | None = None) -> list[str]:
        return os.listdir(path or self._workdir)
