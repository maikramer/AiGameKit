import json
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from gamedev_lab.validate_rules import evaluate_inspect_rules, load_rules_file

FIX = Path(__file__).parent / "fixtures"


def test_load_rules_yaml(tmp_path: Path) -> None:
    p = tmp_path / "r.yaml"
    p.write_text("mesh_totals:\n  vertex_count:\n    max: 100\n", encoding="utf-8")
    r = load_rules_file(p)
    assert r["mesh_totals"]["vertex_count"]["max"] == 100


def test_evaluate_pass() -> None:
    insp = json.loads((FIX / "inspect_sample_a.json").read_text())
    rules = {
        "mesh_totals": {"vertex_count": {"min": 100, "max": 2000}},
        "bones_contain": ["Hips"],
    }
    ok, failures, _details = evaluate_inspect_rules(insp, rules)
    assert ok
    assert not failures


def test_evaluate_fail_vertex() -> None:
    insp = json.loads((FIX / "inspect_sample_a.json").read_text())
    rules = {"mesh_totals": {"vertex_count": {"min": 5000}}}
    ok, failures, _details = evaluate_inspect_rules(insp, rules)
    assert not ok
    assert any("vertex_count" in f for f in failures)


def test_evaluate_fail_bone() -> None:
    insp = json.loads((FIX / "inspect_sample_a.json").read_text())
    rules = {"bones_contain": ["MissingBone"]}
    ok, _failures, _ = evaluate_inspect_rules(insp, rules)
    assert not ok
