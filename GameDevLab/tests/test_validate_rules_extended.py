"""Testes para as regras estendidas (Stage 10 do redesign da pipeline)."""

from __future__ import annotations

from gamedev_lab.validate_rules import evaluate_inspect_rules


def _base_inspect(**glb_meta: object) -> dict:
    return {
        "mesh_totals": {"vertex_count": 1000, "face_count": 2000},
        "world_bounds": {"min": [-1, 0, -1], "max": [1, 2, 1]},
        "armatures": [],
        "actions": [],
        "meshes": [{"name": "Body"}],
        "glb_meta": dict(glb_meta),
    }


def test_v_per_tri_pass() -> None:
    insp = _base_inspect(v_per_tri=0.5)
    rules = {"mesh_totals": {"v_per_tri": {"max": 1.6}}}
    ok, fails, _ = evaluate_inspect_rules(insp, rules)
    assert ok, fails


def test_v_per_tri_fail() -> None:
    insp = _base_inspect(v_per_tri=3.0)
    rules = {"mesh_totals": {"v_per_tri": {"max": 1.6}}}
    ok, fails, _ = evaluate_inspect_rules(insp, rules)
    assert not ok
    assert any("v_per_tri" in f for f in fails)


def test_attributes_required() -> None:
    rules = {"attributes_required": ["POSITION", "NORMAL", "TANGENT"]}
    insp_ok = _base_inspect(attributes_present=["POSITION", "NORMAL", "TANGENT", "TEXCOORD_0"])
    ok, _, _ = evaluate_inspect_rules(insp_ok, rules)
    assert ok

    insp_bad = _base_inspect(attributes_present=["POSITION", "NORMAL"])
    ok, fails, _ = evaluate_inspect_rules(insp_bad, rules)
    assert not ok
    assert any("TANGENT" in f for f in fails)


def test_texture_format_ktx2() -> None:
    rules = {"texture_format": "ktx2"}
    insp_ok = _base_inspect(
        texture_mime_types=["image/ktx2"],
        extensions_used=["KHR_texture_basisu"],
    )
    ok, _, _ = evaluate_inspect_rules(insp_ok, rules)
    assert ok

    insp_bad = _base_inspect(texture_mime_types=["image/png"])
    ok, fails, _ = evaluate_inspect_rules(insp_bad, rules)
    assert not ok


def test_compression_meshopt() -> None:
    rules = {"compression": "meshopt"}
    insp_ok = _base_inspect(extensions_used=["EXT_meshopt_compression"])
    ok, _, _ = evaluate_inspect_rules(insp_ok, rules)
    assert ok

    insp_bad = _base_inspect(extensions_used=[])
    ok, fails, _ = evaluate_inspect_rules(insp_bad, rules)
    assert not ok


def test_origin_y_min_pass() -> None:
    rules = {"origin": {"y_min": {"near": 0.0, "tol": 0.05}}}
    insp = _base_inspect(world_bounds_y_min=0.01)
    ok, _, _ = evaluate_inspect_rules(insp, rules)
    assert ok


def test_origin_y_min_fail() -> None:
    rules = {"origin": {"y_min": {"near": 0.0, "tol": 0.01}}}
    insp = _base_inspect(world_bounds_y_min=-0.5)
    ok, fails, _ = evaluate_inspect_rules(insp, rules)
    assert not ok
    assert any("origin" in f for f in fails)


def test_face_count_per_category() -> None:
    rules = {"face_count": {"max_per_category": {"humanoid": 1500, "weapon": 7200}}}
    insp = _base_inspect()  # face_count=2000
    ok, fails, _ = evaluate_inspect_rules(insp, rules, category="humanoid")
    assert not ok  # 2000 > 1500
    assert any("humanoid" in f for f in fails)

    ok, fails, _ = evaluate_inspect_rules(insp, rules, category="weapon")
    assert ok  # 2000 <= 7200
