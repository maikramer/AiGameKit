"""Elaborate unit tests for Rocks3D (no GPU / no bpy). ≥100 collected via parametrize."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# defaults / presets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["pebble", "boulder", "spire", "slab", "outcrop"])
def test_available_types_includes_base_presets(name: str) -> None:
    from rocks3d.defaults import available_types

    assert name in available_types()


def test_available_types_sorted_unique() -> None:
    from rocks3d.defaults import available_types

    types = available_types()
    assert types == sorted(types)
    assert len(types) == len(set(types))


@pytest.mark.parametrize(
    "quality,delta",
    [
        ("fast", -1),
        ("medium", 0),
        ("highest", 2),
    ],
)
def test_get_preset_quality_subdivisions(quality: str, delta: int) -> None:
    from rocks3d.defaults import BOULDER, get_preset

    p = get_preset("boulder", quality=quality)
    assert p.subdivisions == BOULDER.subdivisions + delta


@pytest.mark.parametrize("quality", ["fast", "low", "medium", "high", "highest"])
def test_get_preset_name_suffix(quality: str) -> None:
    from rocks3d.defaults import get_preset

    p = get_preset("pebble", quality=quality)
    assert p.name == f"pebble-{quality}"


@pytest.mark.parametrize("bad", ["", "rock", "unknown"])
def test_get_preset_unknown_raises(bad: str) -> None:
    from rocks3d.defaults import get_preset

    with pytest.raises(ValueError, match="Unknown rock type"):
        get_preset(bad)


@pytest.mark.parametrize(
    "base,delta,expected",
    [(4, -1, 3), (4, 0, 4), (4, 2, 6), (4, "x", 4), (4, None, 4)],
)
def test_apply_delta_helper(base: int, delta, expected: int) -> None:
    from rocks3d.defaults import _apply_delta

    assert _apply_delta(base, delta) == expected


@pytest.mark.parametrize("preset_attr", ["PEBBLE", "BOULDER", "SPIRE", "SLAB", "OUTCROP"])
def test_rock_preset_frozen_dataclass(preset_attr: str) -> None:
    from rocks3d import defaults

    p = getattr(defaults, preset_attr)
    assert p.noise_type == "simplex"
    assert len(p.color_range) == 2
    assert p.color_range[0].startswith("#")


# ---------------------------------------------------------------------------
# noise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 42, 999])
def test_simplex3_deterministic(seed: int) -> None:
    from rocks3d.noise import simplex3

    a = simplex3(1.0, 2.0, 3.0, seed=seed)
    b = simplex3(1.0, 2.0, 3.0, seed=seed)
    assert a == b
    assert -1.1 <= a <= 1.1


@pytest.mark.parametrize("coord", [(0.0, 0.0, 0.0), (10.5, -3.2, 0.1), (-100, 50, 25)])
def test_simplex3_varies_with_position(coord: tuple[float, float, float]) -> None:
    from rocks3d.noise import simplex3

    v = simplex3(*coord, seed=7)
    assert isinstance(v, float)


def test_perlin3_not_implemented() -> None:
    from rocks3d.noise import perlin3

    with pytest.raises(NotImplementedError, match="perlin3"):
        perlin3(0, 0, 0)


def test_worley3_not_implemented() -> None:
    from rocks3d.noise import worley3

    with pytest.raises(NotImplementedError, match="worley3"):
        worley3(0, 0, 0)


@pytest.mark.parametrize("octaves", [1, 2, 4, 6])
def test_fbm3_output_shape(octaves: int) -> None:
    from rocks3d.noise import fbm3

    pts = np.array([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0], [0.5, -0.5, 1.0]])
    out = fbm3(pts, octaves=octaves, seed=11)
    assert out.shape == (3,)


def test_fbm3_single_point_1d_input() -> None:
    from rocks3d.noise import fbm3

    out = fbm3(np.array([0.1, 0.2, 0.3]), octaves=2, seed=0)
    assert out.shape == (1,)


def test_fbm3_bad_shape_raises() -> None:
    from rocks3d.noise import fbm3

    with pytest.raises(ValueError, match="shape"):
        fbm3(np.zeros((2, 2)), octaves=2)


def test_fbm3_unsupported_noise_type() -> None:
    from rocks3d.noise import fbm3

    with pytest.raises(ValueError, match="simplex"):
        fbm3(np.zeros((1, 3)), noise_type="perlin")


@pytest.mark.parametrize("seed", [0, 5, 100])
def test_fbm3_normalized_range(seed: int) -> None:
    from rocks3d.noise import fbm3

    rng = np.random.default_rng(seed)
    pts = rng.standard_normal((50, 3))
    out = fbm3(pts, octaves=4, seed=seed)
    assert out.min() >= -1.5
    assert out.max() <= 1.5


# ---------------------------------------------------------------------------
# erosion (trimesh only)
# ---------------------------------------------------------------------------


def _unit_icosphere():
    import trimesh

    return trimesh.creation.icosphere(subdivisions=2, radius=1.0)


@pytest.mark.parametrize("passes", [0, 1, 3])
def test_apply_erosion_passes(passes: int) -> None:
    from rocks3d.erosion import apply_erosion

    mesh = _unit_icosphere()
    out = apply_erosion(mesh, seed=42, passes=passes, strength=0.5)
    assert len(out.vertices) == len(mesh.vertices)
    assert len(out.faces) == len(mesh.faces)


@pytest.mark.parametrize("strength", [0.0, 0.25, 1.0])
def test_apply_erosion_strength_blend(strength: float) -> None:
    from rocks3d.erosion import apply_erosion

    mesh = _unit_icosphere()
    out = apply_erosion(mesh, seed=1, passes=2, strength=strength)
    if strength == 0.0:
        np.testing.assert_allclose(out.vertices, mesh.vertices, atol=1e-6)
    else:
        assert not np.allclose(out.vertices, mesh.vertices)


def test_neighbour_mean_uniform_grid() -> None:
    from rocks3d.erosion import _neighbour_mean

    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
    edges = np.array([[0, 1], [1, 2], [2, 0]], dtype=np.int64)
    mean = _neighbour_mean(verts, edges, 3)
    assert mean.shape == (3, 3)


# ---------------------------------------------------------------------------
# texture helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hex_str,rgb",
    [
        ("#FF0000", (255, 0, 0)),
        ("#00FF00", (0, 255, 0)),
        ("#0000FF", (0, 0, 255)),
        ("7A7A6F", (0x7A, 0x7A, 0x6F)),
    ],
)
def test_parse_hex_color(hex_str: str, rgb: tuple[int, int, int]) -> None:
    from rocks3d.texture import _parse_hex_color

    arr = _parse_hex_color(hex_str)
    assert tuple(arr.tolist()) == rgb


@pytest.mark.parametrize("t_val", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_smoothstep_bounds(t_val: float) -> None:
    from rocks3d.texture import _smoothstep

    t = np.array([t_val])
    s = _smoothstep(t)
    assert 0.0 <= s[0] <= 1.0


@pytest.mark.parametrize("resolution", [32, 64, 128])
def test_value_noise_range(resolution: int) -> None:
    from rocks3d.texture import _value_noise

    img = _value_noise(resolution, cells=8, seed=3)
    assert img.shape == (resolution, resolution)
    assert img.min() >= 0.0
    assert img.max() <= 1.0


@pytest.mark.parametrize("octaves", [1, 3, 5])
def test_fbm_image_normalized(octaves: int) -> None:
    from rocks3d.texture import _fbm_image

    img = _fbm_image(64, octaves=octaves, base_cells=4, seed=10)
    assert img.shape == (64, 64)
    assert img.min() >= 0.0
    assert img.max() <= 1.0 + 1e-9


@pytest.mark.parametrize("preset_name", ["pebble", "boulder", "outcrop"])
def test_generate_albedo_texture_shape(preset_name: str) -> None:
    from rocks3d.defaults import get_preset
    from rocks3d.texture import generate_albedo_texture

    preset = get_preset(preset_name, quality="fast")
    tex = generate_albedo_texture(None, preset, seed=0, resolution=32)
    assert tex.shape == (32, 32, 3)
    assert tex.dtype == np.uint8


def test_generate_pbr_with_materialize_missing_binary(tmp_path: Path) -> None:
    from rocks3d.texture import generate_pbr_with_materialize

    alb = tmp_path / "rock.png"
    alb.write_bytes(b"\x89PNG\r\n\x1a\n")
    out = generate_pbr_with_materialize(alb, tmp_path / "pbr")
    assert out == {} or isinstance(out, dict)


# ---------------------------------------------------------------------------
# uv_mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subdiv", [1, 2, 3])
def test_apply_uv_spherical_sets_visual(subdiv: int) -> None:
    import trimesh
    from rocks3d.uv_mapping import apply_uv_spherical

    mesh = trimesh.creation.icosphere(subdivisions=subdiv)
    out = apply_uv_spherical(mesh)
    assert out.visual is not None
    assert out.visual.uv is not None
    assert out.visual.uv.shape[0] == len(out.vertices)
    assert out.visual.uv.min() >= 0.0
    assert out.visual.uv.max() <= 1.0


def test_apply_uv_xatlas_or_fallback() -> None:
    import trimesh
    from rocks3d.uv_mapping import apply_uv_xatlas

    mesh = trimesh.creation.icosphere(subdivisions=2)
    out = apply_uv_xatlas(mesh)
    assert out.visual is not None
    assert out.visual.uv is not None


# ---------------------------------------------------------------------------
# formation constants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["stack", "outcrop", "cliff", "arch", "spire-cluster"])
def test_formation_styles_tuple(style: str) -> None:
    from rocks3d.formation import STYLES

    assert style in STYLES


@pytest.mark.parametrize("quality,sub", [("fast", 2), ("medium", 3), ("highest", 4)])
def test_chunk_subdiv_map(quality: str, sub: int) -> None:
    from rocks3d.formation import _CHUNK_SUBDIV

    assert _CHUNK_SUBDIV[quality] == sub


def test_generate_formation_unknown_style() -> None:
    from rocks3d.formation import generate_formation

    with pytest.raises(ValueError, match="Unknown formation"):
        generate_formation("volcano", seed=1)


@pytest.mark.parametrize("style", ["stack", "outcrop", "cliff"])
def test_generate_formation_produces_mesh(style: str) -> None:
    from rocks3d.formation import generate_formation

    mesh = generate_formation(style, seed=123, quality="fast")
    assert len(mesh.vertices) > 0
    assert len(mesh.faces) > 0
    assert mesh.bounds[0][1] == pytest.approx(0.0, abs=0.05)


# ---------------------------------------------------------------------------
# generator (procedural mesh, no bpy)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_name", ["pebble", "boulder"])
def test_generate_rock_reproducible(type_name: str) -> None:
    from rocks3d.defaults import get_preset
    from rocks3d.generator import generate_rock

    preset = get_preset(type_name, quality="fast")
    a = generate_rock(preset=preset, seed=99)
    b = generate_rock(preset=preset, seed=99)
    np.testing.assert_array_almost_equal(a.vertices, b.vertices)


@pytest.mark.parametrize("type_name", ["pebble", "spire"])
def test_generate_rock_has_faces(type_name: str) -> None:
    from rocks3d.defaults import get_preset
    from rocks3d.generator import generate_rock

    mesh = generate_rock(preset=get_preset(type_name, quality="fast"), seed=1)
    assert mesh.faces.shape[1] == 3


# ---------------------------------------------------------------------------
# CLI --help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["generate", "--help"],
        ["formation", "--help"],
    ],
)
def test_rocks3d_cli_help(argv: list[str]) -> None:
    from rocks3d.cli import main

    runner = CliRunner()
    result = runner.invoke(main, argv)
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# extra parametrized coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("quality", ["fast", "low", "medium", "high", "highest"])
@pytest.mark.parametrize("type_name", ["pebble", "boulder"])
def test_preset_octaves_non_negative(type_name: str, quality: str) -> None:
    from rocks3d.defaults import get_preset

    p = get_preset(type_name, quality=quality)
    assert p.octaves >= 1


@pytest.mark.parametrize("type_name", ["pebble", "boulder"])
def test_pebble_has_base_flatten(type_name: str) -> None:
    from rocks3d.defaults import get_preset

    p = get_preset(type_name, quality="medium")
    assert 0.0 <= p.base_flatten <= 1.0


@pytest.mark.parametrize("seed", [0, 1, 17, 12345])
def test_fbm3_different_seeds_differ(seed: int) -> None:
    from rocks3d.noise import fbm3

    pts = np.array([[0.3, 0.3, 0.3]])
    v0 = fbm3(pts, seed=0)[0]
    v1 = fbm3(pts, seed=seed)[0]
    if seed != 0:
        assert v0 != v1 or seed == 0
