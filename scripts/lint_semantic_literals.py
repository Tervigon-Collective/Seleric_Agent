"""Semantic literal scanner.

Scans Python source files for string literals that match catalogue metric IDs,
dimension IDs, or dimension values. These should not appear in source code;
they must be resolved through the SemanticPort at runtime.

Usage:
    python scripts/lint_semantic_literals.py [--allowlist PATH] [--catalogue PATH] [--src PATH]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def _as_records(node: object) -> list[tuple[str | None, dict]]:
    """Normalise a catalogue collection into ``(key, record)`` pairs.

    The fixture uses a list of objects for metrics and a dict keyed by id for
    hierarchies. Both shapes are accepted. A bare string is an id with no body.
    """
    if isinstance(node, dict):
        return [(str(key), value if isinstance(value, dict) else {}) for key, value in node.items()]
    if isinstance(node, list):
        records: list[tuple[str | None, dict]] = []
        for item in node:
            if isinstance(item, dict):
                records.append((None, item))
            elif isinstance(item, str) and item:
                records.append((item, {}))
        return records
    return []


def _add_id(terms: set[str], record: dict, key: str | None) -> None:
    ident = record.get("id") or key
    if ident:
        terms.add(str(ident))


def load_catalogue_terms(catalogue_path: Path) -> set[str]:
    """Extract metric ids, dimension ids, and dimension values from the catalogue."""
    terms: set[str] = set()

    if not catalogue_path.exists():
        print(f"Warning: Catalogue snapshot not found at {catalogue_path}", file=sys.stderr)
        return terms

    try:
        data = json.loads(catalogue_path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error reading catalogue snapshot: {e}", file=sys.stderr)
        return terms

    if not isinstance(data, dict):
        print(f"Error reading catalogue snapshot: expected an object in {catalogue_path}", file=sys.stderr)
        return terms

    for key, metric in _as_records(data.get("metrics", [])):
        _add_id(terms, metric, key)
        for alias in metric.get("aliases", []):
            if alias:
                terms.add(str(alias))
        label = metric.get("label")
        if label:
            terms.add(str(label))

    for key, dim in _as_records(data.get("dimensions", [])):
        _add_id(terms, dim, key)
        for alias in dim.get("aliases", []):
            if alias:
                terms.add(str(alias))
        for value in dim.get("allowed_values", []):
            if value:
                terms.add(str(value))

    for key, record in _as_records(data.get("hierarchies", [])):
        _add_id(terms, record, key)
    for key, record in _as_records(data.get("views", [])):
        _add_id(terms, record, key)
    for key, record in _as_records(data.get("brands", [])):
        _add_id(terms, record, key)

    return terms


def load_allowlist(allowlist_path: Path) -> dict[str, str]:
    """Load the allowlist mapping literal -> owning module id."""
    allowlist: dict[str, str] = {}
    if not allowlist_path.exists():
        return allowlist

    for line_num, line in enumerate(allowlist_path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: literal # module_id
        parts = line.split("#", 1)
        literal = parts[0].strip()
        module_id = parts[1].strip() if len(parts) > 1 else "unknown"
        if literal:
            allowlist[literal] = module_id
    return allowlist


class LiteralVisitor(ast.NodeVisitor):
    """AST visitor that collects all string literals in the source."""

    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.literals: list[tuple[int, str]] = []  # (line_number, literal)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.literals.append((node.lineno, node.value))
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        # Don't analyze f-string parts individually; the whole f-string is one constant-ish thing
        # but we can't easily extract the static parts. Skip for now.
        self.generic_visit(node)


def scan_file(filepath: Path, terms: set[str], allowlist: dict[str, str]) -> list[tuple[int, str, str]]:
    """Scan a single Python file for forbidden literals.
    Returns list of (line_number, literal, module_id) for violations.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    visitor = LiteralVisitor(filepath)
    visitor.visit(tree)

    violations: list[tuple[int, str, str]] = []
    for line_num, literal in visitor.literals:
        # Exact match only. Terms shorter than 3 characters are sample values
        # ("a", "b") and ordinary words, not catalogue ids.
        if len(literal) < 3 or literal not in terms or literal in allowlist:
            continue
        module_id = "unknown"
        try:
            rel = filepath.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            rel = filepath
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "src":
            module_id = parts[1]
        elif parts:
            module_id = parts[0]
        violations.append((line_num, literal, module_id))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for semantic literals in source code")
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path("scripts/semantic_literal_allowlist.txt"),
        help="Path to allowlist file",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        default=Path("tests/fixtures/catalogue/snapshot.json"),
        help="Path to catalogue snapshot JSON",
    )
    parser.add_argument(
        "--src",
        type=Path,
        default=Path("src"),
        help="Root source directory to scan",
    )
    args = parser.parse_args()

    print(f"Loading catalogue terms from {args.catalogue}...")
    terms = load_catalogue_terms(args.catalogue)
    print(f"  Found {len(terms)} catalogue terms")

    print(f"Loading allowlist from {args.allowlist}...")
    allowlist = load_allowlist(args.allowlist)
    print(f"  Allowlist has {len(allowlist)} entries")

    print(f"Scanning {args.src}...")
    all_violations: list[tuple[Path, int, str, str]] = []

    for py_file in args.src.rglob("*.py"):
        # Skip __pycache__ and test fixtures
        if "__pycache__" in py_file.parts:
            continue
        violations = scan_file(py_file, terms, allowlist)
        for line_num, literal, module_id in violations:
            all_violations.append((py_file, line_num, literal, module_id))

    if all_violations:
        print("\nVIOLATIONS (literal not in allowlist):", file=sys.stderr)
        for filepath, line_num, literal, module_id in all_violations:
            # Truncate long literals for display
            display = literal if len(literal) <= 80 else literal[:77] + "..."
            print(f"{filepath}:{line_num} {display}  # {module_id}", file=sys.stderr)
        print(f"\nTotal violations: {len(all_violations)}", file=sys.stderr)
        return 1

    print("No violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())