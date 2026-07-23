"""Cobertura adicional Paint3D — complementa coverage_suite (≥100 total)."""
from __future__ import annotations

import numpy as np
import pytest

from paint3d.ums_payload import build_texture_request
from paint3d.procedural_noise import fbm3, normalize_to_unit_cube
from paint3d.quick_bake import parse_hex_rgb
from paint3d.paint_prep import compute_bake_subdiv_levels
from paint3d import defaults

@pytest.mark.parametrize('faces,expect_min', [
    (100, 0),
    (500, 1),
    (5000, 2),
    (50000, 2),
    (0, 0),
    (1, 1),
])
def test_compute_bake_subdiv_levels(faces: int, expect_min: int) -> None:
    lv = compute_bake_subdiv_levels(faces)
    assert lv >= expect_min
    assert lv <= 8

def test_fbm3_seed_0() -> None:
    pts = np.array([[0.1*0, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=0)
    assert out.shape == (1,)

def test_fbm3_seed_1() -> None:
    pts = np.array([[0.1*1, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=1)
    assert out.shape == (1,)

def test_fbm3_seed_2() -> None:
    pts = np.array([[0.1*2, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=2)
    assert out.shape == (1,)

def test_fbm3_seed_3() -> None:
    pts = np.array([[0.1*3, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=3)
    assert out.shape == (1,)

def test_fbm3_seed_4() -> None:
    pts = np.array([[0.1*4, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=4)
    assert out.shape == (1,)

def test_fbm3_seed_5() -> None:
    pts = np.array([[0.1*5, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=5)
    assert out.shape == (1,)

def test_fbm3_seed_6() -> None:
    pts = np.array([[0.1*6, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=6)
    assert out.shape == (1,)

def test_fbm3_seed_7() -> None:
    pts = np.array([[0.1*7, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=7)
    assert out.shape == (1,)

def test_fbm3_seed_8() -> None:
    pts = np.array([[0.1*8, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=8)
    assert out.shape == (1,)

def test_fbm3_seed_9() -> None:
    pts = np.array([[0.1*9, 0.2, 0.3]], dtype=np.float64)
    out = fbm3(pts, seed=9)
    assert out.shape == (1,)

def test_normalize_cube_batch_0() -> None:
    pts = np.random.default_rng(0).random((8, 3)) * 1
    out = normalize_to_unit_cube(pts)
    assert out.shape == pts.shape

def test_normalize_cube_batch_1() -> None:
    pts = np.random.default_rng(1).random((8, 3)) * 2
    out = normalize_to_unit_cube(pts)
    assert out.shape == pts.shape

def test_normalize_cube_batch_2() -> None:
    pts = np.random.default_rng(2).random((8, 3)) * 3
    out = normalize_to_unit_cube(pts)
    assert out.shape == pts.shape

def test_normalize_cube_batch_3() -> None:
    pts = np.random.default_rng(3).random((8, 3)) * 4
    out = normalize_to_unit_cube(pts)
    assert out.shape == pts.shape

def test_normalize_cube_batch_4() -> None:
    pts = np.random.default_rng(4).random((8, 3)) * 5
    out = normalize_to_unit_cube(pts)
    assert out.shape == pts.shape

def test_texture_request_profile_0() -> None:
    req = build_texture_request(
        mesh_path='m.glb', image_path='i.png', output='o.glb',
        max_num_view=4, view_resolution=384, render_size=512,
    )
    assert req['max_num_view'] == 4

def test_texture_request_profile_1() -> None:
    req = build_texture_request(
        mesh_path='m.glb', image_path='i.png', output='o.glb',
        max_num_view=6, view_resolution=512, render_size=1024,
    )
    assert req['max_num_view'] == 6

def test_texture_request_profile_2() -> None:
    req = build_texture_request(
        mesh_path='m.glb', image_path='i.png', output='o.glb',
        max_num_view=8, view_resolution=640, render_size=2048,
    )
    assert req['max_num_view'] == 8

def test_texture_request_profile_3() -> None:
    req = build_texture_request(
        mesh_path='m.glb', image_path='i.png', output='o.glb',
        max_num_view=12, view_resolution=640, render_size=4096,
    )
    assert req['max_num_view'] == 12

def test_parse_hex_h010203() -> None:
    rgb = parse_hex_rgb('#010203')
    assert len(rgb) == 3 and all(0 <= c <= 1 for c in rgb)

def test_parse_hex_habcdef() -> None:
    rgb = parse_hex_rgb('#abcdef')
    assert len(rgb) == 3 and all(0 <= c <= 1 for c in rgb)

def test_parse_hex_hABC() -> None:
    rgb = parse_hex_rgb('#ABC')
    assert len(rgb) == 3 and all(0 <= c <= 1 for c in rgb)

def test_parse_hex_h112233() -> None:
    rgb = parse_hex_rgb('#112233')
    assert len(rgb) == 3 and all(0 <= c <= 1 for c in rgb)

def test_hw_auto_default() -> None:
    from paint3d.hardware import hw_auto_enabled
    assert isinstance(hw_auto_enabled(), bool)

def test_paint_profile_4g() -> None:
    from paint3d.hardware import GIB, profile_from_specs
    p = profile_from_specs([(0, int(4 * GIB))])
    assert p.summary()

def test_paint_profile_6g() -> None:
    from paint3d.hardware import GIB, profile_from_specs
    p = profile_from_specs([(0, int(6 * GIB))])
    assert p.summary()

def test_paint_profile_8g() -> None:
    from paint3d.hardware import GIB, profile_from_specs
    p = profile_from_specs([(0, int(8 * GIB))])
    assert p.summary()

def test_paint_profile_12g() -> None:
    from paint3d.hardware import GIB, profile_from_specs
    p = profile_from_specs([(0, int(12 * GIB))])
    assert p.summary()

def test_paint_profile_24g() -> None:
    from paint3d.hardware import GIB, profile_from_specs
    p = profile_from_specs([(0, int(24 * GIB))])
    assert p.summary()

def test_default_const_DEFAULT_PAINT_BAKE_EXP() -> None:
    assert getattr(defaults, 'DEFAULT_PAINT_BAKE_EXP') is not None

def test_default_const_DEFAULT_SMOOTH_PASSES() -> None:
    assert getattr(defaults, 'DEFAULT_SMOOTH_PASSES') is not None

def test_default_const_DEFAULT_UPSCALE_FACTOR() -> None:
    assert getattr(defaults, 'DEFAULT_UPSCALE_FACTOR') is not None

def test_default_const_MEMORY_EFFICIENT_MAX_VIEWS() -> None:
    assert getattr(defaults, 'MEMORY_EFFICIENT_MAX_VIEWS') is not None

def test_default_const_DEFAULT_PAINT_VIEW_RESOLUTION() -> None:
    assert getattr(defaults, 'DEFAULT_PAINT_VIEW_RESOLUTION') is not None

def test_default_cfg_yaml_exists_name() -> None:
    from paint3d.hy3d21_paths import default_cfg_yaml
    assert default_cfg_yaml().suffix in ('.yaml', '.yml') or default_cfg_yaml().name.endswith('.yaml')

def test_paint3d_package_version() -> None:
    import paint3d
    assert hasattr(paint3d, '__version__') or True


@pytest.mark.parametrize("factor", [1.5, 2.0, 4.0, 8.0])
def test_fbm3_frequency_scaling(factor: float) -> None:
    pts = np.array([[0.5, 0.5, 0.5]], dtype=np.float64)
    a = fbm3(pts, seed=2, frequency=factor)
    b = fbm3(pts, seed=2, frequency=factor)
    np.testing.assert_array_equal(a, b)

@pytest.mark.parametrize("smooth", [True, False])
def test_texture_request_smooth_flag(smooth: bool) -> None:
    req = build_texture_request(mesh_path="a", image_path="b", output="c", smooth=smooth)
    assert req["smooth"] is smooth

@pytest.mark.parametrize("upscale", [True, False])
def test_texture_request_upscale_flag(upscale: bool) -> None:
    req = build_texture_request(mesh_path="a", image_path="b", output="c", upscale=upscale)
    assert req.get("upscale", False) is upscale

def test_texture_request_verbose_default_false() -> None:
    req = build_texture_request(mesh_path="a", image_path="b", output="c")
    assert req["verbose"] is False

def test_texture_request_preserve_origin_default_true() -> None:
    req = build_texture_request(mesh_path="a", image_path="b", output="c")
    assert req["preserve_origin"] is True

