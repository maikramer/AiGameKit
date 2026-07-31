import json
from pathlib import Path

from aigamekit_lab.compare_inspect import diff_inspect

FIX = Path(__file__).parent / "fixtures"


def test_diff_inspect_smoke() -> None:
    a = json.loads((FIX / "inspect_sample_a.json").read_text())
    b = json.loads((FIX / "inspect_sample_b.json").read_text())
    d = diff_inspect(a, b)
    assert "mesh_totals_delta" in d
    assert d["mesh_totals_delta"]["vertex_count"]["delta"] == 1000
    assert d["bones"]["common_count"] >= 1
    assert "summary" in d
