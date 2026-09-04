import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

required = [
    "README.md",
    "docs/01_SYSTEM_ARCHITECTURE.md",
    "docs/22_IMPLEMENTATION_ROADMAP.md",
    "diagrams/final_architecture.mmd",
    "config/agent_registry.example.yaml",
    "schemas/a2a_envelope.schema.json",
    "schemas/evidence_artifact.schema.json",
]

missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    raise SystemExit(f"Missing required files: {missing}")

for p in (ROOT / "schemas").glob("*.json"):
    json.loads(p.read_text())

print(f"Repository validation passed. Required files: {len(required)}; schemas valid.")
