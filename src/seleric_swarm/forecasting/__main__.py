"""CLI: ``python -m seleric_swarm.forecasting run|explain ...``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seleric_swarm.forecasting")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Compile gates/frame (dry-run prints payload)")
    run_p.add_argument("--target", required=True)
    run_p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today IST)")
    run_p.add_argument("--horizon-days", type=int, default=14)
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--chronos-url", default="")

    explain_p = sub.add_parser("explain", help="Explain a stored forecast artifact id")
    explain_p.add_argument("artifact_id")

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "explain":
        print(json.dumps(explain_forecast(args.artifact_id), indent=2, default=str))
        return 0
    return 1


def explain_forecast(artifact_id: str) -> dict:
    """Summarise a stored forecast (file path or v3 artifact id) from its payload."""
    import os
    from pathlib import Path

    path = Path(artifact_id)
    data = None
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        url = os.environ.get("DATABASE_URL") or os.environ.get("COMPOSE_DATABASE_URL") or ""
        if url:
            try:
                from sqlalchemy import create_engine, text

                engine = create_engine(url)
                with engine.begin() as conn:
                    body = conn.execute(
                        text("SELECT body FROM v3_artifacts WHERE id=:id"),
                        {"id": artifact_id},
                    ).scalar()
                if body is not None:
                    data = json.loads(body) if isinstance(body, str) else body
            except Exception as exc:  # noqa: BLE001
                return {"artifact_id": artifact_id, "found": False, "error": type(exc).__name__}
    if not isinstance(data, dict):
        return {"artifact_id": artifact_id, "found": False}
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
    targets = payload.get("targets") or []
    return {
        "artifact_id": artifact_id,
        "found": True,
        "status": payload.get("status") or payload.get("winner"),
        "cutoff": payload.get("cutoff"),
        "input_hash": payload.get("input_hash") or payload.get("content_hash"),
        "engine": payload.get("engine") or payload.get("winner"),
        "targets": [t.get("metric_id") for t in targets if isinstance(t, dict)],
        "warnings": payload.get("warnings") or [],
        "backtest_summary": payload.get("backtest_summary") or payload.get("mean_by_engine") or {},
        "note": (
            "Rebuild uses the stored input hash and the same assembler; "
            "this command reports the recorded artifact, it does not re-infer."
        ),
    }


def _cmd_run(args: argparse.Namespace) -> int:
    from seleric_swarm.forecasting.calendar import generate_calendar_features
    from seleric_swarm.forecasting.gates import cutoff_for
    from seleric_swarm.forecasting.horizon import resolve_horizon
    from seleric_swarm.forecasting.policies import default_policies
    from seleric_swarm.services.time_range import as_of_date

    policies = default_policies()
    as_of = as_of_date(args.as_of, "Asia/Kolkata")
    slots = {"horizon": {"n": args.horizon_days, "unit": "day", "period_word": "next_n"}}
    horizon = resolve_horizon(
        slots, as_of=as_of, max_horizon_days=policies.defaults.max_horizon_days
    )
    target_policy = policies.target(args.target)
    maturity = target_policy.eligibility.maturity_days if target_policy else 0
    cutoff = cutoff_for(as_of=as_of, maturity_days=maturity)
    cal = generate_calendar_features(
        [date.fromisoformat(d) for d in _iso_range(horizon.start, horizon.end)]
    )
    payload = {
        "target": args.target,
        "as_of": as_of.isoformat(),
        "cutoff": cutoff.isoformat(),
        "horizon": horizon.model_dump(mode="json"),
        "eligibility": (
            target_policy.eligibility.model_dump() if target_policy else {"status": "refused"}
        ),
        "calendar_future_sample": {k: v[:3] for k, v in cal.items()},
        "chronos_url": args.chronos_url or "(unset — ETS fallback)",
        "dry_run": bool(args.dry_run),
        "note": "Full MCP assemble requires a live mission; dry-run prints gates + calendar only.",
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _iso_range(start: date, end: date) -> list[str]:
    from datetime import timedelta

    out: list[str] = []
    d = start
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


if __name__ == "__main__":
    sys.exit(main())
