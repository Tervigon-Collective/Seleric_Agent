from __future__ import annotations

from pathlib import Path

import pytest

from seleric_swarm.evals import load_cases


def _write(tmp_path: Path, text: str, name: str = "cases.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_and_validates(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
cases:
  - id: a
    as_of: "2026-09-03"
    turns: ["question one"]
    tags: [lookup]
    expect:
      status: completed
      values:
        - label: net sales
          truth: {metric_id: metric.net_sales}
          value: 10.0
          tolerance: 0.5
""",
    )
    cases = load_cases(path)
    assert [case.id for case in cases] == ["a"]
    assert cases[0].tags == ("lookup",)
    assert cases[0].expect.values[0].tolerance == 0.5


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
cases:
  - id: dup
    turns: ["one"]
  - id: dup
    turns: ["two"]
""",
    )
    with pytest.raises(ValueError, match="duplicate case id"):
        load_cases(path)


def test_missing_turn_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "cases:\n  - id: a\n    expect: {}\n")
    with pytest.raises(ValueError):
        load_cases(path)


def test_loads_directory_sorted(tmp_path: Path) -> None:
    _write(tmp_path, "cases:\n  - id: b\n    turns: [q]\n", name="b.yaml")
    _write(tmp_path, "cases:\n  - id: a\n    turns: [q]\n", name="a.yaml")
    assert [case.id for case in load_cases(tmp_path)] == ["a", "b"]
