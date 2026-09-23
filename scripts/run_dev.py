"""Start the API from the repo root without requiring PYTHONPATH.

Uses the interpreter that invoked this file. Prefer the project venv:

    .\\.venv\\Scripts\\python.exe scripts\\run_dev.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    from seleric_swarm.config.settings import get_settings

    load_dotenv(ROOT / ".env")
    get_settings.cache_clear()
    settings = get_settings()
    host = settings.api_host or os.environ.get("API_HOST") or ""
    port = settings.api_port or int(os.environ.get("API_PORT") or "0")
    if not host or not port:
        raise SystemExit("API_HOST and API_PORT must be set in the environment (or .env)")

    # --reload is disabled: on this Windows + heavy-import (dowhy/statsmodels/
    # pydantic-ai) setup, WatchFiles logs "Reloading..." but the worker never
    # actually restarts -- it keeps serving stale code indefinitely and leaks
    # a zombie process per attempted reload. Restart this script manually
    # after code changes instead.
    uvicorn.run("seleric_swarm.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
