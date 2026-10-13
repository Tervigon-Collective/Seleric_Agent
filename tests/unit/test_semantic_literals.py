"""Tests for the semantic literal scanner."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def test_lint_semantic_literals_fails_on_new_literal():
    """Test that the semantic literal scanner fails when a new literal outside the allowlist is found."""
    # Create a temporary Python file with a forbidden literal
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        src_dir = tmpdir / "src" / "test_module"
        src_dir.mkdir(parents=True)

        # Create a test file with a forbidden literal (net_sales is in catalogue but not in allowlist for this test module)
        test_file = src_dir / "test_file.py"
        test_file.write_text("""
def some_function():
    metric_id = "net_sales"  # This should be caught
    return metric_id
""")

        # Create a minimal catalogue snapshot with net_sales
        catalogue_dir = tmpdir / "tests" / "fixtures" / "catalogue"
        catalogue_dir.mkdir(parents=True)
        catalogue_file = catalogue_dir / "snapshot.json"
        catalogue_file.write_text("""{
  "metrics": [
    {"id": "net_sales", "label": "Net Sales", "aliases": ["sales", "revenue"]},
    {"id": "gross_sales", "label": "Gross Sales", "aliases": []}
  ],
  "dimensions": [
    {"id": "channel", "aliases": [], "allowed_values": ["meta", "google"]}
  ],
  "hierarchies": [],
  "views": [],
  "brands": []
}""")

        # Create an empty allowlist
        allowlist_file = tmpdir / "scripts" / "semantic_literal_allowlist.txt"
        allowlist_file.parent.mkdir(parents=True)
        allowlist_file.write_text("")

        # Run the lint script
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "lint_semantic_literals.py"
        result = subprocess.run([
            sys.executable, str(script_path),
            "--src", str(src_dir),
            "--catalogue", str(catalogue_file),
            "--allowlist", str(allowlist_file),
        ], capture_output=True, text=True, cwd=tmpdir, check=False)

        # Should fail with exit code 1
        assert result.returncode == 1, f"Expected exit code 1, got {result.returncode}. stdout: {result.stdout}, stderr: {result.stderr}"
        assert "net_sales" in result.stderr, "Expected violation for net_sales in stderr"
        assert "VIOLATIONS" in result.stderr, "Expected VIOLATIONS in stderr"


def test_lint_semantic_literals_passes_with_allowlist():
    """Test that the semantic literal scanner passes when the literal is in the allowlist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        src_dir = tmpdir / "src" / "test_module"
        src_dir.mkdir(parents=True)

        test_file = src_dir / "test_file.py"
        test_file.write_text("""
def some_function():
    metric_id = "net_sales"  # This is allowed
    return metric_id
""")

        catalogue_dir = tmpdir / "tests" / "fixtures" / "catalogue"
        catalogue_dir.mkdir(parents=True)
        catalogue_file = catalogue_dir / "snapshot.json"
        catalogue_file.write_text("""{
  "metrics": [
    {"id": "net_sales", "label": "Net Sales", "aliases": ["sales", "revenue"]}
  ],
  "dimensions": [],
  "hierarchies": [],
  "views": [],
  "brands": []
}""")

        allowlist_file = tmpdir / "scripts" / "semantic_literal_allowlist.txt"
        allowlist_file.parent.mkdir(parents=True)
        allowlist_file.write_text("net_sales # M0.5\n")

        script_path = Path(__file__).resolve().parents[2] / "scripts" / "lint_semantic_literals.py"
        result = subprocess.run([
            sys.executable, str(script_path),
            "--src", str(src_dir),
            "--catalogue", str(catalogue_file),
            "--allowlist", str(allowlist_file),
        ], capture_output=True, text=True, cwd=tmpdir, check=False)

        # Should pass with exit code 0
        assert result.returncode == 0, f"Expected exit code 0, got {result.returncode}. stderr: {result.stderr}"
        assert "No violations found" in result.stdout


def test_lint_semantic_literals_allows_machine_formats():
    """Test that machine-format parsing terms (ISO, date, json, etc.) are allowed in adapters."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        src_dir = tmpdir / "src" / "adapters"
        src_dir.mkdir(parents=True)

        test_file = src_dir / "parser.py"
        test_file.write_text("""
def parse_date(value: str):
    # ISO date parsing is allowed in adapters
    return value
""")

        catalogue_dir = tmpdir / "tests" / "fixtures" / "catalogue"
        catalogue_dir.mkdir(parents=True)
        catalogue_file = catalogue_dir / "snapshot.json"
        catalogue_file.write_text("""{
  "metrics": [],
  "dimensions": [],
  "hierarchies": [],
  "views": [],
  "brands": []
}""")

        allowlist_file = tmpdir / "scripts" / "semantic_literal_allowlist.txt"
        allowlist_file.parent.mkdir(parents=True)
        allowlist_file.write_text("ISO # adapters\ndate # adapters\njson # adapters\n")

        script_path = Path(__file__).resolve().parents[2] / "scripts" / "lint_semantic_literals.py"
        result = subprocess.run([
            sys.executable, str(script_path),
            "--src", str(src_dir),
            "--catalogue", str(catalogue_file),
            "--allowlist", str(allowlist_file),
        ], capture_output=True, text=True, cwd=tmpdir, check=False)

        # Should pass - machine formats in adapters are allowed
        assert result.returncode == 0, f"Expected exit code 0, got {result.returncode}. stderr: {result.stderr}"