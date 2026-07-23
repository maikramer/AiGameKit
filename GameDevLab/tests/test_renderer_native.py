"""Testes do renderer nativo — lógica pura (sem bpy) + contratos CLI.

Cobre a rampa de cores Blue→Green→Red (extraída como função pura) e os
contratos CLI dos comandos novos/migrados (inspect-rig, inspect-material,
turntable, compare --overlay). Os comandos de rendering reais requerem
bpy e são exercitados manualmente.
"""

from __future__ import annotations

import inspect

import pytest
from click.testing import CliRunner

from gamedev_lab.cli import main
from gamedev_lab.renderer import weight_to_color

# ---------------------------------------------------------------------------
# weight_to_color — rampa Blue→Green→Red (pure function, no bpy)
# ---------------------------------------------------------------------------


class TestWeightToColor:
    """Valida a fórmula Blue→Green→Red nos pontos críticos."""

    def test_zero_is_blue(self) -> None:
        assert weight_to_color(0.0) == pytest.approx((0.0, 0.0, 1.0))

    def test_quarter_is_cyan_green(self) -> None:
        # w=0.25 → (0, 0.5, 0.5)
        r, g, b = weight_to_color(0.25)
        assert r == pytest.approx(0.0)
        assert g == pytest.approx(0.5)
        assert b == pytest.approx(0.5)

    def test_half_is_green(self) -> None:
        assert weight_to_color(0.5) == pytest.approx((0.0, 1.0, 0.0))

    def test_three_quarter_is_olive(self) -> None:
        # w=0.75 → (0.5, 0.5, 0)
        r, g, b = weight_to_color(0.75)
        assert r == pytest.approx(0.5)
        assert g == pytest.approx(0.5)
        assert b == pytest.approx(0.0)

    def test_one_is_red(self) -> None:
        assert weight_to_color(1.0) == pytest.approx((1.0, 0.0, 0.0))

    def test_no_yellow(self) -> None:
        """R e B nunca são ambos altos simultaneamente (sem amarelo puro)."""
        import numpy as np

        ws = np.linspace(0.0, 1.0, 101)
        for w in ws:
            r, _g, b = weight_to_color(float(w))
            # R alto só na segunda metade; B alto só na primeira.
            if r > 0.5:
                assert b < 0.5, f"w={w}: R={r} e B={b} ambos altos"
            if b > 0.5:
                assert r < 0.5, f"w={w}: R={r} e B={b} ambos altos"

    def test_r_monotonic_increasing(self) -> None:
        """R cresce monotonicamente de 0 a 1."""
        import numpy as np

        ws = np.linspace(0.0, 1.0, 101)
        prev_r = -1.0
        for w in ws:
            r, _g, _b = weight_to_color(float(w))
            assert r >= prev_r - 1e-9, f"R não-monotónico em w={w}"
            prev_r = r

    def test_b_monotonic_decreasing(self) -> None:
        """B decresce monotonicamente de 1 a 0."""
        import numpy as np

        ws = np.linspace(0.0, 1.0, 101)
        prev_b = 2.0
        for w in ws:
            _r, _g, b = weight_to_color(float(w))
            assert b <= prev_b + 1e-9, f"B não-monotónico em w={w}"
            prev_b = b

    def test_all_values_in_unit_range(self) -> None:
        """Todos os canais ficam em [0, 1]."""
        import numpy as np

        ws = np.linspace(-0.5, 1.5, 201)  # inclui clamping
        for w in ws:
            r, g, b = weight_to_color(float(w))
            assert 0.0 <= r <= 1.0, f"w={w}: R={r} fora de [0,1]"
            assert 0.0 <= g <= 1.0, f"w={w}: G={g} fora de [0,1]"
            assert 0.0 <= b <= 1.0, f"w={w}: B={b} fora de [0,1]"

    def test_clamps_below_zero(self) -> None:
        """Pesos negativos são clampados a 0 (azul)."""
        assert weight_to_color(-0.5) == pytest.approx((0.0, 0.0, 1.0))

    def test_clamps_above_one(self) -> None:
        """Pesos >1 são clampados a 1 (vermelho)."""
        assert weight_to_color(2.0) == pytest.approx((1.0, 0.0, 0.0))


# ---------------------------------------------------------------------------
# Contratos das funções de rendering (signature introspection, sem bpy)
# ---------------------------------------------------------------------------


class TestRenderFunctionSignatures:
    """Valida que as novas funções têm a assinatura esperada."""

    def test_render_weight_heatmap_signature(self) -> None:
        from gamedev_lab.renderer import render_weight_heatmap

        sig = inspect.signature(render_weight_heatmap)
        params = sig.parameters
        assert "glb_path" in params
        assert "output_dir" in params
        assert "bone_name" in params
        # keyword-only args após *
        assert params["bone_name"].kind != inspect.Parameter.KEYWORD_ONLY
        for kw in ("views", "resolution", "engine", "ortho", "transparent_film"):
            assert kw in params, f"param {kw} em falta"
            assert params[kw].kind == inspect.Parameter.KEYWORD_ONLY

    def test_render_turntable_signature(self) -> None:
        from gamedev_lab.renderer import render_turntable

        sig = inspect.signature(render_turntable)
        params = sig.parameters
        assert "glb_path" in params
        assert "output_path" in params
        for kw in ("frames", "resolution", "engine", "ortho", "transparent_film", "frame_duration_ms"):
            assert kw in params, f"param {kw} em falta"

    def test_render_inspect_material_signature(self) -> None:
        from gamedev_lab.renderer import render_inspect_material

        sig = inspect.signature(render_inspect_material)
        params = sig.parameters
        assert "glb_path" in params
        assert "output_dir" in params
        for kw in ("views", "resolution", "engine", "ortho", "transparent_film"):
            assert kw in params, f"param {kw} em falta"


class TestInspectMaterialsExported:
    """inspect_materials deve estar exportado em debug_tools."""

    def test_inspect_materials_in_all(self) -> None:
        from gamedev_lab.debug_tools import __all__

        assert "inspect_materials" in __all__

    def test_inspect_materials_callable(self) -> None:
        from gamedev_lab.debug_tools import inspect_materials

        assert callable(inspect_materials)


# ---------------------------------------------------------------------------
# Contratos CLI — help commands (sem bpy)
# ---------------------------------------------------------------------------


class TestCliNativeRendererCommands:
    """Valida que os comandos migrados/novos existem e respondem --help."""

    def test_debug_inspect_rig_help(self) -> None:
        r = CliRunner().invoke(main, ["debug", "inspect-rig", "--help"])
        assert r.exit_code == 0
        assert "native bpy" in r.output.lower()

    def test_debug_inspect_material_help(self) -> None:
        r = CliRunner().invoke(main, ["debug", "inspect-material", "--help"])
        assert r.exit_code == 0
        assert "materiais" in r.output.lower() or "material" in r.output.lower()

    def test_debug_turntable_help(self) -> None:
        r = CliRunner().invoke(main, ["debug", "turntable", "--help"])
        assert r.exit_code == 0
        assert "gif" in r.output.lower() or "turntable" in r.output.lower()

    def test_debug_compare_has_overlay_flag(self) -> None:
        r = CliRunner().invoke(main, ["debug", "compare", "--help"])
        assert r.exit_code == 0
        assert "--overlay" in r.output

    def test_debug_bundle_has_include_rig(self) -> None:
        r = CliRunner().invoke(main, ["debug", "bundle", "--help"])
        assert r.exit_code == 0
        assert "--include-rig" in r.output

    def test_debug_group_help_no_animator_mention(self) -> None:
        """O grupo debug não deve citar animator3d (render é native bpy)."""
        r = CliRunner().invoke(main, ["debug", "--help"])
        assert r.exit_code == 0
        assert "animator3d" not in r.output.lower()
        assert "native bpy" in r.output.lower()
