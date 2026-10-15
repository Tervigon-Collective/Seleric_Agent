"""Dispatch: ``python -m seleric_swarm.diagnosis.cli <command> ...``."""

from __future__ import annotations

import sys

from seleric_swarm.diagnosis.cli import replay

COMMANDS = {"replay": replay.main}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in COMMANDS:
        print(f"usage: python -m seleric_swarm.diagnosis.cli {{{','.join(sorted(COMMANDS))}}} ...", file=sys.stderr)
        return 2
    return COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
