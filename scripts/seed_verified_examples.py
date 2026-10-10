"""Seed ``eval/verified/*.yaml`` from the M0.4 golden cases.

Each golden case becomes one :class:`VerifiedExample` with a ``verified-`` id
prefix so it never collides with the eval case id (the eval runner fails on a
collision — eval cases and examples stay separate). The spec is the case's
partial ``expect.spec``; re-validation against the read model drops it the moment
a metric or dimension id disappears.

Usage:
    python scripts/seed_verified_examples.py [--golden eval/golden] [--out eval/verified/seed.yaml]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.evals._case import load_cases
from seleric_swarm.understand.examples import VerifiedExample


def build_example(case: Any) -> VerifiedExample:
    tags = list(case.tags or ())
    return VerifiedExample(
        id=f"verified-{case.id}",
        question=case.turns[0],
        spec=dict(case.expect.spec),
        template_id=tags[0] if tags else "lookup",
        verified_by="golden-seed",
        verified_at=datetime.now(UTC).isoformat(),
        catalogue_version="",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", default="eval/golden")
    parser.add_argument("--out", default="eval/verified/seed.yaml")
    args = parser.parse_args(argv)

    cases = load_cases(args.golden)
    examples = [build_example(case).model_dump(mode="json") for case in cases]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump({"examples": examples}, sort_keys=False), encoding="utf-8"
    )
    print(f"wrote {len(examples)} examples to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
