"""Import contract of the diagnosis package (plan P-2 / D-28, tasks P1.8 and P1.10).

domain <- ports, libs <- core (detect, graph, operators, ledger, scheduler, trace, report) <- adapters, cli.
A layer imports only from its own rank or below. Only ``adapters`` and ``cli`` may import the rest of
``seleric_swarm``. Only ``libs`` may import the third-party analysis libraries. No string constant in the package
may be a catalogue metric id (the engine learns the metrics from the graph and the catalogue, never from its source).
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "src" / "seleric_swarm" / "diagnosis"
PREFIX = "seleric_swarm.diagnosis"

RANK = {
    "domain": 0,
    "ports": 1, "libs": 1,
    "detect": 2, "graph": 2, "operators": 2, "ledger": 2, "scheduler": 2, "trace": 2, "report": 2,
    "adapters": 3, "cli": 3,
}
OUTSIDE_ALLOWED = {"adapters", "cli"}
ANALYSIS_LIBS = {"dowhy", "ruptures", "statsmodels", "networkx", "causalpy", "tigramite", "causallearn"}


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root.parent.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, module: str) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")
                up = up[: len(up) - (node.level - 1)]
                base = ".".join([*up, base] if base else up)
            yield base


def violations(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = _module_name(path, root)
        parts = module.split(".")
        layer = parts[2] if len(parts) > 2 else ""
        if layer not in RANK:
            continue
        for target in _imports(path, module):
            top = target.split(".")[0]
            if top in ANALYSIS_LIBS and layer != "libs":
                found.append(f"{module} imports {target}: analysis libraries belong in libs/")
            if target.startswith(PREFIX + "."):
                other = target.split(".")[2]
                if other in RANK and RANK[other] > RANK[layer]:
                    found.append(f"{module} ({layer}) imports {target} ({other}): upward import")
            elif top == "seleric_swarm" and target != "seleric_swarm" and layer not in OUTSIDE_ALLOWED:
                found.append(f"{module} ({layer}) imports {target}: only adapters/cli may leave the package")
    return found


def test_package_respects_the_layering():
    names = {_module_name(p, PACKAGE) for p in PACKAGE.rglob("*.py")}
    assert "seleric_swarm.diagnosis.graph.loader" in names and "seleric_swarm.diagnosis.domain.graph" in names
    assert violations(PACKAGE) == []


def test_the_checker_catches_each_kind_of_violation(tmp_path):
    root = tmp_path / "seleric_swarm" / "diagnosis"
    for layer in ("domain", "ports", "operators", "adapters"):
        (root / layer).mkdir(parents=True)
        (root / layer / "__init__.py").write_text("")
    (root / "domain" / "bad_up.py").write_text("from seleric_swarm.diagnosis.ports import graph\n")
    (root / "domain" / "bad_out.py").write_text("import seleric_swarm.toolsets.diagnosis\n")
    (root / "operators" / "bad_lib.py").write_text("import statsmodels.api\nfrom dowhy import gcm\n")
    (root / "ports" / "bad_rel.py").write_text("from ..operators import registry\n")
    (root / "adapters" / "fine.py").write_text(
        "import seleric_swarm.toolsets.diagnosis\nfrom seleric_swarm.diagnosis.domain import types\n"
    )
    found = violations(root)
    assert len(found) == 5, found
    assert not any("adapters.fine" in v for v in found)


def _string_constants(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def test_package_source_hardcodes_no_catalogue_names():
    graph = json.loads((ROOT / "src/seleric_swarm/causal/data/dependency_graph.json").read_text(encoding="utf-8"))
    ids = {n["id"] for n in graph["nodes"] if len(n["id"]) > 3}
    assert len(ids) > 200, "graph read no metric ids"
    for path in sorted(PACKAGE.rglob("*.py")):
        hits = sorted(set(_string_constants(path)) & ids)
        assert hits == [], f"{path.relative_to(ROOT)} hardcodes catalogue metric ids: {hits}"


# P-4 / D-30: the core is deterministic by construction. Time and randomness come from the Runtime port.
NONDETERMINISTIC_CALLS = {
    "datetime.now", "datetime.utcnow", "datetime.datetime.now", "datetime.datetime.utcnow", "date.today",
    "datetime.date.today", "time.time", "time.monotonic", "time.perf_counter", "time.time_ns",
}
# Seeded generators, and saving/restoring the global state (what libs/ must do around libraries that only seed globally).
SEEDED_NUMPY = {"default_rng", "Generator", "SeedSequence", "get_state", "set_state", "getstate", "setstate"}


def determinism_violations(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = _module_name(path, root)
        parts = module.split(".")
        layer = parts[2] if len(parts) > 2 else ""
        if layer in OUTSIDE_ALLOWED or layer not in RANK:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if layer != "libs" and isinstance(node, ast.Import) and any(a.name.split(".")[0] == "random" for a in node.names):
                found.append(f"{module} imports random")
            if layer != "libs" and isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "random":
                found.append(f"{module} imports from random")
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name in NONDETERMINISTIC_CALLS:
                    found.append(f"{module} calls {name}: ask the Runtime")
                pieces = name.split(".")
                if "random" in pieces[:-1] and pieces[-1] not in SEEDED_NUMPY:
                    found.append(f"{module} calls {name}: draw from Runtime.rng(stream)")
    return found


def test_the_core_reads_no_clock_and_no_global_randomness():
    assert determinism_violations(PACKAGE) == []


def test_the_determinism_checker_catches_each_kind(tmp_path):
    root = tmp_path / "seleric_swarm" / "diagnosis"
    for layer in ("operators", "adapters"):
        (root / layer).mkdir(parents=True)
        (root / layer / "__init__.py").write_text("")
    (root / "operators" / "bad.py").write_text(
        "import random\nimport time\nimport numpy as np\nfrom datetime import datetime\n"
        "a = datetime.now()\nb = time.time()\nc = np.random.rand(3)\nd = np.random.default_rng(1)\n"
    )
    (root / "adapters" / "fine.py").write_text("import time\nx = time.time()\n")
    found = determinism_violations(root)
    assert len(found) == 4, found
