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
    assert r.meshopt_backend == ""
    assert r.fully_optimized() is False
    r.dedup_applied = True
    r.prune_applied = True
    r.ktx2_applied = True
    r.meshopt_applied = True
    r.meshopt_backend = "bpy"
    assert r.fully_optimized() is True


def test_meshopt_bpy_helpers_importable() -> None:
    from text3d.utils.gltf_finish import _apply_meshopt_bpy, _glb_has_meshopt

    assert callable(_apply_meshopt_bpy)
    assert callable(_glb_has_meshopt)


def test_finish_defaults_enable_ktx2_and_meshopt() -> None:
    import inspect

    from text3d.utils.gltf_finish import gltf_transform_finish

    params = inspect.signature(gltf_transform_finish).parameters
    assert params["apply_uastc"].default is True
    assert params["apply_meshopt"].default is True


def test_finish_uses_hybrid_ktx2_by_slot() -> None:
    """Albedo → ETC1S; normais → UASTC (UASTC-all ~2x disco no albedo)."""
    import inspect

    from text3d.utils import gltf_finish

    src = inspect.getsource(gltf_finish.gltf_transform_finish)
    assert "_KTX2_UASTC_SLOTS" in src
    assert "_KTX2_ETC1S_SLOTS" in src
    assert '"etc1s"' in src or "'etc1s'" in src
    assert gltf_finish._KTX2_UASTC_SLOTS == "*normal*"
    assert "baseColorTexture" in gltf_finish._KTX2_ETC1S_SLOTS


def test_finish_bpy_exports_jpeg_not_auto() -> None:
    """AUTO+PNG no downscale fazia lod1 (bytes) > lod0."""
    import inspect

    from text3d.utils import gltf_finish

    src = inspect.getsource(gltf_finish._recalc_tangents_inplace)
    assert '"export_image_format": "JPEG"' in src
    assert '"export_image_format": "AUTO"' not in src


def test_prune_keeps_vertex_attributes() -> None:
    """Regression: prune sem --keep-attributes apaga TANGENT (gltf-transform 4.x)."""
    import inspect

    from text3d.utils import gltf_finish

    src = inspect.getsource(gltf_finish.gltf_transform_finish)
    assert "--keep-attributes" in src
    assert "true" in src
    assert "ktxdecompress" in src


def test_meshopt_skips_gltf_transform_for_skinned() -> None:
    """Regression: meshopt gltf-transform + quantize desloca origem em skinned."""
    import inspect

    from text3d.utils import gltf_finish

    src = inspect.getsource(gltf_finish.gltf_transform_finish)
    assert "_glb_has_skins" in src
    assert "omitido (GLB skinned)" in src
    assert callable(gltf_finish._glb_has_skins)


def test_glb_vertex_attrs_helper() -> None:
    from text3d.utils.gltf_finish import _glb_vertex_attrs

    assert _glb_vertex_attrs(Path("/nonexistent/nope.glb")) == set()


def test_finish_graceful_when_input_missing(tmp_path: Path) -> None:
    from text3d.utils.gltf_finish import gltf_transform_finish

    src = tmp_path / "missing.glb"
    dst = tmp_path / "out.glb"
    r = gltf_transform_finish(src, dst, apply_tangents=False, apply_uastc=False, apply_meshopt=False)
    assert "ausente" in r.skipped_reason.lower()


def test_has_npx_helper_returns_bool() -> None:
    from text3d.utils.gltf_finish import _has_ktx, _has_npx

    assert isinstance(_has_npx(), bool)
    assert isinstance(_has_ktx(), bool)


def _synthetic_glb(path: Path, *, tri_count: int) -> Path:
    """GLB mínimo válido do ponto de vista do chunk JSON (sem binário real)."""
    import json
    import struct

    doc = {
        "asset": {"version": "2.0"},
        "accessors": [
            {"componentType": 5125, "count": tri_count * 3, "type": "SCALAR"},
            {"componentType": 5126, "count": tri_count * 3, "type": "VEC3"},
        ],
        "meshes": [{"primitives": [{"indices": 0, "attributes": {"POSITION": 1}}]}],
    }
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(raw))
    path.write_bytes(header + struct.pack("<II", len(raw), 0x4E4F534A) + raw)
    return path


def test_glb_face_count_reads_json_chunk(tmp_path: Path) -> None:
    from text3d.utils.gltf_finish import glb_face_count

    glb = _synthetic_glb(tmp_path / "m.glb", tri_count=1200)
    assert glb_face_count(glb) == 1200
    assert glb_face_count(tmp_path / "nope.glb") == -1


def test_meshopt_simplify_refuses_when_already_below_target(tmp_path: Path) -> None:
    from text3d.utils.gltf_finish import meshopt_simplify_glb

    glb = _synthetic_glb(tmp_path / "small.glb", tri_count=100)
    ok, faces, err = meshopt_simplify_glb(glb, tmp_path / "out.glb", target_faces=500)
    assert ok is False
    assert faces == 100
    assert "abaixo do alvo" in err
    assert not (tmp_path / "out.glb").exists()


def test_meshopt_simplify_reports_unreadable_input(tmp_path: Path) -> None:
    from text3d.utils.gltf_finish import meshopt_simplify_glb

    bogus = tmp_path / "bogus.glb"
    bogus.write_bytes(b"not a glb")
    ok, faces, err = meshopt_simplify_glb(bogus, tmp_path / "out.glb", target_faces=10)
    assert ok is False
    assert faces == -1
    assert "contar faces" in err


def test_meshopt_simplify_locks_uv_seams_by_default() -> None:
    """Regression: o valor do meshoptimizer aqui é travar costuras de atributos.

    Passar ``--lock-border`` ou pré-weld com tolerância fundiria as costuras e
    o simplificador voltaria a colapsar através das ilhas UV (textura rasgada).
    """
    import inspect

    from text3d.utils import gltf_finish

    src = inspect.getsource(gltf_finish.meshopt_simplify_glb)
    assert "weld" in src
    assert "--lock-border" not in src


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
