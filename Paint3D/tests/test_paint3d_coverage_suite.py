"""Suite de cobertura Paint3D (UMS, ruído, quick_bake, hardware, paths, defaults) — sem GPU."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from paint3d import defaults
from paint3d.hardware import GIB, Paint3DHardwareProfile, profile_from_specs
from paint3d.procedural_noise import _v01, fbm3, normalize_to_unit_cube
from paint3d.quick_bake import parse_hex_rgb
from paint3d.vramd_payload import build_texture_request


def _gib(n: float) -> int:
    return int(n * GIB)


class TestBuildTextureRequest:
    def test_required_paths(self) -> None:
        req = build_texture_request(mesh_path="/m.glb", image_path="/i.png", output="/o.glb")
        assert req["mesh_path"] == "/m.glb"
        assert req["image_path"] == "/i.png"
        assert req["output"] == "/o.glb"

    def test_default_numeric_fields(self) -> None:
        req = build_texture_request(mesh_path="a", image_path="b", output="c")
        assert req["max_num_view"] == 6
        assert req["view_resolution"] == 512
        assert req["render_size"] == 1024
        assert req["texture_size"] == 1024

    def test_bake_exp_optional(self) -> None:
        without = build_texture_request(mesh_path="a", image_path="b", output="c")
        assert "bake_exp" not in without
        with_exp = build_texture_request(mesh_path="a", image_path="b", output="c", bake_exp=6.0)
        assert with_exp["bake_exp"] == 6.0

    def test_smooth_passes_optional(self) -> None:
        req = build_texture_request(mesh_path="a", image_path="b", output="c", smooth_passes=2)
        assert req["smooth_passes"] == 2

    def test_upscale_flags(self) -> None:
        req = build_texture_request(
            mesh_path="a",
            image_path="b",
            output="c",
            upscale=True,
            upscale_factor=2.0,
        )
        assert req["upscale"] is True
        assert req["upscale_factor"] == 2.0

    def test_torch_compile_mode(self) -> None:
        req = build_texture_request(
            mesh_path="a",
            image_path="b",
            output="c",
            torch_compile=True,
            torch_compile_mode="reduce-overhead",
        )
        assert req["torch_compile"] is True
        assert req["torch_compile_mode"] == "reduce-overhead"

    def test_gpu_ids_list(self) -> None:
        req = build_texture_request(mesh_path="a", image_path="b", output="c", gpu_ids=[0, 1])
        assert req["gpu_ids"] == [0, 1]

    def test_gpu_ids_string(self) -> None:
        req = build_texture_request(mesh_path="a", image_path="b", output="c", gpu_ids="0,2")
        assert req["gpu_ids"] == [0, 2]

    def test_memory_efficient_sets_sdnq_preset(self) -> None:
        req = build_texture_request(
            mesh_path="a",
            image_path="b",
            output="c",
            memory_efficient=True,
        )
        assert req["memory_efficient"] is True
        assert req.get("sdnq_preset") == "sdnq-uint8"

    def test_explicit_sdnq_none_without_mem_eff(self) -> None:
        req = build_texture_request(
            mesh_path="a",
            image_path="b",
            output="c",
            sdnq_preset="none",
        )
        assert "sdnq_preset" not in req or req.get("sdnq_preset") in (None, "none")

    def test_extra_merged(self) -> None:
        req = build_texture_request(
            mesh_path="a",
            image_path="b",
            output="c",
            extra={"batch_id": "x1"},
        )
        assert req["batch_id"] == "x1"

    def test_preserve_origin_and_smooth_bools(self) -> None:
        req = build_texture_request(
            mesh_path="a",
            image_path="b",
            output="c",
            preserve_origin=False,
            smooth=False,
            verbose=True,
        )
        assert req["preserve_origin"] is False
        assert req["smooth"] is False
        assert req["verbose"] is True


class TestProceduralNoiseCoverage:
    def test_v01_broadcast_shapes(self) -> None:
        n = 100
        ix = np.arange(n, dtype=np.int64)
        out = _v01(ix, ix + 1, ix + 2, seed=0)
        assert out.shape == (n,)

    def test_fbm3_empty_points(self) -> None:
        out = fbm3(np.zeros((0, 3)))
        assert out.shape == (0,)

    def test_fbm3_octaves_clamped_to_eight(self) -> None:
        pts = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
        out = fbm3(pts, octaves=99, seed=1)
        assert out.shape == (2,)
        assert np.all(out >= -1.0)
        assert np.all(out <= 1.0)

    def test_fbm3_deterministic(self) -> None:
        pts = np.random.default_rng(0).standard_normal((12, 3))
        a = fbm3(pts, seed=5, frequency=2.0, octaves=3)
        b = fbm3(pts, seed=5, frequency=2.0, octaves=3)
        np.testing.assert_array_equal(a, b)

    def test_normalize_to_unit_cube_empty(self) -> None:
        out = normalize_to_unit_cube(np.zeros((0, 3)))
        assert out.shape == (0, 3)

    def test_normalize_to_unit_cube_degenerate_extent(self) -> None:
        pts = np.tile([1.0, 2.0, 3.0], (5, 1))
        out = normalize_to_unit_cube(pts)
        np.testing.assert_allclose(out, 0.0)

    def test_normalize_to_unit_cube_scales_into_range(self) -> None:
        pts = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]])
        out = normalize_to_unit_cube(pts)
        assert np.abs(out).max() <= 1.0 + 1e-9


class TestParseHexRgb:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("#ff0000", (1.0, 0.0, 0.0)),
            ("00ff00", (0.0, 1.0, 0.0)),
            ("#0000ff", (0.0, 0.0, 1.0)),
            ("#f00", (1.0, 0.0, 0.0)),
            ("  #aabbcc  ", (170 / 255, 187 / 255, 204 / 255)),
        ],
    )
    def test_valid_hex(self, raw: str, expected: tuple[float, float, float]) -> None:
        assert parse_hex_rgb(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("bad", ["gggggg", "#12", "red", "#12345"])
    def test_invalid_hex_raises(self, bad: str) -> None:
        with pytest.raises(ValueError, match="hex"):
            parse_hex_rgb(bad)


class TestProfileFromSpecsCoverage:
    def test_multi_gpu_full_profile_lists_ids(self) -> None:
        p = profile_from_specs([(0, _gib(12)), (1, _gib(12))])
        assert p.memory_efficient is False
        assert p.gpu_ids == [0, 1]
        assert p.total_vram_gib == pytest.approx(24.0, abs=0.2)

    def test_single_12gb_no_overrides(self) -> None:
        p = profile_from_specs([(0, _gib(12))])
        assert p.max_views is None
        assert p.render_size is None

    def test_summary_string_nonempty(self) -> None:
        p = profile_from_specs([(0, _gib(6))])
        text = p.summary()
        assert isinstance(text, str)
        assert len(text) > 10

    def test_frozen_profile_immutable(self) -> None:
        p = profile_from_specs([])
        with pytest.raises((AttributeError, TypeError)):
            p.device = "cuda"  # type: ignore[misc]


class TestHy3d21PathsMonkeypatch:
    def test_resolve_hy3dpaint_root_under_package(self) -> None:
        from paint3d import hy3d21_paths

        root = hy3d21_paths.resolve_hy3dpaint_root()
        assert root.name == "hy3dpaint"
        assert root.parent.name == "paint3d"

    def test_ensure_hy3dpaint_on_path_inserts_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from paint3d import hy3d21_paths

        fake = Path("/tmp/fake_hy3dpaint")
        monkeypatch.setattr(hy3d21_paths, "resolve_hy3dpaint_root", lambda: fake)
        monkeypatch.setattr(sys, "path", [])
        hy3d21_paths.ensure_hy3dpaint_on_path()
        assert sys.path[0] == str(fake)
        n = len(sys.path)
        hy3d21_paths.ensure_hy3dpaint_on_path()
        assert len(sys.path) == n

    def test_default_realesrgan_ckpt_suffix(self) -> None:
        from paint3d.hy3d21_paths import default_realesrgan_ckpt

        p = default_realesrgan_ckpt()
        assert p.name == "RealESRGAN_x4plus.pth"
        assert p.parent.name == "ckpt"


class TestDefaultsModuleConstants:
    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("DEFAULT_PAINT_HF_REPO", "tencent/Hunyuan3D-2.1"),
            ("DEFAULT_PAINT_SUBFOLDER", "hunyuan3d-paintpbr-v2-1"),
            ("DEFAULT_PAINT_RENDER_SIZE", 2048),
            ("DEFAULT_PAINT_TEXTURE_SIZE", 4096),
            ("DEFAULT_PAINT_MAX_VIEWS", 6),
            ("DEFAULT_PAINT_VIEW_RESOLUTION", 640),
            ("DEFAULT_PAINT_BAKE_EXP", 6),
            ("MEMORY_EFFICIENT_RENDER_SIZE", 1024),
            ("MEMORY_EFFICIENT_TEXTURE_SIZE", 2048),
            ("DEFAULT_SMOOTH", True),
            ("DEFAULT_UPSCALE", False),
        ],
    )
    def test_constant_values(self, name: str, value: object) -> None:
        assert getattr(defaults, name) == value

    def test_dino_gpu_min_gib(self) -> None:
        assert defaults.DINO_GPU_MIN_GIB == 10.0


class TestPaint3DHardwareProfileSummary:
    def test_cpu_profile_summary_mentions_memory_efficient(self) -> None:
        p = Paint3DHardwareProfile(
            name="cpu",
            device="cpu",
            memory_efficient=True,
            gpu_ids=None,
            total_vram_gib=0.0,
            max_views=4,
            view_resolution=384,
            render_size=1024,
            texture_size=2048,
        )
        assert "memory-efficient" in p.summary()
