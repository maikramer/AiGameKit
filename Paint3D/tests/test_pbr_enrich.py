"""Enriquecimento PBR do paint — normal + AO via Materialize, slot occlusion.

O modelo Hunyuan-Paint 2.1 só gera albedo + mr; este enriquecimento deriva
``normal`` e ``ao`` do albedo baked com o crate Materialize (wgpu) e embute
tudo no GLB no contrato glTF: ORM **R=AO, G=roughness, B=metallic** partilhada
por ``metallicRoughnessTexture`` e ``occlusionTexture`` + ``normalTexture``.
Testes aqui são CPU-first: o Materialize real só corre quando há binário
(marcked explicitamente); os de wiring usam mapas fake.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("bpy", reason="export GLB PBR precisa de bpy (venv do Paint3D)")

import paint3d.pbr_enrich as pbr_enrich
from paint3d.hy3dpaint.DifferentiableRenderer.mesh_utils import _save_glb_mesh_bpy


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


def _fake_enriched(albedo=None, preset=None, logger=None):
    """Substituto determinístico do Materialize: normal plana + AO 0.5."""
    size = 16 if albedo is None else albedo.shape[0]
    normal = np.zeros((size, size, 3), dtype=np.uint8)
    normal[..., 0] = 128
    normal[..., 1] = 128
    normal[..., 2] = 255
    ao = np.full((size, size, 3), 128, dtype=np.uint8)
    return normal, ao


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


class TestPbrEnrichModule:
    def test_enabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("PAINT3D_PBR_ENRICH", raising=False)
        assert pbr_enrich.pbr_enrich_enabled() is True

    def test_env_zero_disables(self, monkeypatch) -> None:
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "0")
        assert pbr_enrich.pbr_enrich_enabled() is False

    def test_missing_binary_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "1")
        monkeypatch.setattr(pbr_enrich, "resolve_materialize_bin", lambda: None)
        albedo = np.full((16, 16, 3), 200, dtype=np.uint8)
        assert pbr_enrich.enrich_maps_from_albedo(albedo) is None

    def test_invokes_materialize_with_expected_argv(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "1")
        monkeypatch.setenv("PAINT3D_PBR_PRESET", "stone")
        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            out_dir = Path(cmd[cmd.index("-o") + 1])
            stem = Path(cmd[1]).stem
            normal = np.full((16, 16, 3), 128, dtype=np.uint8)
            ao = np.full((16, 16, 3), 64, dtype=np.uint8)
            from PIL import Image as PILImage

            PILImage.fromarray(normal, mode="RGB").save(out_dir / f"{stem}_normal.png")
            PILImage.fromarray(ao, mode="RGB").save(out_dir / f"{stem}_ao.png")

            class _Proc:
                returncode = 0
                stderr = b""

            return _Proc()

        monkeypatch.setattr(pbr_enrich, "resolve_materialize_bin", lambda: "/fake/materialize")
        monkeypatch.setattr(pbr_enrich.subprocess, "run", fake_run)
        albedo = np.full((16, 16, 3), 200, dtype=np.uint8)
        result = pbr_enrich.enrich_maps_from_albedo(albedo)
        assert result is not None
        normal, ao = result
        assert normal.shape == (16, 16, 3) and ao.shape == (16, 16, 3)
        assert ao[0, 0, 0] == 64
        cmd = captured["cmd"]
        assert cmd[0] == "/fake/materialize"
        assert "--only" in cmd and cmd[cmd.index("--only") + 1] == "normal,ao"
        assert "--no-seamless" in cmd
        assert "--normal-format" in cmd and cmd[cmd.index("--normal-format") + 1] == "opengl"
        assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "stone"

    def test_real_materialize_when_available(self, monkeypatch) -> None:
        """Integração com o binário real — skip sem MATERIALIZE_BIN/GPU."""
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "1")
        monkeypatch.setenv("PAINT3D_PBR_PRESET", "default")
        if pbr_enrich.resolve_materialize_bin() is None:
            pytest.skip("materialize não disponível")
        albedo = np.full((32, 32, 3), 180, dtype=np.uint8)
        result = pbr_enrich.enrich_maps_from_albedo(albedo)
        if result is None:  # sem adapter wgpu (CI/sem Vulkan)
            pytest.skip("materialize indisponível (rc!=0 / sem adapter GPU)")
        normal, ao = result
        assert normal.shape == (32, 32, 3) and ao.shape == (32, 32, 3)


class TestEnrichedGlbExport:
    def test_enriched_glb_has_normal_orm_and_occlusion(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "1")
        monkeypatch.setattr(pbr_enrich, "enrich_maps_from_albedo", _fake_enriched)
        out = tmp_path / "enriched.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo = np.full((16, 16, 3), 0.8, dtype=np.float64)
        metallic = np.full((16, 16, 3), 0.25, dtype=np.float64)
        roughness = np.full((16, 16, 3), 0.75, dtype=np.float64)
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness)

        g = _glb_json(out)
        mat = g["materials"][0]
        pbr = mat["pbrMetallicRoughness"]
        assert "baseColorTexture" in pbr
        assert "metallicRoughnessTexture" in pbr
        assert "normalTexture" in mat
        assert "occlusionTexture" in mat
        # Convenção ORM: occlusion aponta para a mesma textura do MR.
        assert mat["occlusionTexture"]["index"] == pbr["metallicRoughnessTexture"]["index"]
        assert mat["occlusionTexture"]["strength"] == pytest.approx(1.0)

    def test_orm_pixels_pack_ao_roughness_metallic(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "1")
        monkeypatch.setattr(pbr_enrich, "enrich_maps_from_albedo", _fake_enriched)
        cv2 = pytest.importorskip("cv2")
        out = tmp_path / "orm.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo = np.full((16, 16, 3), 0.8, dtype=np.float64)
        metallic = np.full((16, 16, 3), 0.25, dtype=np.float64)
        roughness = np.full((16, 16, 3), 0.75, dtype=np.float64)
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness)

        g = _glb_json(out)
        orm_tex = g["materials"][0]["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]
        src = g["textures"][orm_tex]["source"]
        bgr = cv2.imdecode(np.frombuffer(_glb_image_bytes(out, src), dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = bgr.shape[:2]
        c = bgr[h // 2, w // 2].astype(np.float64) / 255.0  # cv2 → BGR
        assert c[0] == pytest.approx(0.25, abs=3 / 255.0)  # B = metallic
        assert c[1] == pytest.approx(0.75, abs=3 / 255.0)  # G = roughness
        assert c[2] == pytest.approx(0.5, abs=3 / 255.0)  # R = AO

    def test_enrich_off_keeps_plain_mr(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "0")
        out = tmp_path / "plain.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo = np.full((16, 16, 3), 0.8, dtype=np.float64)
        metallic = np.full((16, 16, 3), 0.25, dtype=np.float64)
        roughness = np.full((16, 16, 3), 0.75, dtype=np.float64)
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness)

        mat = _glb_json(out)["materials"][0]
        assert "metallicRoughnessTexture" in mat["pbrMetallicRoughness"]
        assert "normalTexture" not in mat
        assert "occlusionTexture" not in mat

    def test_roundtrip_preserves_occlusion(self, tmp_path: Path, monkeypatch) -> None:
        """bpy import/export não pode perder o slot patchado (ou o mesh_io repõe)."""
        monkeypatch.setenv("PAINT3D_PBR_ENRICH", "1")
        monkeypatch.setattr(pbr_enrich, "enrich_maps_from_albedo", _fake_enriched)
        from paint3d.utils.mesh_io import load_mesh_bpy, save_glb

        out = tmp_path / "enriched.glb"
        vtx_pos, pos_idx, vtx_uv, uv_idx = _mesh_arrays()
        albedo = np.full((16, 16, 3), 0.8, dtype=np.float64)
        metallic = np.full((16, 16, 3), 0.25, dtype=np.float64)
        roughness = np.full((16, 16, 3), 0.75, dtype=np.float64)
        _save_glb_mesh_bpy(str(out), vtx_pos, pos_idx, vtx_uv, uv_idx, albedo, metallic, roughness)

        objs = load_mesh_bpy(out)
        out2 = tmp_path / "reexport.glb"
        save_glb(objs, out2)
        mat = _glb_json(out2)["materials"][0]
        assert "normalTexture" in mat
        assert "occlusionTexture" in mat
        assert mat["occlusionTexture"]["index"] == mat["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]


class TestEnsureOcclusionHeuristic:
    @staticmethod
    def _write_glb(path: Path, materials: list[dict[str, Any]]) -> Path:
        js = {"asset": {"version": "2.0"}, "materials": materials}
        raw = json.dumps(js).encode()
        raw += b" " * (-len(raw) % 4)
        path.write_bytes(
            struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(raw) + 8)
            + struct.pack("<II", len(raw), 0x4E4F534A)
            + raw
            + struct.pack("<II", 0, 0x004E4942)
        )
        return path

    def test_patches_mr_plus_normal_without_occlusion(self, tmp_path: Path) -> None:
        from aigamekit_shared.gltf_occlusion import ensure_occlusion_texture

        glb = self._write_glb(
            tmp_path / "a.glb",
            [{"pbrMetallicRoughness": {"metallicRoughnessTexture": {"index": 0}}, "normalTexture": {"index": 1}}],
        )
        assert ensure_occlusion_texture(glb) is True
        mat = _glb_json(glb)["materials"][0]
        assert mat["occlusionTexture"]["index"] == 0
        assert mat["occlusionTexture"]["strength"] == pytest.approx(1.0)

    def test_skips_mr_without_normal(self, tmp_path: Path) -> None:
        from aigamekit_shared.gltf_occlusion import ensure_occlusion_texture

        glb = self._write_glb(
            tmp_path / "b.glb",
            [{"pbrMetallicRoughness": {"metallicRoughnessTexture": {"index": 0}}}],
        )
        assert ensure_occlusion_texture(glb) is False
        assert "occlusionTexture" not in _glb_json(glb)["materials"][0]

    def test_idempotent_when_slot_present(self, tmp_path: Path) -> None:
        from aigamekit_shared.gltf_occlusion import ensure_occlusion_texture

        existing = {"index": 2, "strength": 0.4}
        glb = self._write_glb(
            tmp_path / "c.glb",
            [
                {
                    "pbrMetallicRoughness": {"metallicRoughnessTexture": {"index": 0}},
                    "normalTexture": {"index": 1},
                    "occlusionTexture": dict(existing),
                }
            ],
        )
        assert ensure_occlusion_texture(glb) is False
        mat = _glb_json(glb)["materials"][0]
        assert mat["occlusionTexture"] == existing
