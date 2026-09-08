"""Testes do patch PBR pós-LOD (cirurgia GLB) — CPU, sem materialize/GPU."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from PIL import Image

from text3d.utils.glb_patch_pbr import (
    PatchPbrResult,
    _inject_maps,
    _load_glb,
    _save_glb,
    extract_albedo_png,
    glb_is_pbr_complete,
    material_slots,
    patch_glb_pbr,
)

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def _write_glb(path: Path, gltf: dict, bin_data: bytes) -> None:
    js = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    bd = bin_data + b"\x00" * ((4 - len(bin_data) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bd)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(js), JSON_CHUNK) + js
    out += struct.pack("<II", len(bd), BIN_CHUNK) + bd
    path.write_bytes(bytes(out))


def _png_bytes(rgb: tuple[int, int, int] = (128, 128, 128), size: int = 8) -> bytes:
    buf = Path("/tmp")  # noqa: F841
    import io

    img = Image.new("RGB", (size, size), rgb)
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def _base_glb(tmp_path: Path, *, with_normal_ao: bool = False) -> Path:
    """GLB sintético com albedo PNG + material base/mr (+ normal/ao se pedido)."""
    albedo = _png_bytes((200, 100, 80))
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(albedo)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(albedo)}],
        "images": [{"bufferView": 0, "mimeType": "image/png", "name": "albedo"}],
        "samplers": [{}],
        "textures": [{"source": 0, "sampler": 0}],
        "materials": [
            {
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                    "metallicRoughnessTexture": {"index": 0},
                },
            }
        ],
    }
    if with_normal_ao:
        gltf["images"].append({"bufferView": 0, "mimeType": "image/png", "name": "n"})
        gltf["images"].append({"bufferView": 0, "mimeType": "image/png", "name": "ao"})
        gltf["textures"].append({"source": 1, "sampler": 0})
        gltf["textures"].append({"source": 2, "sampler": 0})
        gltf["materials"][0]["normalTexture"] = {"index": 1}
        gltf["materials"][0]["occlusionTexture"] = {"index": 2}
    p = tmp_path / "synthetic.glb"
    _write_glb(p, gltf, albedo)
    return p


class TestSlots:
    def test_incomplete_detected(self, tmp_path):
        p = _base_glb(tmp_path)
        gltf, _ = _load_glb(p)
        assert not glb_is_pbr_complete(gltf)
        slots = material_slots(gltf)
        assert slots == {"base": 0, "mr": 0, "normal": None, "ao": None}

    def test_complete_detected(self, tmp_path):
        p = _base_glb(tmp_path, with_normal_ao=True)
        gltf, _ = _load_glb(p)
        assert glb_is_pbr_complete(gltf)
        assert material_slots(gltf)["normal"] == 1
        assert material_slots(gltf)["ao"] == 2


class TestInject:
    def test_inject_adds_slots_and_roundtrips(self, tmp_path):
        src = _base_glb(tmp_path)
        gltf, bin_data = _load_glb(src)
        n = tmp_path / "normal.png"
        a = tmp_path / "ao.png"
        n.write_bytes(_png_bytes((128, 128, 255)))
        a.write_bytes(_png_bytes((200, 200, 200)))
        _inject_maps(gltf, bin_data, n, a, tmp_path)
        out = tmp_path / "patched.glb"
        _save_glb(out, gltf, bin_data)

        gltf2, _ = _load_glb(out)
        assert glb_is_pbr_complete(gltf2)
        assert len(gltf2["images"]) == 3
        assert len(gltf2["textures"]) == 3
        # Os mapas novos entram como KTX2 (regra texture_format do pipeline).
        assert gltf2["images"][1]["mimeType"] == "image/ktx2"
        assert gltf2["images"][2]["mimeType"] == "image/ktx2"
        assert "KHR_texture_basisu" in gltf2["extensionsUsed"]
        # bufferViews novos com offsets alinhados e byteLength igual ao PNG.
        for bv in gltf2["bufferViews"][1:]:
            assert bv["byteOffset"] % 4 == 0
            assert bv["byteLength"] > 0

    def test_inject_is_deterministic(self, tmp_path):
        src = _base_glb(tmp_path)
        n = tmp_path / "n.png"
        a = tmp_path / "ao.png"
        n.write_bytes(_png_bytes())
        a.write_bytes(_png_bytes())
        outs = []
        for _ in range(2):
            gltf, bin_data = _load_glb(src)
            _inject_maps(gltf, bin_data, n, a, tmp_path)
            out = tmp_path / f"o{len(outs)}.glb"
            _save_glb(out, gltf, bin_data)
            outs.append(out.read_bytes())
        assert outs[0] == outs[1]


class TestExtractAlbedo:
    def test_extract_png_direct(self, tmp_path):
        p = _base_glb(tmp_path)
        out = extract_albedo_png(p, tmp_path)
        assert out.is_file()
        with Image.open(out) as im:
            assert im.size == (8, 8)

    def test_extract_resolves_basisu_source_on_texture(self, tmp_path):
        # GLBs gltf-transform podem pôr o source em extensions.KHR_texture_basisu.
        albedo = _png_bytes((50, 150, 250))
        gltf = {
            "asset": {"version": "2.0"},
            "extensionsUsed": ["KHR_texture_basisu"],
            "buffers": [{"byteLength": len(albedo)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(albedo)}],
            "images": [{"bufferView": 0, "mimeType": "image/png"}],
            "samplers": [{}],
            "textures": [{"sampler": 0, "extensions": {"KHR_texture_basisu": {"source": 0}}}],
            "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        }
        p = tmp_path / "basisu.glb"
        _write_glb(p, gltf, albedo)
        out = extract_albedo_png(p, tmp_path)
        assert out.is_file()


class TestPatchFlow:
    def test_skip_when_complete(self, tmp_path):
        p = _base_glb(tmp_path, with_normal_ao=True)
        result = patch_glb_pbr(p, logger=None)
        assert isinstance(result, PatchPbrResult)
        assert result.skipped
        assert "normalTexture" in result.reason

    def test_skip_when_no_base_color(self, tmp_path):
        gltf, _ = _load_glb(_base_glb(tmp_path))
        gltf["materials"][0]["pbrMetallicRoughness"] = {}
        p = tmp_path / "nopbr.glb"
        _write_glb(p, gltf, b"")
        result = patch_glb_pbr(p, logger=None)
        assert result.skipped
        assert "baseColorTexture" in result.reason

    def test_missing_bin_raises_clean(self, tmp_path, monkeypatch):
        import text3d.utils.glb_patch_pbr as mod

        p = _base_glb(tmp_path)
        monkeypatch.setattr(mod, "resolve_materialize_bin", lambda: None)
        with pytest.raises(RuntimeError, match="materialize não encontrado"):
            patch_glb_pbr(p, logger=None)
