"""Testes do módulo viz — funções puras (sem bpy) + contratos CLI + smoke bpy.

Segue a convenção de ``test_renderer_native.py``: lógica pura e contratos CLI
correm sem bpy; o smoke real com bpy é auto-skip quando bpy não está instalado.
"""

from __future__ import annotations

import inspect

import pytest
from click.testing import CliRunner

from aigamekit_lab.cli import main
from aigamekit_lab.viz import (
    MAX_INFLUENCES_HIGHLIGHT,
    VIZ_MODES,
    WEIGHT_VIEWS,
    attach_panel,
    bone_palette,
    influence_count_color,
    make_colorbar,
    make_legend,
    normal_to_rgb,
    sample_indices,
)

# ---------------------------------------------------------------------------
# normal_to_rgb
# ---------------------------------------------------------------------------


class TestNormalToRgb:
    def test_axis_x(self) -> None:
        assert normal_to_rgb(1, 0, 0) == pytest.approx((1.0, 0.5, 0.5))

    def test_axis_y(self) -> None:
        assert normal_to_rgb(0, 1, 0) == pytest.approx((0.5, 1.0, 0.5))

    def test_axis_z_negative(self) -> None:
        assert normal_to_rgb(0, 0, -1) == pytest.approx((0.5, 0.5, 0.0))

    def test_clamps_out_of_range(self) -> None:
        r, g, b = normal_to_rgb(2.0, -2.0, 0.0)
        assert (r, g, b) == pytest.approx((1.0, 0.0, 0.5))


# ---------------------------------------------------------------------------
# bone_palette
# ---------------------------------------------------------------------------


class TestBonePalette:
    def test_deterministic(self) -> None:
        names = ["spine", "head", "arm_l", "arm_r"]
        assert bone_palette(names) == bone_palette(list(reversed(names)))

    def test_distinct_colors(self) -> None:
        palette = bone_palette([f"bone_{i:02d}" for i in range(24)])
        assert len(set(palette.values())) == 24

    def test_channels_in_unit_range(self) -> None:
        for rgb in bone_palette(["a", "b", "c"]).values():
            assert all(0.0 <= c <= 1.0 for c in rgb)


# ---------------------------------------------------------------------------
# influence_count_color
# ---------------------------------------------------------------------------


class TestInfluenceCountColor:
    def test_zero_is_gray(self) -> None:
        assert influence_count_color(0) == (0.5, 0.5, 0.5)

    def test_one_is_blue(self) -> None:
        assert influence_count_color(1) == pytest.approx((0.0, 0.0, 1.0))

    def test_max_is_red(self) -> None:
        assert influence_count_color(MAX_INFLUENCES_HIGHLIGHT) == pytest.approx((1.0, 0.0, 0.0))

    def test_over_limit_is_magenta(self) -> None:
        assert influence_count_color(MAX_INFLUENCES_HIGHLIGHT + 1) == (1.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# sample_indices
# ---------------------------------------------------------------------------


class TestSampleIndices:
    def test_all_when_small(self) -> None:
        assert sample_indices(5, 10) == [0, 1, 2, 3, 4]

    def test_subsample_size(self) -> None:
        idx = sample_indices(41107, 2000)
        assert len(idx) == 2000
        assert idx[0] == 0
        assert idx[-1] < 41107

    def test_sorted_unique(self) -> None:
        idx = sample_indices(1000, 100)
        assert idx == sorted(set(idx))

    def test_empty_inputs(self) -> None:
        assert sample_indices(0, 10) == []
        assert sample_indices(10, 0) == []


# ---------------------------------------------------------------------------
# Pillow panels
# ---------------------------------------------------------------------------


class TestPanels:
    def test_make_legend_dimensions(self) -> None:
        img = make_legend([("boundary", (1.0, 0.0, 0.0)), ("non-manifold", (1.0, 0.55, 0.0))], title="edges")
        assert img.mode == "RGBA"
        assert img.width > 20
        assert img.height > 3 * 12

    def test_make_colorbar_gradient(self) -> None:
        img = make_colorbar([(0.0, 0.0, 1.0), (1.0, 0.0, 0.0)], labels=("0", "1"), title="peso")
        assert img.mode == "RGBA"
        # Gradiente azul→vermelho: canto esquerdo mais azul que o direito.
        y = img.height - 14 - 14 - 4  # dentro da barra
        left = img.getpixel((8, y))
        right = img.getpixel((img.width - 8, y))
        assert left[2] > left[0]
        assert right[0] > right[2]

    def test_attach_panel_composites(self, tmp_path) -> None:
        from PIL import Image

        base = tmp_path / "base.png"
        Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(base)
        panel = make_legend([("x", (0.0, 1.0, 0.0))])
        attach_panel(base, panel)
        out = Image.open(base)
        assert out.size == (128, 128)
        assert out.getbbox() is not None  # painel colado → deixou de estar vazio


# ---------------------------------------------------------------------------
# Contratos — render_viz signature + CLI
# ---------------------------------------------------------------------------


class TestRenderVizContract:
    def test_signature(self) -> None:
        from aigamekit_lab.viz import render_viz

        params = inspect.signature(render_viz).parameters
        for pos in ("glb_path", "output_dir", "mode"):
            assert pos in params
        for kw in (
            "views",
            "resolution",
            "engine",
            "ortho",
            "transparent_film",
            "sample",
            "arrow_length",
            "bone",
            "weights_view",
            "wireframe",
            "world_space",
        ):
            assert kw in params, f"param {kw} em falta"
            assert params[kw].kind == inspect.Parameter.KEYWORD_ONLY

    def test_mode_constants(self) -> None:
        assert set(VIZ_MODES) == {"normals", "normals-arrows", "orientation", "uv", "edges", "weights"}
        assert set(WEIGHT_VIEWS) == {"dominant", "count", "unweighted", "bone"}

    def test_invalid_mode_raises(self, tmp_path) -> None:
        from aigamekit_lab.viz import render_viz

        with pytest.raises(ValueError, match="modo inválido"):
            render_viz(tmp_path / "x.glb", tmp_path, "nope")

    def test_bone_view_requires_bone(self, tmp_path) -> None:
        from aigamekit_lab.viz import render_viz

        with pytest.raises(ValueError, match="--bone"):
            render_viz(tmp_path / "x.glb", tmp_path, "weights", weights_view="bone")


class TestCliVizCommand:
    def test_viz_help(self) -> None:
        r = CliRunner().invoke(main, ["debug", "viz", "--help"])
        assert r.exit_code == 0
        for flag in ("--mode", "--sample", "--bone", "--weights-view", "--wireframe", "--world-space"):
            assert flag in r.output, f"flag {flag} em falta no help"

    def test_viz_mode_choices(self) -> None:
        r = CliRunner().invoke(main, ["debug", "viz", "--help"])
        for mode in VIZ_MODES:
            assert mode in r.output


# ---------------------------------------------------------------------------
# Smoke bpy — cubo simples gerado in-memory (auto-skip sem bpy)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cube_glb(tmp_path_factory) -> str:
    bpy = pytest.importorskip("bpy")
    from aigamekit_shared.bpy_mesh import clear_scene

    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    path = tmp_path_factory.mktemp("viz") / "cube.glb"
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB")
    return str(path)


class TestVizSmoke:
    @pytest.mark.parametrize("mode", ["normals", "normals-arrows", "orientation", "uv", "edges"])
    def test_static_modes_produce_pngs(self, mode: str, cube_glb: str, tmp_path) -> None:
        pytest.importorskip("bpy")
        from aigamekit_lab.viz import render_viz

        report = render_viz(cube_glb, tmp_path / mode, mode, views="front", resolution=64)
        shots = report["screenshots"]
        assert len(shots) == 1
        from pathlib import Path

        assert Path(shots[0]["path"]).stat().st_size > 0
        assert report["mode"] == mode

    def test_edges_metrics_closed_cube(self, cube_glb: str, tmp_path) -> None:
        pytest.importorskip("bpy")
        from aigamekit_lab.viz import render_viz

        report = render_viz(cube_glb, tmp_path / "edges", "edges", views="front", resolution=64)
        assert report["metrics"]["boundary_edges"] == 0
        assert report["metrics"]["nonmanifold_edges"] == 0

    def test_weights_requires_rig(self, cube_glb: str, tmp_path) -> None:
        pytest.importorskip("bpy")
        from aigamekit_lab.viz import render_viz

        with pytest.raises(ValueError, match="vertex groups"):
            render_viz(cube_glb, tmp_path / "w", "weights", views="front", resolution=64)

    def test_wireframe_overlay_renders(self, cube_glb: str, tmp_path) -> None:
        pytest.importorskip("bpy")
        from pathlib import Path

        from aigamekit_lab.viz import render_viz

        report = render_viz(cube_glb, tmp_path / "wire", "normals", views="front", resolution=64, wireframe=True)
        assert Path(report["screenshots"][0]["path"]).stat().st_size > 0
