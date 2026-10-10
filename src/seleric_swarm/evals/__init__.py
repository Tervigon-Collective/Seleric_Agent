"""Behavioural eval harness (M0.4).

One public behaviour: :func:`run_suite` runs golden cases in ``replay``
(recorded MCP, deterministic; CI), ``live`` (real MCP and model; Jenkins) or
``record`` mode (run live and write the MCP cassette back), and returns a
:class:`Report`. Cases load with :func:`load_cases`.

The cassette types are public because they are the suite's durable artefacts.
The scorer and runner stay private; they are exercised through
:func:`run_suite` with an injected ``runner``.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from seleric_swarm.evals._case import EvalCase, load_cases
from seleric_swarm.evals._cassette import (
    Cassette,
    CassetteMiss,
    RecordingMcpClient,
    ReplayMcpClient,
)
from seleric_swarm.evals._report import (
    Report,
    build_report,
    load_baseline,
    render_markdown,
    write_report,
)
from seleric_swarm.evals._runner import (
    CaseRunner,
    LegacyMissionRunner,
    McpTruthResolver,
    ObservedCase,
    ObservedEvidence,
    build_mission_runtime,
    install_mcp_client,
)
from seleric_swarm.evals._scorer import (
    CaseScore,
    ResolvedTruth,
    aggregate,
    score_case,
)

__all__ = [
    "Cassette",
    "CassetteMiss",
    "EvalCase",
    "ObservedCase",
    "ObservedEvidence",
    "RecordingMcpClient",
    "ReplayMcpClient",
    "Report",
    "load_cases",
    "render_markdown",
    "run_suite",
    "write_report",
]

DEFAULT_CASSETTE_DIR = "eval/cassettes"

_CaseSource = Sequence[EvalCase] | Sequence[str | Path] | str | Path
_TruthResolver = Callable[[dict[str, Any]], Any]


def _resolve_cases(cases: _CaseSource) -> list[EvalCase]:
    if isinstance(cases, (str, Path)):
        return load_cases(cases)
    materialised = list(cases)
    if materialised and isinstance(materialised[0], EvalCase):
        return [case for case in materialised if isinstance(case, EvalCase)]
    paths = [Path(p) for p in materialised if isinstance(p, (str, Path))]
    return load_cases(paths)


async def _resolve_truths(
    case: EvalCase, resolver: _TruthResolver | None
) -> list[ResolvedTruth]:
    truths: list[ResolvedTruth] = []
    for value in case.expect.values:
        expected = value.value
        if expected is None and resolver is not None:
            resolved = resolver(value.truth)
            expected = await resolved if inspect.isawaitable(resolved) else resolved
        truths.append(
            ResolvedTruth(
                label=value.label,
                expected=expected,
                tolerance=value.tolerance,
                match=dict(value.truth),
            )
        )
    return truths


async def _call_runner(runner: CaseRunner, case: EvalCase) -> ObservedCase:
    result: Any = runner(case)
    if inspect.isawaitable(result):
        result = await result
    return result


def _build_stack(
    mode: str,
    *,
    mcp_client: Any | None,
    cassette_dir: str | Path,
    settings: Any | None,
) -> tuple[CaseRunner, RecordingMcpClient | None, _TruthResolver | None]:
    if mode == "replay":
        client = mcp_client
        if client is None:
            cassette = Cassette.load_dir(cassette_dir)
            if not cassette.entries:
                raise FileNotFoundError(
                    f"replay mode found no cassettes under {cassette_dir!r}; "
                    "record them with mode='record'"
                )
            client = ReplayMcpClient(cassette)
        runtime = build_mission_runtime(mcp_client=client, settings=settings)
        return LegacyMissionRunner(runtime), None, None

    runtime = build_mission_runtime(mcp_client=mcp_client, settings=settings)
    recorder: RecordingMcpClient | None = None
    if mode == "record":
        recorder = RecordingMcpClient(runtime.mcp)
        install_mcp_client(runtime, recorder)
    truth_client = recorder or mcp_client or runtime.mcp
    return (
        LegacyMissionRunner(runtime),
        recorder,
        McpTruthResolver(truth_client),
    )


async def run_suite(
    cases: _CaseSource,
    mode: str = "replay",
    *,
    suite: str = "golden",
    runner: CaseRunner | None = None,
    truth_resolver: _TruthResolver | None = None,
    mcp_client: Any | None = None,
    cassette_dir: str | Path = DEFAULT_CASSETTE_DIR,
    settings: Any | None = None,
    baseline: dict[str, float | None] | None = None,
    baseline_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Report:
    """Run ``cases`` and return a :class:`Report`.

    ``runner`` and ``mcp_client`` are injection seams: tests pass a fake runner;
    the default builds a mission runtime and drives ``run_v3_mission`` with the
    cassette (``replay``), the live MCP (``live``) or a recorder (``record``).
    """

    if mode not in {"replay", "live", "record"}:
        raise ValueError(f"unknown mode {mode!r}")

    resolved_cases = _resolve_cases(cases)
    if baseline is None and baseline_path is not None:
        baseline = load_baseline(baseline_path)

    recorder: RecordingMcpClient | None = None
    if runner is None:
        runner, recorder, built_truth = _build_stack(
            mode, mcp_client=mcp_client, cassette_dir=cassette_dir, settings=settings
        )
        truth_resolver = truth_resolver or built_truth

    scores: list[CaseScore] = []
    for case in resolved_cases:
        if progress is not None:
            progress(f"running {case.id} ({mode})")
        observed = await _call_runner(runner, case)
        truth_rows = await _resolve_truths(case, truth_resolver)
        scores.append(score_case(case, observed, truth_rows))

    if recorder is not None:
        target = Path(cassette_dir) / f"{suite}.json"
        recorder.save(target)
        if progress is not None:
            progress(f"wrote cassette {target}")

    report = build_report(
        suite=suite,
        mode=mode,
        score=aggregate(resolved_cases, scores),
        baseline=baseline,
        baseline_path=str(baseline_path) if baseline_path is not None else None,
    )
    if out_dir is not None:
        write_report(report, out_dir)
    return report
