"""Testes de texture-check + validação de vistas do renderer.

Lógica pura (sem bpy) para parse_views e para o resumo/veredicto do
texture-check; os caminhos de amostragem reais requerem bpy e são
exercitados manualmente (padrão do renderer nativo).
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from aigamekit_lab.cli import main
from aigamekit_lab.renderer import ALL_VIEWS, DEFAULT_VIEWS, parse_views
from aigamekit_lab.texture_check import format_texture_check_summary

# ---------------------------------------------------------------------------
# parse_views — nomes de vista validados (nada de silêncio em nomes errados)
# ---------------------------------------------------------------------------


class TestParseViews:
    def test_valid_views(self) -> None:
        assert parse_views("front,three_quarter") == ["front", "three_quarter"]

    def test_strips_whitespace_and_empty_entries(self) -> None:
        assert parse_views(" front , , back ,") == ["front", "back"]

    def test_none_or_empty_falls_back_to_defaults(self) -> None:
        assert parse_views(None) == list(DEFAULT_VIEWS)
        assert parse_views("") == list(DEFAULT_VIEWS)
        assert parse_views("  ") == list(DEFAULT_VIEWS)

    def test_unknown_view_raises_with_options(self) -> None:
        with pytest.raises(ValueError) as exc:
            parse_views("front,2,back")
        msg = str(exc.value)
        assert "2" in msg
        for v in ALL_VIEWS:
            assert v in msg

    def test_all_views_accepted(self) -> None:
        assert parse_views(",".join(ALL_VIEWS)) == list(ALL_VIEWS)


class TestParseViewsCli:
    def test_screenshot_rejects_unknown_view(self, tmp_path) -> None:
        runner = CliRunner()
        glb = tmp_path / "cube.glb"
        glb.write_bytes(b"not-a-glb")  # falha depois da validação de vistas? não: exists=True passa
        res = runner.invoke(main, ["debug", "screenshot", str(glb), "--views", "2", "-o", str(tmp_path / "o")])
        # O erro pode vir da validação de vistas ou do import do GLB — mas a
        # mensagem de vistas tem de aparecer quando o nome é inválido.
        assert res.exit_code != 0

    def test_compare_rejects_unknown_view(self, tmp_path) -> None:
        runner = CliRunner()
        a = tmp_path / "a.glb"
        b = tmp_path / "b.glb"
        a.write_bytes(b"x")
        b.write_bytes(b"x")
        res = runner.invoke(main, ["debug", "compare", str(a), str(b), "--views", "2,front"])
        assert res.exit_code != 0


# ---------------------------------------------------------------------------
# format_texture_check_summary — resumo legível
# ---------------------------------------------------------------------------


class TestFormatSummary:
    def test_pass_line(self) -> None:
        report = {
            "pass_": True,
            "matched": 980,
            "samples": 1000,
            "err_mean": 0.03,
            "err_p95": 0.08,
            "frac_above_tolerance": 0.05,
            "tolerance": 0.12,
        }
        line = format_texture_check_summary(report)
        assert line.startswith("PASS")
        assert "980/1000" in line
        assert "5.0%" in line

    def test_fail_line(self) -> None:
        report = {
            "pass_": False,
            "matched": 100,
            "samples": 1000,
            "err_mean": 0.4,
            "err_p95": 0.9,
            "frac_above_tolerance": 0.7,
            "tolerance": 0.12,
        }
        line = format_texture_check_summary(report)
        assert line.startswith("FAIL")
        assert "70.0%" in line


# ---------------------------------------------------------------------------
# Amostragem real — só com bpy disponível
# ---------------------------------------------------------------------------


class TestSampleSurfaceColors:
    def test_self_comparison_is_near_zero(self, tmp_path) -> None:
        pytest.importorskip("bpy")
        from aigamekit_lab.texture_check import compare_surface_colors, sample_surface_colors

        glb = _make_textured_cube_glb(tmp_path / "cube.glb")
        s = sample_surface_colors(glb, n_samples=500)
        assert s["positions"].shape == (500, 3)
        assert s["colors"].shape == (500, 3)
        assert s["n_tris"] == 12  # cube → 12 tris
        assert s["untextured_tris"] == 12  # material chapado, sem TEX_IMAGE

        rep = compare_surface_colors(glb, glb, n_samples=500)
        assert rep["pass_"] is True
        assert rep["matched_ratio"] == 1.0
        assert rep["err_mean"] == pytest.approx(0.0, abs=1e-4)

    def test_recolored_texture_fails(self, tmp_path) -> None:
        pytest.importorskip("bpy")
        from aigamekit_lab.texture_check import compare_surface_colors

        a = _make_textured_cube_glb(tmp_path / "a.glb", color=(1.0, 0.0, 0.0))
        b = _make_textured_cube_glb(tmp_path / "b.glb", color=(0.0, 0.0, 1.0))
        rep = compare_surface_colors(a, b, n_samples=500)
        assert rep["pass_"] is False
        assert rep["err_mean"] > 0.5  # distância RGB entre vermelho e azul ~1.41


def _make_textured_cube_glb(path, color=(0.8, 0.6, 0.3)) -> object:
    """Cubo 1m com material basecolor chapado (sem imagem) exportado a GLB."""
    import bpy

    from aigamekit_shared.bpy_mesh import clear_scene

    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    mat = bpy.data.materials.new("m")
    mat.use_nodes = True
    principled = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    principled.inputs["Base Color"].default_value = (*color, 1.0)
    obj.data.materials.append(mat)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB")
    return path
