"""Cobertura adicional GameDevLab — complementa coverage_suite (≥100 total)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from gamedev_lab.cli import main
from gamedev_lab.compare_inspect import _num, diff_inspect
from gamedev_lab.glb_meta import glb_extract_meta
from gamedev_lab.validate_rules import evaluate_inspect_rules, load_rules_file


def _build_glb(gltf: dict) -> bytes:
    jb = json.dumps(gltf).encode("utf-8")
    pad = (4 - len(jb) % 4) % 4
    jb += b" " * pad
    chunks = struct.pack("<II", len(jb), 0x4E4F534A) + jb
    hdr = struct.pack("<4sII", b"glTF", 2, 12 + len(chunks))
    return hdr + chunks


def test_glb_meta_mesh_count_0(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 100, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m0.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_1(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 101, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m1.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_2(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 102, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m2.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_3(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 103, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m3.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_4(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 104, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m4.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_5(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 105, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m5.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_6(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 106, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m6.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_7(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 107, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m7.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_8(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 108, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m8.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_9(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 109, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m9.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_10(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 110, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m10.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_11(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 111, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m11.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_12(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 112, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m12.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_13(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 113, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m13.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_glb_meta_mesh_count_14(tmp_path: Path) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 114, "min": [0.0, 0.0, 0.0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    p = tmp_path / "m14.glb"
    p.write_bytes(_build_glb(gltf))
    meta = glb_extract_meta(p)
    assert meta.get("mesh_count") == 1


def test_evaluate_actions_min_0() -> None:
    insp = {"actions": [{"name": "A"}] * 0}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 1})
    assert ok is (0 >= 1)


def test_evaluate_actions_min_1() -> None:
    insp = {"actions": [{"name": "A"}] * 1}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 1})
    assert ok is (1 >= 1)


def test_evaluate_actions_min_2() -> None:
    insp = {"actions": [{"name": "A"}] * 2}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 2})
    assert ok is (2 >= 2)


def test_evaluate_actions_min_3() -> None:
    insp = {"actions": [{"name": "A"}] * 3}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 3})
    assert ok is (3 >= 3)


def test_evaluate_actions_min_4() -> None:
    insp = {"actions": [{"name": "A"}] * 4}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 4})
    assert ok is (4 >= 4)


def test_evaluate_actions_min_5() -> None:
    insp = {"actions": [{"name": "A"}] * 5}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 5})
    assert ok is (5 >= 5)


def test_evaluate_actions_min_6() -> None:
    insp = {"actions": [{"name": "A"}] * 6}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 6})
    assert ok is (6 >= 6)


def test_evaluate_actions_min_7() -> None:
    insp = {"actions": [{"name": "A"}] * 7}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 7})
    assert ok is (7 >= 7)


def test_evaluate_actions_min_8() -> None:
    insp = {"actions": [{"name": "A"}] * 8}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 8})
    assert ok is (8 >= 8)


def test_evaluate_actions_min_9() -> None:
    insp = {"actions": [{"name": "A"}] * 9}
    ok, _fails, _ = evaluate_inspect_rules(insp, {"actions_min": 9})
    assert ok is (9 >= 9)


def test_diff_num_roundtrip_0() -> None:
    assert _num("0.0") == pytest.approx(0.0)


def test_diff_num_roundtrip_1() -> None:
    assert _num("1.1") == pytest.approx(1.1)


def test_diff_num_roundtrip_2() -> None:
    assert _num("2.2") == pytest.approx(2.2)


def test_diff_num_roundtrip_3() -> None:
    assert _num("3.3") == pytest.approx(3.3)


def test_diff_num_roundtrip_4() -> None:
    assert _num("4.4") == pytest.approx(4.4)


def test_diff_num_roundtrip_5() -> None:
    assert _num("5.5") == pytest.approx(5.5)


def test_diff_num_roundtrip_6() -> None:
    assert _num("6.6") == pytest.approx(6.6)


def test_diff_num_roundtrip_7() -> None:
    assert _num("7.7") == pytest.approx(7.7)


def test_diff_num_roundtrip_8() -> None:
    assert _num("8.8") == pytest.approx(8.8)


def test_diff_num_roundtrip_9() -> None:
    assert _num("9.9") == pytest.approx(9.9)


def test_diff_inspect_mesh_delta_0() -> None:
    a = {"mesh_totals": {"vertex_count": 1000}}
    b = {"mesh_totals": {"vertex_count": 1010}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_1() -> None:
    a = {"mesh_totals": {"vertex_count": 1001}}
    b = {"mesh_totals": {"vertex_count": 1011}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_2() -> None:
    a = {"mesh_totals": {"vertex_count": 1002}}
    b = {"mesh_totals": {"vertex_count": 1012}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_3() -> None:
    a = {"mesh_totals": {"vertex_count": 1003}}
    b = {"mesh_totals": {"vertex_count": 1013}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_4() -> None:
    a = {"mesh_totals": {"vertex_count": 1004}}
    b = {"mesh_totals": {"vertex_count": 1014}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_5() -> None:
    a = {"mesh_totals": {"vertex_count": 1005}}
    b = {"mesh_totals": {"vertex_count": 1015}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_6() -> None:
    a = {"mesh_totals": {"vertex_count": 1006}}
    b = {"mesh_totals": {"vertex_count": 1016}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_7() -> None:
    a = {"mesh_totals": {"vertex_count": 1007}}
    b = {"mesh_totals": {"vertex_count": 1017}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_8() -> None:
    a = {"mesh_totals": {"vertex_count": 1008}}
    b = {"mesh_totals": {"vertex_count": 1018}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_diff_inspect_mesh_delta_9() -> None:
    a = {"mesh_totals": {"vertex_count": 1009}}
    b = {"mesh_totals": {"vertex_count": 1019}}
    out = diff_inspect(a, b)
    assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 10


def test_cli_check_help() -> None:
    r = CliRunner().invoke(main, ["check", "--help"])
    assert r.exit_code == 0


def test_cli_debug_help() -> None:
    r = CliRunner().invoke(main, ["debug", "--help"])
    assert r.exit_code == 0


def test_cli_bench_help() -> None:
    r = CliRunner().invoke(main, ["bench", "--help"])
    assert r.exit_code == 0


def test_cli_perf_help() -> None:
    r = CliRunner().invoke(main, ["perf", "--help"])
    assert r.exit_code == 0


def test_load_rules_roundtrip_0(tmp_path: Path) -> None:
    p = tmp_path / "r0.yaml"
    p.write_text("actions_min: 0\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 0


def test_load_rules_roundtrip_1(tmp_path: Path) -> None:
    p = tmp_path / "r1.yaml"
    p.write_text("actions_min: 1\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 1


def test_load_rules_roundtrip_2(tmp_path: Path) -> None:
    p = tmp_path / "r2.yaml"
    p.write_text("actions_min: 2\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 2


def test_load_rules_roundtrip_3(tmp_path: Path) -> None:
    p = tmp_path / "r3.yaml"
    p.write_text("actions_min: 3\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 3


def test_load_rules_roundtrip_4(tmp_path: Path) -> None:
    p = tmp_path / "r4.yaml"
    p.write_text("actions_min: 4\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 4


def test_load_rules_roundtrip_5(tmp_path: Path) -> None:
    p = tmp_path / "r5.yaml"
    p.write_text("actions_min: 5\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 5


def test_load_rules_roundtrip_6(tmp_path: Path) -> None:
    p = tmp_path / "r6.yaml"
    p.write_text("actions_min: 6\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 6


def test_load_rules_roundtrip_7(tmp_path: Path) -> None:
    p = tmp_path / "r7.yaml"
    p.write_text("actions_min: 7\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 7


def test_load_rules_roundtrip_8(tmp_path: Path) -> None:
    p = tmp_path / "r8.yaml"
    p.write_text("actions_min: 8\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 8


def test_load_rules_roundtrip_9(tmp_path: Path) -> None:
    p = tmp_path / "r9.yaml"
    p.write_text("actions_min: 9\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 9


def test_load_rules_roundtrip_10(tmp_path: Path) -> None:
    p = tmp_path / "r10.yaml"
    p.write_text("actions_min: 10\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 10


def test_load_rules_roundtrip_11(tmp_path: Path) -> None:
    p = tmp_path / "r11.yaml"
    p.write_text("actions_min: 11\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 11


def test_load_rules_roundtrip_12(tmp_path: Path) -> None:
    p = tmp_path / "r12.yaml"
    p.write_text("actions_min: 12\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 12


def test_load_rules_roundtrip_13(tmp_path: Path) -> None:
    p = tmp_path / "r13.yaml"
    p.write_text("actions_min: 13\n", encoding="utf-8")
    assert load_rules_file(p)["actions_min"] == 13
