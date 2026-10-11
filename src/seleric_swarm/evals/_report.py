"""Eval report and baseline comparison (M0.4).

The report is the artefact a PR reviewer and Jenkins read. ``--compare`` folds
in a baseline: a metric that got *worse* than the baseline is a regression and
fails the gate. Metrics that are ``None`` for every case are not compared, so a
suite that does not yet exercise spec accuracy is not blocked by it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seleric_swarm.evals._scorer import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    SuiteScore,
)

COMPARISON_TOLERANCE = 1e-9


class Regression(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str
    scope: str
    baseline: float
    current: float
    direction: Literal["drop", "rise"]


class BaselineComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    compared: bool = False
    baseline_path: str | None = None
    passed: bool = True
    regressions: tuple[Regression, ...] = ()


class Report(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    mode: str
    generated_at: str
    case_count: int
    score: SuiteScore
    comparison: BaselineComparison = Field(default_factory=BaselineComparison)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=False)


def compare_baseline(
    score: SuiteScore, baseline: dict[str, float | None]
) -> list[Regression]:
    regressions: list[Regression] = []
    for metric, reference in baseline.items():
        if metric not in HIGHER_IS_BETTER and metric not in LOWER_IS_BETTER:
            continue
        current = score.overall.get(metric)
        if reference is None or current is None:
            continue
        if metric in HIGHER_IS_BETTER:
            if current < reference - COMPARISON_TOLERANCE:
                regressions.append(
                    Regression(
                        metric=metric,
                        scope="overall",
                        baseline=float(reference),
                        current=float(current),
                        direction="drop",
                    )
                )
        elif current > reference + COMPARISON_TOLERANCE:
            regressions.append(
                Regression(
                    metric=metric,
                    scope="overall",
                    baseline=float(reference),
                    current=float(current),
                    direction="rise",
                )
            )
    return regressions


def build_report(
    *,
    suite: str,
    mode: str,
    score: SuiteScore,
    baseline: dict[str, float | None] | None = None,
    baseline_path: str | None = None,
) -> Report:
    comparison = BaselineComparison()
    if baseline is not None:
        regressions = compare_baseline(score, baseline)
        comparison = BaselineComparison(
            compared=True,
            baseline_path=baseline_path,
            passed=not regressions,
            regressions=tuple(regressions),
        )
    return Report(
        suite=suite,
        mode=mode,
        generated_at=datetime.now(UTC).isoformat(),
        case_count=len(score.cases),
        score=score,
        comparison=comparison,
    )


def _format(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def render_markdown(report: Report) -> str:
    lines = [
        f"# Eval report — {report.suite} ({report.mode})",
        "",
        f"- cases: {report.case_count}",
        f"- generated: {report.generated_at}",
        "",
        "## Overall",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for metric, value in report.score.overall.items():
        lines.append(f"| {metric} | {_format(value)} |")

    if report.score.by_tag:
        tags = sorted(report.score.by_tag)
        metrics = list(report.score.overall)
        lines += ["", "## By tag", "", "| metric | " + " | ".join(tags) + " |"]
        lines.append("|---|" + "---|" * len(tags))
        for metric in metrics:
            row = [f"{metric}"] + [_format(report.score.by_tag[t].get(metric)) for t in tags]
            lines.append("| " + " | ".join(row) + " |")

    lines += ["", "## Cases", "", "| id | tags | status_ok | value_correctness | spec_accuracy | revisions |"]
    lines.append("|---|---|---|---|---|---|")
    for case in report.score.cases:
        case_metrics = case.metrics
        lines.append(
            "| {id} | {tags} | {status} | {value} | {spec} | {revisions} |".format(
                id=case.id,
                tags=",".join(case.tags),
                status=_format(case_metrics.get("status_ok")),
                value=_format(case_metrics.get("value_correctness")),
                spec=_format(case_metrics.get("spec_accuracy")),
                revisions=_format(case_metrics.get("revisions")),
            )
        )

    lines += ["", "## Diagnostics", ""]
    for case in report.score.cases:
        obs = case.details.get("observed") or {}
        shadow = obs.get("v2_shadow")
        spec_info = f"{case.details.get('spec_matched', '-')}/{case.details.get('spec_total', '-')} from {obs.get('spec_source')}"
        truths = "; ".join(f"{t['match'].get('metric_id')}={t['expected']}" for t in case.details.get("truths") or [])
        mission = obs.get("v2_mission")
        lines.append(f"- `{case.id}`: status {case.details.get('status')}; spec {spec_info}; evidence {obs.get('evidence_metrics')}; "
                     f"truth {truths or '-'}; v2_mission {json.dumps(mission, default=str)[:300]}; v2_shadow {json.dumps(shadow, default=str)[:200]}")

    comparison = report.comparison
    lines += ["", "## Baseline", ""]
    if not comparison.compared:
        lines.append("_not compared_")
    else:
        lines.append(f"- baseline: {comparison.baseline_path}")
        lines.append(f"- passed: {comparison.passed}")
        for regression in comparison.regressions:
            lines.append(
                f"- REGRESSION {regression.metric} ({regression.scope}): "
                f"{regression.baseline:.4f} -> {regression.current:.4f}"
            )
    lines.append("")
    return "\n".join(lines)


def write_report(report: Report, out_dir: str | Path) -> tuple[Path, Path]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    md_path = directory / "report.md"
    json_path.write_text(report.to_json() + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def load_baseline(path: str | Path) -> dict[str, float | None]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path}: baseline must be a JSON object")
    return {str(k): (float(v) if isinstance(v, (int, float)) else None) for k, v in data.items()}
