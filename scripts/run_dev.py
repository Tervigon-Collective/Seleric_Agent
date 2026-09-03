"""Start the API from the repo root without requiring PYTHONPATH.

Uses the interpreter that invoked this file. Prefer the project venv:

    .\\.venv\\Scripts\\python.exe scripts\\run_dev.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    import uvicorn

    uvicorn.run(
        "seleric_swarm.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(ROOT / "src"), str(ROOT / "config"), str(ROOT / "prompts")],
        reload_includes=[".env"],
    )


if __name__ == "__main__":
    main()
