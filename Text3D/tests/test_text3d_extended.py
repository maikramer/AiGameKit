"""Testes extra Text3D: defaults, generator helpers, flags de ambiente no CLI."""

from __future__ import annotations

import math
import runpy
import types
from pathlib import Path

import pytest


def _load_defaults():
    """Carrega ``defaults.py`` via ``runpy`` (evita importar o pacote ``text3d``)."""
    p = Path(__file__).resolve().parents[1] / "src" / "text3d" / "defaults.py"
    ns = runpy.run_path(str(p))
    mod = types.ModuleType("text3d_defaults_ext")
    for k, v in ns.items():
        setattr(mod, k, v)
    return mod


d = _load_defaults()


def test_export_rotation_default(monkeypatch: pytest.MonkeyPatch) -> None:
    d.set_export_rotation_x_rad_override(None)
    monkeypatch.delenv("TEXT3D_EXPORT_ROTATION_X_RAD", raising=False)
    monkeypatch.delenv("TEXT3D_EXPORT_ROTATION_X_DEG", raising=False)
    assert d.get_export_rotation_x_rad() == pytest.approx(math.pi / 2)


def test_export_rotation_rad_env(monkeypatch: pytest.MonkeyPatch) -> None:
    d.set_export_rotation_x_rad_override(None)
    monkeypatch.setenv("TEXT3D_EXPORT_ROTATION_X_RAD", str(math.pi / 4))
    assert d.get_export_rotation_x_rad() == pytest.approx(math.pi / 4)


def test_export_rotation_deg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    d.set_export_rotation_x_rad_override(None)
    monkeypatch.delenv("TEXT3D_EXPORT_ROTATION_X_RAD", raising=False)
    monkeypatch.setenv("TEXT3D_EXPORT_ROTATION_X_DEG", "90")
    assert d.get_export_rotation_x_rad() == pytest.approx(math.pi / 2)


def test_export_rotation_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT3D_EXPORT_ROTATION_X_RAD", "0.5")
    d.set_export_rotation_x_rad_override(1.25)
    assert d.get_export_rotation_x_rad() == pytest.approx(1.25)
    d.set_export_rotation_x_rad_override(None)


def test_preset_fast_values() -> None:
    f = d.PRESET_HUNYUAN["fast"]
    assert f["steps"] < d.DEFAULT_HY_STEPS
    assert f["octree"] < d.DEFAULT_OCTREE_RESOLUTION


def test_preset_hq_heavier_than_balanced() -> None:
    hq = d.PRESET_HUNYUAN["hq"]
    bal = d.PRESET_HUNYUAN["balanced"]
    assert hq["steps"] >= bal["steps"]
    assert hq["octree"] >= bal["octree"]


def test_default_mc_level() -> None:
    assert d.DEFAULT_MC_LEVEL == 0.0


def test_default_t2d_dims() -> None:
    assert d.DEFAULT_T2D_WIDTH == d.DEFAULT_T2D_HEIGHT == 768


def test_default_subfolder_string() -> None:
    assert "hunyuan" in d.DEFAULT_SUBFOLDER.lower()


def test_hq_constants_match_preset() -> None:
    hq = d.PRESET_HUNYUAN["hq"]
    assert hq["steps"] == d.DEFAULT_HY_STEPS
    assert hq["octree"] == d.DEFAULT_OCTREE_RESOLUTION
    assert hq["chunks"] == d.DEFAULT_NUM_CHUNKS


def test_default_hy_steps_positive() -> None:
    assert d.DEFAULT_HY_STEPS > 0
    assert 1.0 <= d.DEFAULT_HY_GUIDANCE <= 20.0


def test_default_t2d_guidance() -> None:
    assert d.DEFAULT_T2D_GUIDANCE == 1.0


def test_cpu_offload_defaults_are_bool() -> None:
    assert isinstance(d.DEFAULT_T2D_CPU_OFFLOAD, bool)


def test_mesh_smooth_default() -> None:
    assert d.DEFAULT_MESH_SMOOTH == 0


def test_default_octree_and_chunks() -> None:
    assert d.DEFAULT_OCTREE_RESOLUTION >= 64
    assert d.DEFAULT_NUM_CHUNKS >= 1024


def test_balanced_preset_equals_constants() -> None:
    b = d.PRESET_HUNYUAN["balanced"]
    assert b["steps"] == d.LOW_VRAM_STEPS
    assert b["octree"] == d.LOW_VRAM_OCTREE
    assert b["chunks"] == d.LOW_VRAM_NUM_CHUNKS


def test_fast_preset_smaller_than_balanced_steps() -> None:
    assert d.PRESET_HUNYUAN["fast"]["steps"] < d.PRESET_HUNYUAN["balanced"]["steps"]


def test_env_allow_shared_gpu_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Espelha a lógica de ``text3d.cli._env_allow_shared_gpu`` sem importar o CLI."""

    def allow() -> bool:
        import os

        return os.environ.get("TEXT3D_ALLOW_SHARED_GPU", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    monkeypatch.setenv("TEXT3D_ALLOW_SHARED_GPU", "on")
    assert allow() is True
    monkeypatch.setenv("TEXT3D_ALLOW_SHARED_GPU", "0")
    assert allow() is False


def test_gpu_kill_effective_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Espelha ``_gpu_kill_others_effective`` (stdlib apenas)."""

    def effective(cli_wants: bool) -> bool:
        import os

        v = os.environ.get("TEXT3D_GPU_KILL_OTHERS", "").strip().lower()
        if v in ("0", "false", "no", "off"):
            return False
        if v in ("1", "true", "yes", "on"):
            return True
        return cli_wants

    monkeypatch.setenv("TEXT3D_GPU_KILL_OTHERS", "0")
    assert effective(True) is False
    monkeypatch.setenv("TEXT3D_GPU_KILL_OTHERS", "1")
    assert effective(False) is True
    monkeypatch.delenv("TEXT3D_GPU_KILL_OTHERS", raising=False)
    assert effective(True) is True
    assert effective(False) is False
