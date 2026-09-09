"""Regressão do export PBR do paint — GLB tem de levar metallicRoughness/normal.

O bake do Hunyuan-Paint 2.1 produz albedo **e** branch mr (metallic/roughness);
o export vendido deixava só Base Color no GLB (albedo sem PBR). Estes testes
fixam o contrato: material glTF com ``baseColorTexture`` +
``metallicRoughnessTexture`` (B=metallic, G=roughness) + ``normalTexture``,
albedo em JPEG e data maps em PNG (sem perda), sobrevivendo ao re-export do
``paint3d.utils.mesh_io`` (AUTO).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("bpy", reason="export GLB PBR precisa de bpy (venv do Paint3D)")

from paint3d.hy3dpaint.DifferentiableRenderer.mesh_utils import _save_glb_mesh_bpy


@pytest.fixture(autouse=True)
def _no_pbr_enrich(monkeypatch):
    """Testes de contrato base sem enriquecimento (determinístico, sem GPU).

    O enriquecimento normal+AO (Materialize) tem suite própria em
    ``test_pbr_enrich.py``.
    """
    monkeypatch.setenv("PAINT3D_PBR_ENRICH", "0")


def _glb_json(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        f.read(12)
        clen, _ctype = struct.unpack("<II", f.read(8))
        return json.loads(f.read(clen))


def _glb_image_bytes(path: Path, index: int) -> bytes:
    """Extrai os bytes da imagem ``index`` do chunk BIN do GLB."""
    with open(path, "rb") as f:
        f.read(12)
        jlen, _jtype = struct.unpack("<II", f.read(8))
        js = json.loads(f.read(jlen))
        blen, _btype = struct.unpack("<II", f.read(8))
        bin_data = f.read(blen)
    img = js["images"][index]
    view = js["bufferViews"][img["bufferView"]]
    off = view.get("byteOffset", 0)
    return bin_data[off : off + view["byteLength"]]


def _mesh_arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Quad de 2 triângulos com UVs a cobrir o atlas inteiro."""
    vtx_pos = np.array([[-0.5, -0.5, 0.0], [0.5, -0.5, 0.0], [0.5, 0.5, 0.0], [-0.5, 0.5, 0.0]], dtype=np.float64)
    pos_idx = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    vtx_uv = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float64)
    uv_idx = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    return vtx_pos, pos_idx, vtx_uv, uv_idx


def _textures(size: int = 16) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Albedo cinza claro, metallic 0.25, roughness 0.75, normal plana."""
    albedo = np.full((size, size, 3), 0.8, dtype=np.float64)
    metallic = np.full((size, size, 3), 0.25, dtype=np.float64)
    roughness = np.full((size, size, 3), 0.75, dtype=np.float64)
    normal = np.zeros((size, size, 3), dtype=np.float64)
    normal[..., 2] = 1.0
    return albedo, metallic, roughness, normal


@pytest.fixture(autouse=True)
def _clean_bpy_scene():
    """Limpa a cena e órfãos entre testes (imagens acumulam em bpy.data)."""
    yield
    import bpy

    for obj in list(bpy.context.scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


class TestSaveGlbMeshBpyPbr:
    """Contrato do export vendido ``_save_glb_mesh_bpy`` com PBR baked."""

    def test_full_pbr_has_mr_and_normal(self, tmp_path: Path) -> None:
        out = tmp_path / "pbr.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo, metallic, roughness, normal = _textures()
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness, normal)

        g = _glb_json(out)
        assert len(g["materials"]) == 1
        pbr = g["materials"][0]["pbrMetallicRoughness"]
        assert "baseColorTexture" in pbr
        assert "metallicRoughnessTexture" in pbr
        assert "normalTexture" in g["materials"][0]

    def test_albedo_only_keeps_legacy_shape(self, tmp_path: Path) -> None:
        out = tmp_path / "albedo.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        (albedo, _m, _r, _n) = _textures()
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo)

        g = _glb_json(out)
        pbr = g["materials"][0]["pbrMetallicRoughness"]
        assert "baseColorTexture" in pbr
        assert "metallicRoughnessTexture" not in pbr
        assert "normalTexture" not in g["materials"][0]
        assert len(g["images"]) == 1

    def test_mr_without_normal(self, tmp_path: Path) -> None:
        out = tmp_path / "mr_only.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo, metallic, roughness, _n = _textures()
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness)

        g = _glb_json(out)
        assert "metallicRoughnessTexture" in g["materials"][0]["pbrMetallicRoughness"]
        assert "normalTexture" not in g["materials"][0]

    def test_albedo_jpeg_and_data_maps_png(self, tmp_path: Path) -> None:
        out = tmp_path / "formats.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo, metallic, roughness, normal = _textures()
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness, normal)

        g = _glb_json(out)
        mimes = sorted(img["mimeType"] for img in g["images"])
        assert mimes == ["image/jpeg", "image/png", "image/png"]

    def test_mr_texture_pixels_roundtrip(self, tmp_path: Path) -> None:
        """B=metallic (0.25), G=roughness (0.75) no PNG embutido, sem perda."""
        cv2 = pytest.importorskip("cv2")
        out = tmp_path / "pixels.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo, metallic, roughness, _n = _textures()
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness)

        g = _glb_json(out)
        mr_index = g["materials"][0]["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]
        src = g["textures"][mr_index]["source"]
        png_bytes = _glb_image_bytes(out, src)
        bgr = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = bgr.shape[:2]
        center = bgr[h // 2, w // 2].astype(np.float64) / 255.0  # cv2 → BGR
        assert center[0] == pytest.approx(0.25, abs=2 / 255.0)  # B = metallic
        assert center[1] == pytest.approx(0.75, abs=2 / 255.0)  # G = roughness

    def test_normal_map_is_non_color_png(self, tmp_path: Path) -> None:
        out = tmp_path / "normal.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo, metallic, roughness, normal = _textures()
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness, normal)

        g = _glb_json(out)
        n_tex = g["materials"][0]["normalTexture"]
        # scale default do glTF é 1.0 — exporter omite quando 1.
        assert n_tex.get("scale", 1.0) == pytest.approx(1.0)
        src = g["textures"][n_tex["index"]]["source"]
        assert g["images"][src]["mimeType"] == "image/png"


class TestMeshIoRoundtripPreservesPbr:
    """O re-export do ``paint3d.utils.mesh_io`` (AUTO) não pode descartar PBR."""

    def _paint_glb(self, tmp_path: Path) -> Path:
        out = tmp_path / "painted.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo, metallic, roughness, normal = _textures()
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness, normal)
        return out

    def test_save_glb_roundtrip_keeps_mr_and_normal(self, tmp_path: Path) -> None:
        from paint3d.utils.mesh_io import load_mesh_bpy, save_glb

        painted = self._paint_glb(tmp_path)
        objs = load_mesh_bpy(painted)
        out = tmp_path / "repainted.glb"
        save_glb(objs, out)

        g = _glb_json(out)
        pbr = g["materials"][0]["pbrMetallicRoughness"]
        assert "baseColorTexture" in pbr
        assert "metallicRoughnessTexture" in pbr
        assert "normalTexture" in g["materials"][0]

    def test_save_glb_roundtrip_keeps_formats(self, tmp_path: Path) -> None:
        from paint3d.utils.mesh_io import load_mesh_bpy, save_glb

        painted = self._paint_glb(tmp_path)
        objs = load_mesh_bpy(painted)
        out = tmp_path / "repainted.glb"
        save_glb(objs, out)

        g = _glb_json(out)
        mimes = sorted(img["mimeType"] for img in g["images"])
        assert mimes == ["image/jpeg", "image/png", "image/png"]
