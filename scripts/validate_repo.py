import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

required = [
    "README.md",
    "docs/00_PROJECT_CHARTER.md",
    "docs/CURRENT_ARCHITECTURE.md",
    "diagrams/evidence_flow.mmd",
    "schemas/evidence_artifact.schema.json",
]

missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    raise SystemExit(f"Missing required files: {missing}")

for p in (ROOT / "schemas").glob("*.json"):
    json.loads(p.read_text())

print(f"Repository validation passed. Required files: {len(required)}; schemas valid.")
