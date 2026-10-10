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
import json
import os
import threading
import traceback
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.paths import repo_root
from seleric_swarm.toolsets.analytics import (
    _load_evidence,
    _note_repaired_ids,
    _provenance,
    _unwrap_findings_to_evidence_ids,
    _write_finding,
)

_DEFAULT_TIMEOUT_S = 10.0
# Inline size of the serialized result/stdout returned to the model. Larger
# output is written in full to WORKDIR and referenced, never silently cut.
_DEFAULT_RESULT_CHARS = 20_000
_DEFAULT_MAX_METRICS = 500

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
# ``globals``/``locals``/``vars`` are NOT stripped: they are rebound per run to
# the sandbox namespace (live 2026-10-10 MS3-66cc3bb5f2 — the model wrote
# ``globals().get('evidence')`` and NameError burned a turn). Returning the real
# process globals would be an escape hatch; returning the script dict is not.
_UNSAFE_BUILTINS = frozenset({
    "eval", "exec", "compile", "__import__",
    "globals", "locals", "vars",  # rebound to the sandbox namespace below
    "input", "exit", "quit", "help", "breakpoint", "memoryview",
})
_SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(_builtins, name)
    for name in dir(_builtins)
    if not name.startswith("_") and name not in _UNSAFE_BUILTINS
}
_VARS_MISSING = object()


def _bind_namespace_builtins(namespace: dict[str, Any], builtins_map: dict[str, Any]) -> None:
    """Expose ``globals``/``locals``/``vars`` as views of *namespace*, not the process."""

    def _globals() -> dict[str, Any]:
        return namespace

    def _locals() -> dict[str, Any]:
        # Top-level ``exec`` has no separate locals dict; nested ``def``/``class``
        # still get real function locals from the interpreter.
        return namespace

    def _vars(obj: Any = _VARS_MISSING) -> Any:
        if obj is _VARS_MISSING:
            return namespace
        return _builtins.vars(obj)

    builtins_map["globals"] = _globals
    builtins_map["locals"] = _locals
    builtins_map["vars"] = _vars


def _timeout_s() -> float:
    try:
        return float(os.environ.get("SELERIC_SANDBOX_TIMEOUT_S", _DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _result_chars() -> int:
    return _env_int("SELERIC_SANDBOX_RESULT_CHARS", _DEFAULT_RESULT_CHARS)


def _max_metrics() -> int:
    return _env_int("SELERIC_SANDBOX_MAX_METRICS", _DEFAULT_MAX_METRICS)


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


def _fix_the_script(message: str) -> ToolResult:
    """A script that failed: a failed, retryable result rather than ModelRetry. Tool retries are capped, and an
    exhausted ModelRetry is an UnexpectedModelBehavior that fails the whole attempt and restarts the mission from
    scratch (live 2026-10-09 MS3-70c7ba2445: a second KeyError in a script cost the run its first 25 s)."""
    return ToolResult(success=False, summary=message, error_code="SANDBOX_SCRIPT_ERROR", retryable=True)


def _typed_in_values(code: str, ctx: RunContext[SelericDeps]) -> list[float]:
    """Numeric literals in *code* that equal a value this mission fetched (to the cent).

    A literal is any constant in the parsed script; small numbers (counts, scales, thresholds) are left alone
    by requiring a fractional part or a magnitude beyond everyday constants, and a match against a fetched
    value — so a script that re-types the evidence instead of reading it is caught."""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    literals = {
        float(n.value) for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)
        and (abs(float(n.value)) >= 1000 or float(n.value) != int(n.value))
    }
    if not literals:
        return []
    fetched = []
    for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id):
        if a.artifact_type == "evidence" and isinstance(a.payload, dict):
            v = a.payload.get("value")
            if isinstance(v, (int, float)):
                fetched.append(float(v))
    return sorted(x for x in literals if any(abs(x - f) <= 0.005 * max(1.0, abs(f) / 1000) for f in fetched))


async def run_python(
    ctx: RunContext[SelericDeps],
    code: str,
    evidence_ids: list[str] | None = None,
    purpose: str = "",
) -> ToolResult:
    """Run a short python script to aggregate/transform already-fetched evidence.

    Use this for computation the fixed analytics tools don't cover (custom
    ratios, rankings, multi-step arithmetic). It never fetches data — pass the
    ``evidence_ids`` returned by ``query_metrics``/``drilldown`` and read them
    inside the script (omitted: every evidence row this mission fetched). A finding
    id (e.g. a prefetched table's) is read as the evidence rows it cites. Read every
    figure from ``evidence``; a script that types a fetched value in as a literal is
    refused, because its result would no longer follow the data. Sum or roll up rows here, never in the answer's prose:
    a total computed here is recorded and can be cited.

    In scope, the script sees:
      - ``evidence``: list of dicts, one per evidence id (metric_id, value,
        unit, dimensions, period_start/period_end, grain). Prefer this name
        directly; ``globals()``/``locals()``/``vars()`` also see the sandbox
        namespace (including ``evidence``), not the host process.
      - ``WORKDIR``: str path to a per-mission dir; write intermediate files
        there (they persist for later steps). Use ``open(os.path.join(...))``.
      - stdlib math/statistics/json/collections (+ numpy/pandas if available).
    Set a ``result`` variable to the value(s) you computed — it is returned in
    full (very large output is saved to WORKDIR and referenced), and every
    numeric leaf, nested or not, is recorded on the Finding keyed by its path
    (e.g. ``aug.top[0].revenue``). Blocked: network, arbitrary
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

    verdict = ctx.deps.budget.consume("python_calls")
    if not verdict.ok:
        return ToolResult(
            success=False,
            summary=f"python sandbox budget exhausted for this mission ({verdict.reason})",
            error_code="EXECUTION_LIMIT_EXCEEDED",
            retryable=False,
        )

    # A script computes over measurements, so a finding or chart passed in is read as
    # the evidence it cites — the result then cites those rows, not the summary. Live
    # 2026-10-07/08: run_python refused the prefetch finding holding the very table a
    # channel reconciliation had to sum; the model summed in prose instead and the
    # unrecorded totals failed grounding until the mission did (4 of 6 failures).
    if not evidence_ids:
        # Omitted ids meant a validation retry, after which the model typed the fetched numbers into the script
        # (live 2026-10-09 MS3-9781c608fc): the script sees the mission's evidence instead.
        evidence_ids = [
            a.id for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id) if a.artifact_type == "evidence"
        ]
    evidence_ids, unwrapped = _unwrap_findings_to_evidence_ids(ctx, list(evidence_ids))
    if unwrapped:
        _note_repaired_ids(
            ctx,
            "derived artifacts read as the evidence they cite: "
            + "; ".join(f"{aid} -> {len(eids)} evidence rows" for aid, eids in sorted(unwrapped.items())),
        )
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    try:
        compiled = compile(code, "<sandbox>", "exec")
    except SyntaxError as exc:
        return _fix_the_script(f"sandbox code has a syntax error: {exc}. Fix it and retry.")
    if typed := _typed_in_values(code, ctx):
        return _fix_the_script(
            "the script types fetched values in as literals ("
            + ", ".join(f"{v:g}" for v in typed[:5])
            + "); read them from `evidence` (each row has metric_id, value, dimensions, period_start/period_end) "
            "so the result follows the data"
        )

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
    _bind_namespace_builtins(sandbox_globals, safe_builtins)

    holder: dict[str, Any] = {}
    stdout = io.StringIO()

    def _run() -> None:
        try:
            with contextlib.redirect_stdout(stdout):
                exec(compiled, sandbox_globals)  # noqa: S102 — see ceiling note
            holder["result"] = sandbox_globals.get("result")
        except Exception as exc:  # captured, returned as a failed result below
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
        err = holder["error"]
        return _fix_the_script(
            f"sandbox code raised {type(err).__name__}: {err}. "
            f"Fix the script and retry.\n{holder.get('tb', '')[-800:]}"
        )

    result = holder.get("result")
    new_files = sorted(
        {p.name for p in workdir.iterdir()} - before
    ) if workdir.exists() else []

    if result is None and not new_files:
        return _fix_the_script(
            "sandbox script produced no output — set a `result` variable or write "
            "a file under WORKDIR, then retry."
        )

    metrics = _flatten_numbers(result, limit=_max_metrics())

    printed = stdout.getvalue().strip()
    statement_bits = [purpose.strip() or "sandbox computation"]
    spilled: list[str] = []
    if result is not None:
        text, spill = _inline_or_spill(_serialize(result), workdir, "result", "json")
        statement_bits.append(f"result={text}")
        spilled += [spill] if spill else []
    if new_files:
        statement_bits.append(f"files={', '.join(new_files)}")
    if printed:
        text, spill = _inline_or_spill(printed, workdir, "stdout", "txt")
        statement_bits.append(f"stdout={text}")
        spilled += [spill] if spill else []
    statement = " | ".join(statement_bits)

    finding_id = _write_finding(
        ctx,
        finding_type="sandbox_computation",
        statement=statement,
        evidence_ids=list(evidence_ids),
        metrics=metrics,
    )

    warnings = [f"wrote {len(new_files)} file(s) to {workdir}"] if new_files else []
    warnings += [
        f"output exceeded {_result_chars()} chars; full text in {name} under WORKDIR"
        for name in spilled
    ]
    return ToolResult(
        success=True,
        artifact_ids=[finding_id],
        summary=statement,
        provenance=_provenance(list(evidence_ids)),
        warnings=warnings,
    )


def _serialize(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _inline_or_spill(text: str, workdir: Path, stem: str, ext: str) -> tuple[str, str | None]:
    """Return ``text`` inline when it fits; otherwise write it in full under
    WORKDIR and return a head plus the file name, so nothing is lost."""
    cap = _result_chars()
    if len(text) <= cap:
        return text, None
    n = sum(1 for _ in workdir.glob(f"{stem}_*.{ext}")) + 1
    name = f"{stem}_{n}.{ext}"
    (workdir / name).write_text(text, encoding="utf-8")
    return f"{text[:cap]}… [truncated; full output in WORKDIR/{name}]", name


def _flatten_numbers(value: Any, *, limit: int, prefix: str = "") -> dict[str, float]:
    """Every numeric leaf of ``value`` keyed by its path (``a.b[0].c``)."""
    out: dict[str, float] = {}

    def walk(v: Any, path: str) -> None:
        if len(out) >= limit or isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out[path or "result"] = float(v)
        elif isinstance(v, dict):
            for k, child in v.items():
                walk(child, f"{path}.{k}" if path else str(k))
        elif isinstance(v, (list, tuple)):
            for i, child in enumerate(v):
                walk(child, f"{path}[{i}]")

    walk(value, prefix)
    return out


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
