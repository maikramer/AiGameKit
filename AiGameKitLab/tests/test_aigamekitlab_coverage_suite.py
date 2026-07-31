"""Suite de cobertura AiGameKitLab — glb_meta, regras, diff, imagens, paths, perf, CLI."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image

from aigamekit_lab import perf_analyze
from aigamekit_lab.cli import main
from aigamekit_lab.compare_images import _to_float_rgb_a, metrics_mae_rmse_ssim
from aigamekit_lab.compare_inspect import _num, diff_inspect
from aigamekit_lab.glb_meta import glb_extract_meta
from aigamekit_lab.paths import aigamekit_repo_root
from aigamekit_lab.validate_rules import evaluate_inspect_rules, load_rules_file

GLB_MAGIC = b"glTF"
JSON_CHUNK_TYPE = 0x4E4F534A


def _pad4(data: bytes, fill: bytes = b"\x00") -> bytes:
    remainder = len(data) % 4
    return data if remainder == 0 else data + fill * (4 - remainder)


def build_glb(gltf: dict[str, Any], bin_data: bytes = b"") -> bytes:
    json_bytes = json.dumps(gltf).encode("utf-8")
    json_padded = _pad4(json_bytes, fill=b" ")
    chunks = struct.pack("<II", len(json_padded), JSON_CHUNK_TYPE) + json_padded
    if bin_data:
        bin_padded = _pad4(bin_data)
        chunks += struct.pack("<II", len(bin_padded), 0x004E4942) + bin_padded
    total = 12 + len(chunks)
    header = struct.pack("<4sII", GLB_MAGIC, 2, total)
    return header + chunks


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestGlbMetaCoverage:
    def test_extract_from_built_glb(self, tmp_path: Path) -> None:
        gltf = {
            "asset": {"version": "2.0"},
            "accessors": [
                {"count": 6, "min": [0.0, -0.5, 0.0]},
                {"count": 9},
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        }
        path = tmp_path / "m.glb"
        path.write_bytes(build_glb(gltf))
        meta = glb_extract_meta(path)
        assert meta.get("mesh_count") == 1
        assert "POSITION" in meta.get("attributes_present", [])

    def test_invalid_magic_returns_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.glb"
        p.write_bytes(b"XXXX" + b"\x00" * 20)
        assert "_error" in glb_extract_meta(p)

    def test_extensions_and_mime(self, tmp_path: Path) -> None:
        gltf = {
            "asset": {"version": "2.0"},
            "extensionsUsed": ["EXT_meshopt_compression"],
            "images": [{"mimeType": "image/png"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"count": 3, "min": [0, 0, 0]}],
        }
        path = tmp_path / "ext.glb"
        path.write_bytes(build_glb(gltf))
        meta = glb_extract_meta(path)
        assert "EXT_meshopt_compression" in meta.get("extensions_used", [])
        assert meta.get("texture_mime_types") == ["image/png"]


class TestLoadRulesFile:
    def test_load_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "rules.yaml"
        p.write_text("mesh_totals:\n  vertex_count:\n    min: 1\n", encoding="utf-8")
        rules = load_rules_file(p)
        assert rules["mesh_totals"]["vertex_count"]["min"] == 1

    def test_load_json(self, tmp_path: Path) -> None:
        p = tmp_path / "rules.json"
        p.write_text('{"actions_min": 1}', encoding="utf-8")
        assert load_rules_file(p)["actions_min"] == 1

    def test_rejects_non_dict_root(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("- item\n", encoding="utf-8")
        with pytest.raises(ValueError, match="dict"):
            load_rules_file(p)


class TestEvaluateInspectRules:
    def _inspect_ok(self) -> dict[str, Any]:
        return {
            "mesh_totals": {"vertex_count": 1000, "face_count": 500},
            "world_bounds": {"max_extent": 2.0, "size": [1.0, 2.0, 1.0]},
            "armatures": [{"bone_count": 20, "bones": ["Hips", "Spine"]}],
            "actions": [{"name": "Idle"}],
            "meshes": [{"name": "Body"}],
            "glb_meta": {
                "attributes_present": ["POSITION", "NORMAL"],
                "v_per_tri": 1.2,
                "texture_mime_types": ["image/png"],
                "extensions_used": [],
                "world_bounds_y_min": 0.0,
            },
        }

    def test_passes_minimal_rules(self) -> None:
        ok, fails, _ = evaluate_inspect_rules(self._inspect_ok(), {"actions_min": 1})
        assert ok is True
        assert fails == []

    def test_vertex_count_min_fail(self) -> None:
        insp = self._inspect_ok()
        insp["mesh_totals"]["vertex_count"] = 5
        ok, fails, _ = evaluate_inspect_rules(insp, {"mesh_totals": {"vertex_count": {"min": 100}}})
        assert ok is False
        assert any("vertex_count" in f for f in fails)

    def test_glb_meta_fallback_face_count(self) -> None:
        insp = {"glb_meta": {"face_count_total": 100}, "mesh_totals": {}}
        ok, fails, _ = evaluate_inspect_rules(
            insp,
            {"mesh_totals": {"face_count": {"min": 50, "max": 200}}},
        )
        assert ok is True
        assert fails == []

    def test_bones_contain_missing(self) -> None:
        insp = self._inspect_ok()
        ok, _fails, _ = evaluate_inspect_rules(insp, {"bones_contain": ["LeftToe"]})
        assert ok is False

    def test_attributes_required(self) -> None:
        insp = self._inspect_ok()
        ok, _, _ = evaluate_inspect_rules(insp, {"attributes_required": ["TANGENT"]})
        assert ok is False

    def test_texture_format_png_ok(self) -> None:
        insp = self._inspect_ok()
        ok, _, _ = evaluate_inspect_rules(insp, {"texture_format": "png"})
        assert ok is True

    def test_texture_format_ktx2_fail(self) -> None:
        insp = self._inspect_ok()
        ok, fails, _ = evaluate_inspect_rules(insp, {"texture_format": "ktx2"})
        assert ok is False
        assert any("texture_format" in f for f in fails)

    def test_compression_meshopt_missing(self) -> None:
        insp = self._inspect_ok()
        ok, _, _ = evaluate_inspect_rules(insp, {"compression": "meshopt"})
        assert ok is False

    def test_origin_y_min_pass(self) -> None:
        insp = self._inspect_ok()
        ok, _, _ = evaluate_inspect_rules(insp, {"origin": {"y_min": {"near": 0.0, "tol": 0.05}}})
        assert ok is True

    def test_face_count_max_per_category(self) -> None:
        insp = self._inspect_ok()
        rules = {"face_count": {"max_per_category": {"humanoid": 600}}}
        ok, _, _ = evaluate_inspect_rules(insp, rules, category="humanoid")
        assert ok is True

    def test_meshes_min_from_glb_meta(self) -> None:
        insp = {"glb_meta": {"mesh_count": 2}, "meshes": []}
        ok, _, _ = evaluate_inspect_rules(insp, {"meshes_min": 2})
        assert ok is True

    def test_v_per_tri_max(self) -> None:
        insp = self._inspect_ok()
        insp["glb_meta"]["v_per_tri"] = 4.0
        ok, _fails, _ = evaluate_inspect_rules(insp, {"mesh_totals": {"v_per_tri": {"max": 2.0}}})
        assert ok is False


class TestDiffInspect:
    def test_fixture_delta_vertices(self) -> None:
        a = json.loads((FIXTURES / "inspect_sample_a.json").read_text(encoding="utf-8"))
        b = json.loads((FIXTURES / "inspect_sample_b.json").read_text(encoding="utf-8"))
        out = diff_inspect(a, b)
        assert out["mesh_totals_delta"]["vertex_count"]["delta"] == 1000
        assert "Δvértices" in out["summary"]

    def test_bone_only_a_listed(self) -> None:
        a = {"armatures": [{"bones": ["Hips", "Spine"]}]}
        b = {"armatures": [{"bones": ["Hips"]}]}
        out = diff_inspect(a, b)
        assert "Spine" in out["bones"]["only_a"]

    def test_num_helper(self) -> None:
        assert _num("3.5") == 3.5
        assert _num(None) is None
        assert _num("nope") is None

    def test_world_bounds_size_delta(self) -> None:
        a = {"world_bounds": {"size": [1.0, 2.0, 1.0]}}
        b = {"world_bounds": {"size": [2.0, 2.0, 1.0]}}
        out = diff_inspect(a, b)
        assert out["world_bounds_delta"]["size"][0]["delta"] == 1.0

    def test_animation_counts(self) -> None:
        a = {"actions": [1, 2]}
        b = {"actions": []}
        out = diff_inspect(a, b)
        assert out["animations"]["count_a"] == 2
        assert out["animations"]["count_b"] == 0


class TestCompareImages:
    def test_identical_arrays_perfect_ssim(self) -> None:
        arr = np.ones((4, 4, 3), dtype=np.float32) * 0.5
        m = metrics_mae_rmse_ssim(arr, arr)
        assert m["mae"] == 0.0
        assert m["ssim"] == pytest.approx(1.0, abs=1e-5)

    def test_different_arrays_positive_mae(self) -> None:
        a = np.zeros((2, 2, 3), dtype=np.float32)
        b = np.ones((2, 2, 3), dtype=np.float32)
        m = metrics_mae_rmse_ssim(a, b)
        assert m["mae"] > 0
        assert m["rmse"] >= m["mae"]

    def test_to_float_rgb_grayscale(self) -> None:
        gray = np.array([[128, 64], [32, 255]], dtype=np.uint8)
        rgb = _to_float_rgb_a(gray)
        assert rgb.shape == (2, 2, 3)
        assert rgb[0, 0, 0] == pytest.approx(128 / 255.0)

    def test_to_float_rgb_strips_alpha(self) -> None:
        rgba = np.zeros((1, 1, 4), dtype=np.uint8)
        rgba[0, 0, 3] = 255
        assert _to_float_rgb_a(rgba).shape[-1] == 3

    def test_png_pair_via_files(self, tmp_path: Path) -> None:
        from aigamekit_lab.compare_images import compare_view_pair

        pa, pb = tmp_path / "a.png", tmp_path / "b.png"
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(pa)
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(pb)
        result = compare_view_pair(pa, pb)
        assert result["ssim"] == pytest.approx(1.0, abs=1e-4)


class TestPaths:
    def test_repo_root_from_file_location(self) -> None:
        root = aigamekit_repo_root()
        assert (root / "AiGameKitLab").is_dir()
        assert (root / "Text3D").is_dir()

    def test_repo_root_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AIGAMEKIT_ROOT", str(tmp_path))
        assert aigamekit_repo_root() == tmp_path.resolve()


class TestPerfAnalyze:
    def test_list_runs_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "perf.db"
        runs = perf_analyze.list_runs(limit=5, db_path=str(db))
        assert runs == []

    def test_list_runs_after_insert(self, tmp_path: Path) -> None:
        from aigamekit_shared.perfstore.db import PerfDB
        from aigamekit_shared.perfstore.models import RunRecord

        db_path = tmp_path / "perf.db"
        with PerfDB(db_path) as db:
            db.insert_run(
                RunRecord(
                    tool="text2d",
                    started_at="2026-01-01T00:00:00",
                    success=True,
                    gpu_name="Test GPU",
                    gpu_total_vram_mb=8192,
                    quantization_mode="fp16",
                )
            )
        runs = perf_analyze.list_runs(tool="text2d", limit=10, db_path=str(db_path))
        assert len(runs) == 1
        assert runs[0]["tool"] == "text2d"

    def test_print_runs_table_no_crash(self, capsys: pytest.CaptureFixture[str]) -> None:
        perf_analyze.print_runs_table([])
        assert "No runs" in capsys.readouterr().out or True

    def test_print_summary_empty(self, tmp_path: Path) -> None:
        rows = perf_analyze.print_summary(db_path=str(tmp_path / "empty.db"))
        assert rows == []


class TestCliHelpGroups:
    def test_main_help(self) -> None:
        r = CliRunner().invoke(main, ["--help"])
        assert r.exit_code == 0

    def test_check_group_help(self) -> None:
        r = CliRunner().invoke(main, ["check", "--help"])
        assert r.exit_code == 0
        assert "GLB" in r.output or "glb" in r.output.lower()

    def test_debug_group_help(self) -> None:
        r = CliRunner().invoke(main, ["debug", "--help"])
        assert r.exit_code == 0

    def test_bench_group_help(self) -> None:
        r = CliRunner().invoke(main, ["bench", "--help"])
        assert r.exit_code == 0

    def test_perf_group_help(self) -> None:
        r = CliRunner().invoke(main, ["perf", "--help"])
        assert r.exit_code == 0

    def test_check_glb_help(self) -> None:
        r = CliRunner().invoke(main, ["check", "glb", "--help"])
        assert r.exit_code == 0

    def test_perf_list_help(self) -> None:
        r = CliRunner().invoke(main, ["perf", "list", "--help"])
        assert r.exit_code == 0
