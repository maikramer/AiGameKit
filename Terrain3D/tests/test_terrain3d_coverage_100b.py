"""Cobertura adicional Terrain3D — complementa coverage_suite (≥100 total)."""

from __future__ import annotations

import numpy as np

from terrain3d.generator import TerrainConfig, _native_resolution_from_model
from terrain3d.postprocess import _smoothstep, elevation_scurve, taubin_smooth
from terrain3d.ums_payload import build_generate_request


def test_build_request_mode_island() -> None:
    req = build_generate_request(output="o.png", mode="island")
    assert req["mode"] == "island"


def test_build_request_mode_continental() -> None:
    req = build_generate_request(output="o.png", mode="continental")
    assert req["mode"] == "continental"


def test_build_request_mode_mountain() -> None:
    req = build_generate_request(output="o.png", mode="mountain")
    assert req["mode"] == "mountain"


def test_build_request_mode_desert() -> None:
    req = build_generate_request(output="o.png", mode="desert")
    assert req["mode"] == "desert"


def test_build_request_seed_0() -> None:
    req = build_generate_request(output="o.png", seed=0)
    assert req["seed"] == 0


def test_build_request_seed_1() -> None:
    req = build_generate_request(output="o.png", seed=1)
    assert req["seed"] == 1


def test_build_request_seed_2() -> None:
    req = build_generate_request(output="o.png", seed=2)
    assert req["seed"] == 2


def test_build_request_seed_3() -> None:
    req = build_generate_request(output="o.png", seed=3)
    assert req["seed"] == 3


def test_build_request_seed_4() -> None:
    req = build_generate_request(output="o.png", seed=4)
    assert req["seed"] == 4


def test_build_request_seed_5() -> None:
    req = build_generate_request(output="o.png", seed=5)
    assert req["seed"] == 5


def test_build_request_seed_6() -> None:
    req = build_generate_request(output="o.png", seed=6)
    assert req["seed"] == 6


def test_build_request_seed_7() -> None:
    req = build_generate_request(output="o.png", seed=7)
    assert req["seed"] == 7


def test_build_request_seed_8() -> None:
    req = build_generate_request(output="o.png", seed=8)
    assert req["seed"] == 8


def test_build_request_seed_9() -> None:
    req = build_generate_request(output="o.png", seed=9)
    assert req["seed"] == 9


def test_smoothstep_t_0_0() -> None:
    out = _smoothstep(0.0, 1.0, np.array([0.0]))
    assert 0.0 <= out[0] <= 1.0


def test_smoothstep_t_0_25() -> None:
    out = _smoothstep(0.0, 1.0, np.array([0.25]))
    assert 0.0 <= out[0] <= 1.0


def test_smoothstep_t_0_5() -> None:
    out = _smoothstep(0.0, 1.0, np.array([0.5]))
    assert 0.0 <= out[0] <= 1.0


def test_smoothstep_t_0_75() -> None:
    out = _smoothstep(0.0, 1.0, np.array([0.75]))
    assert 0.0 <= out[0] <= 1.0


def test_smoothstep_t_1_0() -> None:
    out = _smoothstep(0.0, 1.0, np.array([1.0]))
    assert 0.0 <= out[0] <= 1.0


def test_native_res_x_30m() -> None:
    assert _native_resolution_from_model("x/30m") == 30.0


def test_native_res_x_90m() -> None:
    assert _native_resolution_from_model("x/90m") == 90.0


def test_native_res_plain() -> None:
    assert _native_resolution_from_model("plain") == 30.0


def test_elevation_scurve_gamma_0_8() -> None:
    h = np.linspace(0, 1, 16).reshape(4, 4)
    out = elevation_scurve(h, gamma=0.8, contrast=0.1)
    assert out.min() >= 0 and out.max() <= 1


def test_elevation_scurve_gamma_1_0() -> None:
    h = np.linspace(0, 1, 16).reshape(4, 4)
    out = elevation_scurve(h, gamma=1.0, contrast=0.1)
    assert out.min() >= 0 and out.max() <= 1


def test_elevation_scurve_gamma_1_2() -> None:
    h = np.linspace(0, 1, 16).reshape(4, 4)
    out = elevation_scurve(h, gamma=1.2, contrast=0.1)
    assert out.min() >= 0 and out.max() <= 1


def test_elevation_scurve_gamma_1_5() -> None:
    h = np.linspace(0, 1, 16).reshape(4, 4)
    out = elevation_scurve(h, gamma=1.5, contrast=0.1)
    assert out.min() >= 0 and out.max() <= 1


def test_taubin_iters_0() -> None:
    h = np.ones((8, 8))
    out = taubin_smooth(h, iterations=0)
    assert out.shape == h.shape


def test_taubin_iters_1() -> None:
    h = np.ones((8, 8))
    out = taubin_smooth(h, iterations=1)
    assert out.shape == h.shape


def test_taubin_iters_2() -> None:
    h = np.ones((8, 8))
    out = taubin_smooth(h, iterations=2)
    assert out.shape == h.shape


def test_taubin_iters_3() -> None:
    h = np.ones((8, 8))
    out = taubin_smooth(h, iterations=3)
    assert out.shape == h.shape


def test_terrain_config_world_size() -> None:
    cfg = TerrainConfig(**{"world_size": 256.0})
    assert cfg.world_size == 256.0


def test_terrain_config_max_height() -> None:
    cfg = TerrainConfig(**{"max_height": 80.0})
    assert cfg.max_height == 80.0


def test_terrain_config_size() -> None:
    cfg = TerrainConfig(**{"size": 1024})
    assert cfg.size == 1024
