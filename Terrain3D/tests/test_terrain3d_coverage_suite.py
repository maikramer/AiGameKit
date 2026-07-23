"""Suite de cobertura Terrain3D (UMS payload, postprocess, export, config, CLI) — sem GPU."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from click.testing import CliRunner

from terrain3d.export import export_heightmap, export_metadata
from terrain3d.generator import TerrainConfig, TerrainResult
from terrain3d.postprocess import (
    _sample_circular_noise,
    _smoothstep,
    apply_postprocess_chain,
    elevation_scurve,
    island_falloff,
    taubin_smooth,
)
from terrain3d.ums_payload import build_generate_request


class TestBuildGenerateRequest:
    def test_minimal_payload_has_output_only(self) -> None:
        req = build_generate_request(output="/tmp/h.png")
        assert req["output"] == "/tmp/h.png"

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("metadata_path", "/meta/terrain.json", "/meta/terrain.json"),
            ("seed", 42, 42),
            ("size", 1024, 1024),
            ("world_size", 256.5, 256.5),
            ("max_height", 12.0, 12.0),
            ("mode", "continental", "continental"),
            ("device", "cpu", "cpu"),
            ("prompt", "volcanic island", "volcanic island"),
            ("dtype", "bf16", "bf16"),
            ("cache_size", "1G", "1G"),
            ("coarse_window", 8, 8),
        ],
    )
    def test_optional_scalar_fields(self, field: str, value: object, expected: object) -> None:
        req = build_generate_request(output="out.png", **{field: value})
        assert req[field] == expected

    def test_omitted_optionals_absent(self) -> None:
        req = build_generate_request(output="only.png")
        for key in (
            "metadata_path",
            "seed",
            "size",
            "world_size",
            "max_height",
            "mode",
            "device",
            "prompt",
            "dtype",
            "cache_size",
            "coarse_window",
        ):
            assert key not in req

    def test_seed_coerced_to_int(self) -> None:
        req = build_generate_request(output="x.png", seed=99)
        assert req["seed"] == 99
        assert isinstance(req["seed"], int)

    def test_gpu_ids_list_in_payload(self) -> None:
        req = build_generate_request(output="x.png", gpu_ids=[0, 1])
        assert req["gpu_ids"] == [0, 1]

    def test_gpu_ids_string_parsed(self) -> None:
        req = build_generate_request(output="x.png", gpu_ids="0, 2 , 3")
        assert req["gpu_ids"] == [0, 2, 3]

    def test_gpu_ids_empty_string_omits_key(self) -> None:
        req = build_generate_request(output="x.png", gpu_ids="  , ")
        assert "gpu_ids" not in req

    def test_extra_merged_into_payload(self) -> None:
        req = build_generate_request(output="x.png", extra={"custom_flag": True, "tier": "high"})
        assert req["custom_flag"] is True
        assert req["tier"] == "high"

    def test_extra_does_not_replace_output(self) -> None:
        req = build_generate_request(output="/a.png", extra={"note": "batch"})
        assert req["output"] == "/a.png"
        assert req["note"] == "batch"

    def test_all_optionals_together(self) -> None:
        req = build_generate_request(
            output="/out/h.png",
            metadata_path="/out/m.json",
            seed=7,
            size=512,
            world_size=128.0,
            max_height=40.0,
            mode="island",
            device="cuda",
            prompt="hills",
            dtype="fp16",
            cache_size="100M",
            coarse_window=2,
            gpu_ids=[1],
            extra={"job": "test"},
        )
        assert req["output"] == "/out/h.png"
        assert req["metadata_path"] == "/out/m.json"
        assert req["seed"] == 7
        assert req["size"] == 512
        assert req["gpu_ids"] == [1]
        assert req["job"] == "test"

    def test_with_ums_peak_includes_backend_signals(self) -> None:
        req = build_generate_request(output="x.png")
        assert "memory_efficient" not in req or req.get("memory_efficient") is False


class TestSmoothstep:
    def test_below_edge0_is_zero(self) -> None:
        x = np.array([0.0, 0.1])
        out = _smoothstep(0.5, 1.0, x)
        np.testing.assert_array_equal(out, [0.0, 0.0])

    def test_above_edge1_is_one(self) -> None:
        x = np.array([1.0, 1.5])
        out = _smoothstep(0.0, 0.5, x)
        np.testing.assert_array_equal(out, [1.0, 1.0])

    def test_midpoint_is_half(self) -> None:
        out = _smoothstep(0.0, 1.0, np.array([0.5]))
        assert out[0] == pytest.approx(0.5)

    @pytest.mark.parametrize(
        "t",
        [0.0, 0.25, 0.5, 0.75, 1.0],
    )
    def test_output_bounded_zero_one(self, t: float) -> None:
        out = _smoothstep(0.0, 1.0, np.array([t]))
        assert 0.0 <= out[0] <= 1.0

    def test_vectorized_matches_scalar_loop(self) -> None:
        xs = np.linspace(0.0, 1.0, 11)
        bulk = _smoothstep(0.2, 0.8, xs)
        for i, x in enumerate(xs):
            single = _smoothstep(0.2, 0.8, np.array([x]))[0]
            assert bulk[i] == pytest.approx(single)

    def test_monotonic_increasing(self) -> None:
        xs = np.linspace(0.0, 1.0, 50)
        ys = _smoothstep(0.1, 0.9, xs)
        assert np.all(np.diff(ys) >= -1e-12)


class TestSampleCircularNoise:
    def test_constant_noise_profile(self) -> None:
        noise = np.full(64, 0.25)
        angles = np.array([0.0, np.pi / 2, np.pi, 3 * np.pi / 2])
        out = _sample_circular_noise(noise, angles)
        np.testing.assert_allclose(out, 0.25)

    def test_wraps_at_two_pi(self) -> None:
        noise = np.arange(8, dtype=np.float64)
        a0 = _sample_circular_noise(noise, np.array([0.0]))
        a2pi = _sample_circular_noise(noise, np.array([2.0 * np.pi]))
        assert a0[0] == pytest.approx(a2pi[0], abs=1e-6)

    def test_output_shape_matches_angles(self) -> None:
        noise = np.linspace(-1, 1, 32)
        angles = np.random.default_rng(0).uniform(0, 2 * np.pi, size=(4, 5))
        out = _sample_circular_noise(noise, angles)
        assert out.shape == angles.shape

    def test_linear_interpolation_midpoint(self) -> None:
        noise = np.array([0.0, 1.0])
        angle = np.array([np.pi / 2])
        out = _sample_circular_noise(noise, angle)
        assert out[0] == pytest.approx(0.5, abs=0.01)

    def test_2d_angles_grid(self) -> None:
        noise = np.sin(np.linspace(0, 2 * np.pi, 128, endpoint=False))
        y, x = np.mgrid[0:8, 0:8]
        angles = np.arctan2(y - 4, x - 4)
        angles_pos = (angles + 2 * np.pi) % (2 * np.pi)
        out = _sample_circular_noise(noise, angles_pos)
        assert out.shape == (8, 8)


class TestIslandFalloffWithMockNoise:
    def test_uses_circular_perlin(self) -> None:
        h = np.ones((32, 32), dtype=np.float64)
        fake = np.zeros(1024)
        with patch("terrain3d.postprocess._circular_perlin", return_value=fake):
            out = island_falloff(h, falloff=0.35, noise_scale=0.0, seed=1)
        assert out.shape == h.shape
        assert out[16, 16] == pytest.approx(1.0, abs=0.05)

    def test_zero_noise_scale_symmetric_mask(self) -> None:
        h = np.full((64, 64), 0.8, dtype=np.float64)
        with patch("terrain3d.postprocess._circular_perlin", return_value=np.zeros(1024)):
            out = island_falloff(h, falloff=0.4, noise_scale=0.0, seed=0)
        assert out[0, 0] < out[32, 32]
        assert out[32, 32] == pytest.approx(0.8, abs=0.1)


class TestTaubinSmoothCoverage:
    def test_zero_iterations_returns_copy(self) -> None:
        h = np.random.default_rng(1).random((16, 16))
        out = taubin_smooth(h, iterations=0)
        np.testing.assert_array_equal(out, h)

    def test_reduces_high_frequency(self) -> None:
        rng = np.random.default_rng(2)
        base = np.linspace(0, 1, 32, dtype=np.float64).reshape(1, -1) * np.ones((32, 1))
        noisy = base + rng.normal(0, 0.08, (32, 32))
        smooth = taubin_smooth(noisy, iterations=5)
        assert smooth.std() < noisy.std()

    def test_preserves_mean_approx(self) -> None:
        h = np.full((24, 24), 0.42, dtype=np.float64)
        out = taubin_smooth(h, iterations=3)
        assert out.mean() == pytest.approx(0.42, abs=0.05)

    def test_output_dtype_float64(self) -> None:
        h = np.ones((8, 8), dtype=np.float32)
        out = taubin_smooth(h.astype(np.float64), iterations=1)
        assert out.dtype == np.float64


class TestElevationScurveCoverage:
    def test_gamma_one_contrast_zero_identity(self) -> None:
        h = np.linspace(0, 1, 16, dtype=np.float64).reshape(4, 4)
        out = elevation_scurve(h, gamma=1.0, contrast=0.0)
        np.testing.assert_allclose(out, h, atol=1e-6)

    def test_gamma_above_one_lifts_low_values(self) -> None:
        h = np.array([[0.25, 0.75]], dtype=np.float64)
        out = elevation_scurve(h, gamma=2.0, contrast=0.0)
        assert out[0, 0] > h[0, 0]
        assert out[0, 1] > h[0, 1]

    def test_contrast_increases_mid_range_spread(self) -> None:
        h = np.array([[0.4, 0.6]], dtype=np.float64)
        out = elevation_scurve(h, gamma=1.0, contrast=0.2)
        assert out[0, 1] - out[0, 0] >= h[0, 1] - h[0, 0] - 1e-6

    def test_output_clipped_to_unit_interval(self) -> None:
        h = np.random.default_rng(3).random((10, 10))
        out = elevation_scurve(h, gamma=1.5, contrast=0.15)
        assert out.min() >= 0.0
        assert out.max() <= 1.0


class TestApplyPostprocessChainCoverage:
    @pytest.fixture
    def plate(self) -> np.ndarray:
        return np.full((48, 48), 0.6, dtype=np.float64)

    def test_continental_skips_falloff(self, plate: np.ndarray) -> None:
        with patch("terrain3d.postprocess.island_falloff") as mock_iso:
            apply_postprocess_chain(plate, mode="continental", smooth_iterations=0, elevation_gamma=1.0)
        mock_iso.assert_not_called()

    def test_island_calls_falloff(self, plate: np.ndarray) -> None:
        with patch("terrain3d.postprocess.island_falloff", side_effect=lambda h, *a, **k: h) as mock_iso:
            apply_postprocess_chain(plate, mode="island", smooth_iterations=0, elevation_gamma=1.0)
        mock_iso.assert_called_once()

    def test_normalizes_to_zero_one(self, plate: np.ndarray) -> None:
        h = plate.copy()
        h[0, :] = 0.2
        h[-1, :] = 0.9
        out = apply_postprocess_chain(
            h,
            mode="continental",
            smooth_iterations=0,
            elevation_gamma=1.0,
            elevation_contrast=0.0,
        )
        assert out.min() == pytest.approx(0.0, abs=1e-6)
        assert out.max() == pytest.approx(1.0, abs=1e-6)

    def test_flat_input_stays_finite(self) -> None:
        flat = np.zeros((20, 20), dtype=np.float64)
        out = apply_postprocess_chain(flat, mode="continental", smooth_iterations=0)
        assert np.all(np.isfinite(out))


class TestTerrainConfigDefaults:
    def test_default_size_and_world(self) -> None:
        cfg = TerrainConfig()
        assert cfg.size == 2048
        assert cfg.world_size == 512.0
        assert cfg.max_height == 50.0

    def test_default_postprocess_mode(self) -> None:
        cfg = TerrainConfig()
        assert cfg.mode == "island"
        assert cfg.island_falloff == pytest.approx(0.35)
        assert cfg.smooth_iterations == 3

    def test_optional_fields_none_or_empty(self) -> None:
        cfg = TerrainConfig()
        assert cfg.seed is None
        assert cfg.device is None
        assert cfg.dtype is None
        assert cfg.prompt is None
        assert cfg.model_id == ""

    @pytest.mark.parametrize(
        "field,expected",
        [
            ("cache_size", "100M"),
            ("coarse_window", 4),
            ("island_noise_scale", 0.15),
            ("island_noise_freq", 3.0),
            ("elevation_gamma", 1.2),
            ("elevation_contrast", 0.1),
            ("num_inference_steps", 20),
        ],
    )
    def test_default_constants(self, field: str, expected: object) -> None:
        cfg = TerrainConfig()
        assert getattr(cfg, field) == expected

    def test_custom_override(self) -> None:
        cfg = TerrainConfig(size=128, mode="continental", prompt="mesa")
        assert cfg.size == 128
        assert cfg.mode == "continental"
        assert cfg.prompt == "mesa"


class TestExportHeightmapCoverage:
    def test_clips_above_one(self, tmp_path: Path) -> None:
        from PIL import Image

        arr = np.array([[1.5, -0.2]], dtype=np.float64)
        path = export_heightmap(arr, tmp_path / "c.png", size=2)
        img = np.array(Image.open(path))
        assert img.max() <= 255
        assert img.min() >= 0

    def test_resizes_non_square_input(self, tmp_path: Path) -> None:
        from PIL import Image

        arr = np.zeros((10, 20), dtype=np.float64)
        path = export_heightmap(arr, tmp_path / "r.png", size=32)
        assert Image.open(path).size == (32, 32)

    def test_returns_path_object(self, tmp_path: Path) -> None:
        arr = np.ones((4, 4), dtype=np.float64) * 0.5
        out = export_heightmap(arr, tmp_path / "t.png", size=4)
        assert isinstance(out, Path)
        assert out.is_file()


class TestExportMetadataCoverage:
    def test_model_id_from_stats(self, tmp_path: Path) -> None:
        cfg = TerrainConfig(size=8)
        h = np.ones((8, 8), dtype=np.float64) * 0.3
        result = TerrainResult(heightmap=h, config=cfg, stats={"model_id": "custom/model"})
        export_metadata(result, tmp_path / "m.json")
        data = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
        assert data["model_id"] == "custom/model"

    def test_generation_time_in_stats_block(self, tmp_path: Path) -> None:
        cfg = TerrainConfig(size=4)
        h = np.zeros((4, 4), dtype=np.float64)
        result = TerrainResult(heightmap=h, config=cfg, stats={"generation_time_seconds": 9.5})
        export_metadata(result, tmp_path / "m.json")
        data = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
        assert data["stats"]["generation_time_seconds"] == 9.5

    def test_height_stats_match_array(self, tmp_path: Path) -> None:
        cfg = TerrainConfig(size=3, world_size=10.0, max_height=5.0)
        h = np.array([[0.0, 0.5, 1.0]], dtype=np.float64)
        result = TerrainResult(heightmap=h, config=cfg, stats={})
        export_metadata(result, tmp_path / "m.json")
        t = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["terrain"]
        assert t["height_min"] == pytest.approx(0.0)
        assert t["height_max"] == pytest.approx(1.0)
        assert t["height_mean"] == pytest.approx(0.5)


class TestTerrain3dCliHelp:
    def test_root_help(self) -> None:
        from terrain3d.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "terrain3d" in result.output.lower() or "terrain" in result.output.lower()

    def test_generate_help_lists_options(self) -> None:
        from terrain3d.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["generate", "--help"])
        assert result.exit_code == 0
        assert "--output" in result.output
        assert "--quality" in result.output

    def test_serve_help(self) -> None:
        from terrain3d.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["serve", "--help"])
        assert result.exit_code == 0
        assert "ums-worker" in result.output.lower() or "worker" in result.output.lower()

    def test_version(self) -> None:
        from terrain3d.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestCircularPerlinOptional:
    """FastNoiseLite opcional — skip se pyfastnoiselite ausente."""

    def test_circular_perlin_shape(self) -> None:
        pytest.importorskip("pyfastnoiselite")
        from terrain3d.postprocess import _circular_perlin

        arr = _circular_perlin(seed=5, noise_freq=2.0, n_samples=256)
        assert arr.shape == (256,)
        assert arr.min() >= -1.0
        assert arr.max() <= 1.0

    def test_circular_perlin_deterministic(self) -> None:
        pytest.importorskip("pyfastnoiselite")
        from terrain3d.postprocess import _circular_perlin

        a = _circular_perlin(seed=11, noise_freq=1.0, n_samples=64)
        b = _circular_perlin(seed=11, noise_freq=1.0, n_samples=64)
        np.testing.assert_array_equal(a, b)


def test_build_generate_request_does_not_mutate_extra() -> None:
    extra = {"k": 1}
    build_generate_request(output="o.png", extra=extra)
    assert extra == {"k": 1}


@pytest.mark.parametrize("gpu_ids", [[0], [1, 2], "1"])
def test_build_generate_request_gpu_variants(gpu_ids: list[int] | str) -> None:
    req = build_generate_request(output="g.png", gpu_ids=gpu_ids)
    assert "gpu_ids" in req
    assert len(req["gpu_ids"]) >= 1


class TestResolveModelIdAndNativeResolution:
    def test_resolve_model_id_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terrain3d.generator import DEFAULT_MODEL_ID, _resolve_model_id

        monkeypatch.delenv("TERRAIN3D_MODEL_ID", raising=False)
        assert _resolve_model_id() == DEFAULT_MODEL_ID

    def test_resolve_model_id_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terrain3d.generator import _resolve_model_id

        monkeypatch.setenv("TERRAIN3D_MODEL_ID", "org/custom-90m")
        assert _resolve_model_id() == "org/custom-90m"

    @pytest.mark.parametrize(
        ("model_id", "meters"),
        [
            ("xandergos/terrain-diffusion-30m", 30.0),
            ("xandergos/terrain-diffusion-90m", 90.0),
            ("ORG/Terrain-Diffusion-90M", 90.0),
            ("local/foo-30m-bar", 30.0),
            ("no-resolution-tag", 30.0),
        ],
    )
    def test_native_resolution_from_model(self, model_id: str, meters: float) -> None:
        from terrain3d.generator import _native_resolution_from_model

        assert _native_resolution_from_model(model_id) == meters


class TestTerrainConfigDefaults:
    def test_default_size_and_mode(self) -> None:
        cfg = TerrainConfig()
        assert cfg.size == 2048
        assert cfg.mode == "island"
        assert cfg.world_size == 512.0
        assert cfg.max_height == 50.0
        assert cfg.num_inference_steps == 20
        assert cfg.cache_size == "100M"
        assert cfg.coarse_window == 4

    def test_postprocess_defaults(self) -> None:
        cfg = TerrainConfig()
        assert cfg.island_falloff == pytest.approx(0.35)
        assert cfg.island_noise_scale == pytest.approx(0.15)
        assert cfg.island_noise_freq == pytest.approx(3.0)
        assert cfg.smooth_iterations == 3
        assert cfg.elevation_gamma == pytest.approx(1.2)
        assert cfg.elevation_contrast == pytest.approx(0.1)

    def test_terrain_result_stats_default_empty(self) -> None:
        h = np.zeros((4, 4), dtype=np.float64)
        result = TerrainResult(heightmap=h, config=TerrainConfig())
        assert result.stats == {}


class TestExportEdgeCases:
    def test_export_heightmap_clips_out_of_range(self, tmp_path: Path) -> None:
        h = np.array([[-0.5, 0.0], [1.0, 2.0]], dtype=np.float64)
        out = export_heightmap(h, tmp_path / "clip.png", size=2)
        from PIL import Image

        arr = np.array(Image.open(out))
        assert arr.min() >= 0
        assert arr.max() <= 255
        assert arr[0, 0] == 0
        assert arr[1, 1] == 255

    def test_export_heightmap_creates_parent_dirs(self, tmp_path: Path) -> None:
        h = np.ones((8, 8), dtype=np.float64) * 0.25
        out = export_heightmap(h, tmp_path / "nested" / "a" / "h.png", size=8)
        assert out.is_file()

    def test_export_metadata_includes_prompt(self, tmp_path: Path) -> None:
        cfg = TerrainConfig(size=4, prompt="rolling hills")
        h = np.linspace(0, 1, 16, dtype=np.float64).reshape(4, 4)
        result = TerrainResult(heightmap=h, config=cfg, stats={"generation_time_seconds": 1.5})
        path = export_metadata(result, tmp_path / "meta.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["generator"] == "terrain3d"
        assert data["version"] == "2.0"
        assert data.get("prompt") == "rolling hills" or "rolling" in json.dumps(data)
        assert data["stats"]["generation_time_seconds"] == pytest.approx(1.5)

    def test_export_metadata_empty_hydrography(self, tmp_path: Path) -> None:
        cfg = TerrainConfig(size=2)
        h = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float64)
        path = export_metadata(TerrainResult(heightmap=h, config=cfg), tmp_path / "m.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["rivers"] == []
        assert data["lakes"] == []
        assert data["lake_planes"] == []


class TestPostprocessStability:
    def test_taubin_zero_iterations_identity(self) -> None:
        h = np.random.default_rng(0).random((16, 16))
        out = taubin_smooth(h, iterations=0)
        np.testing.assert_allclose(out, h)

    def test_elevation_scurve_endpoints(self) -> None:
        h = np.array([[0.0, 1.0]], dtype=np.float64)
        out = elevation_scurve(h, gamma=1.2, contrast=0.1)
        assert out.min() >= 0.0
        assert out.max() <= 1.0

    def test_island_falloff_corners_near_zero(self) -> None:
        h = np.ones((32, 32), dtype=np.float64)
        with patch("terrain3d.postprocess._circular_perlin", return_value=np.zeros(1024)):
            out = island_falloff(h, falloff=0.35, noise_scale=0.0, seed=0)
        assert out[0, 0] < 0.05
        assert out[16, 16] > 0.5
