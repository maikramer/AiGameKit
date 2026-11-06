"""Contratos do pipeline (regressões da revisão 2026-10-30)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gameassets.helpers import effective_face_ratio
from gameassets.omni_ctrl import shape_omni_stale
from gameassets.profile import GameProfile


def _profile() -> GameProfile:
    return GameProfile(
        title="t",
        genre="g",
        tone="t",
        style_preset="s",
        output_dir=".",
        generation="medium",
    )


def test_highest_face_ratio_nao_colapsa_para_zero() -> None:
    """``highest`` usa simplify_face_ratio=0.0 ("não simplificar") → ratio 1.0.

    Antes devolvia 0 → get_target_faces=0 → master caía no fallback 8000.
    """
    row = SimpleNamespace(generation="highest")
    assert effective_face_ratio(_profile(), row) == 1.0


def test_bbox_preset_aniso_nao_invalida_shape(tmp_path: Path) -> None:
    """O sidecar grava bbox_preset + bbox resolvido; o normalizador do stale
    tem de o comparar com o mesmo merge do expected (bug da chapel)."""
    from gameassets.omni_ctrl import OmniControls

    try:
        from text3d.omni_presets import size_m_to_bbox
    except ImportError:  # job do GameAssets no CI não instala Text3D

        def size_m_to_bbox(size_m: list[float]) -> list[float]:
            """Regra do Text3D: eixo maior = OMNI_BBOX_AXIS_MAX (1.0)."""
            m = max(size_m)
            return [1.0 * (v / m) for v in size_m]

    size = [6.0, 7.0, 4.5]
    omni = OmniControls(
        control_type="bbox",
        bbox_preset="chapel",
        size_m=tuple(size),
    )
    shape = tmp_path / "chapel_shape.glb"
    shape.write_bytes(b"glTF")  # só is_file() é verificado
    sidecar = {
        "control_type": "bbox",
        "bbox": size_m_to_bbox(size),  # resolvido, como o generate grava
        "bbox_preset": "chapel",
        "size_m": size,
        "pose_preset": None,
        "pose_file": None,
        "point_cloud": None,
        "voxel_mesh": None,
        "point_from": None,
        "bounds_mode": None,
        "mc_level": None,
        "seed": None,
        "octree_resolution": 576,  # autotune: não deve invalidar
        "bbox_axis_max": 1.0,
    }
    Path(str(shape) + ".omni.json").write_text(json.dumps(sidecar), encoding="utf-8")
    assert not shape_omni_stale(
        shape,
        omni,
        category="building",
        bounds_mode="auto",
        mc_level="auto",
        seed=None,
        octree_resolution=None,
    )


def test_master_force_e_master_force_contextvar() -> None:
    """Contratos de --force: o master aceita force e o contextvar existe."""
    from gameassets.pipeline import run_master_pipeline
    from gameassets.vramd_coord import MASTER_FORCE

    assert "force" in run_master_pipeline.__code__.co_varnames
    assert MASTER_FORCE.get() is False


@pytest.mark.parametrize(
    "func_name",
    ["_run_static_lod_stages"],
)
def test_static_lod_aceita_lod_profile(func_name: str) -> None:
    import gameassets.pipeline as pipeline

    fn = getattr(pipeline, func_name)
    assert "lod_profile" in fn.__code__.co_varnames
