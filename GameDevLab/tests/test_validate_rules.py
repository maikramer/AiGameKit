"""Regras de validação GLB — fallback glb_meta quando bpy inspect ausente."""

from __future__ import annotations

from gamedev_lab.validate_rules import evaluate_inspect_rules


def _meta(**kwargs):
    base = {
        "attributes_present": ["NORMAL", "POSITION", "TEXCOORD_0"],
        "extensions_used": [],
        "texture_mime_types": ["image/png"],
        "mesh_count": 1,
        "primitive_count": 1,
        "vertex_count_total": 1000,
        "triangle_count_total": 500,
        "face_count_total": 500,
        "v_per_tri": 2.0,
        "world_bounds_y_min": 0.0,
    }
    base.update(kwargs)
    return base


def test_no_bpy_inspect_uses_glb_meta_counts() -> None:
    """Master pipeline passa --no-bpy-inspect; contagens vêm do parser."""
    inspect = {"glb_meta": _meta()}
    rules = {
        "mesh_totals": {"vertex_count": {"min": 8, "max": 200000}},
        "meshes_min": 1,
        "face_count": {"max_per_category": {"building": 28800}},
    }
    ok, failures, _ = evaluate_inspect_rules(inspect, rules, category="building")
    assert ok, failures
    assert failures == []


def test_no_bpy_false_positive_meshes_zero_fixed() -> None:
    """Regressão: meshes=[] + sem fallback → meshes: 0 < meshes_min."""
    inspect = {"meshes": [], "mesh_totals": {}, "glb_meta": _meta(mesh_count=1)}
    rules = {"meshes_min": 1}
    ok, failures, _ = evaluate_inspect_rules(inspect, rules)
    assert ok, failures


def test_bpy_inspect_preferred_over_meta() -> None:
    inspect = {
        "meshes": [{"name": "A"}, {"name": "B"}],
        "mesh_totals": {"vertex_count": 42, "face_count": 10},
        "glb_meta": _meta(vertex_count_total=9999, mesh_count=1),
    }
    rules = {
        "mesh_totals": {"vertex_count": {"min": 40, "max": 50}},
        "meshes_min": 2,
    }
    ok, failures, _ = evaluate_inspect_rules(inspect, rules)
    assert ok, failures


def test_real_content_failures_still_reported() -> None:
    inspect = {"glb_meta": _meta()}
    rules = {
        "attributes_required": ["POSITION", "NORMAL", "TEXCOORD_0", "TANGENT"],
        "texture_format": "ktx2",
        "compression": "meshopt",
    }
    ok, failures, _ = evaluate_inspect_rules(inspect, rules)
    assert not ok
    joined = " ".join(failures)
    assert "TANGENT" in joined
    assert "ktx2" in joined.lower() or "image/ktx2" in joined
    assert "meshopt" in joined.lower()
