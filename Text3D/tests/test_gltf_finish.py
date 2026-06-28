"""Smoke tests para Text3D/src/text3d/utils/gltf_finish.py (Round 2)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_gltf_finish_module_imports() -> None:
    from text3d.utils import gltf_finish

    assert hasattr(gltf_finish, "gltf_transform_finish")
    assert hasattr(gltf_finish, "FinishResult")


def test_finish_result_has_all_flags() -> None:
    from text3d.utils.gltf_finish import FinishResult

    r = FinishResult(output_path=Path("/tmp/x.glb"))
    assert r.tangents_added is False
    assert r.dedup_applied is False
    assert r.prune_applied is False
    assert r.ktx2_applied is False
    assert r.meshopt_applied is False
    assert r.fully_optimized() is False
    r.dedup_applied = True
    r.prune_applied = True
    r.ktx2_applied = True
    r.meshopt_applied = True
    assert r.fully_optimized() is True


def test_finish_graceful_when_input_missing(tmp_path: Path) -> None:
    from text3d.utils.gltf_finish import gltf_transform_finish

    src = tmp_path / "missing.glb"
    dst = tmp_path / "out.glb"
    r = gltf_transform_finish(src, dst, apply_tangents=False, apply_uastc=False, apply_meshopt=False)
    assert "ausente" in r.skipped_reason.lower()


def test_has_npx_helper_returns_bool() -> None:
    from text3d.utils.gltf_finish import _has_npx

    assert isinstance(_has_npx(), bool)


@pytest.mark.skipif(not __import__("shutil").which("npx"), reason="npx não está disponível neste ambiente")
def test_finish_runs_dedup_prune_when_npx_available(tmp_path: Path) -> None:
    """Smoke: pelo menos os passos sem texturas devem aplicar quando há npx.

    Não construímos um GLB sintético aqui (caro); só validamos que a função
    não rebenta com input inválido leve quando os passos são permitidos.
    """
    from text3d.utils.gltf_finish import gltf_transform_finish

    fake = tmp_path / "fake.glb"
    fake.write_bytes(b"glTF" + b"\x00" * 64)  # GLB inválido — gltf-transform deve recusar
    out = tmp_path / "out.glb"
    r = gltf_transform_finish(
        fake,
        out,
        apply_tangents=False,
        apply_uastc=False,
        apply_meshopt=False,
        apply_dedup=True,
        apply_prune=True,
    )
    # mesmo com input falhando, FinishResult deve ser retornado sem exception
    assert r is not None
