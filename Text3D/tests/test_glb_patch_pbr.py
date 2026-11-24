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

    def test_force_packs_orm_and_repacks_without_bloat(self, tmp_path, monkeypatch):
        import numpy as np

        import text3d.utils.glb_patch_pbr as mod

        p = _base_glb(tmp_path)
        monkeypatch.setattr(mod, "resolve_materialize_bin", lambda: "/fake/materialize-cli")

        def fake_derive(albedo_rgb, **kwargs):
            h, w = albedo_rgb.shape[:2]
            n = np.zeros((h, w, 3), dtype=np.uint8)
            n[..., 0] = 128
            n[..., 1] = 128
            n[..., 2] = 255
            ao = np.full((h, w, 3), 180, dtype=np.uint8)
            return n, ao

        monkeypatch.setattr(mod, "derive_uv_masked_pbr", fake_derive)
        r1 = patch_glb_pbr(p, force=True, logger=None)
        assert not r1.skipped
        size1 = p.stat().st_size
        g1, _ = _load_glb(p)
        slots1 = material_slots(g1)
        assert slots1["normal"] is not None
        assert slots1["ao"] is not None
        # ORM partilhado: occlusion aponta para metallicRoughness (não para baseColor!)
        assert slots1["ao"] == slots1["mr"]
        assert slots1["mr"] != slots1["base"]

        # Segunda passagem --force substitui in-place sem duplicar blobs
        r2 = patch_glb_pbr(p, force=True, logger=None)
        assert not r2.skipped
        assert p.stat().st_size == size1

    def test_orphan_image_left_by_the_upsert_is_dropped(self, tmp_path, monkeypatch):
        """A ORM passa a servir a occlusion: a textura ``ao`` antiga fica órfã.

        O Bevy descodifica todas as imagens do GLB, usadas ou não — uma órfã com
        ``byteLength`` 0 derruba o asset inteiro (``UnexpectedEnd``).
        """
        import numpy as np

        import text3d.utils.glb_patch_pbr as mod

        names = ("albedo", "n", "mr", "ao")
        # geometria DEPOIS das imagens: a remoção renumera as views dela
        chunks = [_png_bytes(rgb) for rgb in ((200, 100, 80), (128, 128, 255), (160, 160, 160), (180, 180, 180))]
        chunks += [struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0), struct.pack("<3H", 0, 1, 2)]
        views, data = [], bytearray()
        for blob in chunks:
            data += b"\0" * ((-len(data)) % 4)
            views.append({"buffer": 0, "byteOffset": len(data), "byteLength": len(blob)})
            data += blob
        gltf = {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": len(data)}],
            "accessors": [
                {"bufferView": 4, "componentType": 5126, "count": 3, "type": "VEC3"},
                {"bufferView": 5, "componentType": 5123, "count": 3, "type": "SCALAR"},
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
            "bufferViews": views,
            "images": [{"bufferView": i, "mimeType": "image/png", "name": n} for i, n in enumerate(names)],
            "samplers": [{}],
            "textures": [{"source": i, "sampler": 0} for i in range(len(names))],
            "materials": [
                {
                    "pbrMetallicRoughness": {
                        "baseColorTexture": {"index": 0},
                        "metallicRoughnessTexture": {"index": 2},
                    },
                    "normalTexture": {"index": 1},
                    "occlusionTexture": {"index": 3},
                }
            ],
        }
        p = tmp_path / "split.glb"
        _write_glb(p, gltf, bytes(data))
        monkeypatch.setattr(mod, "resolve_materialize_bin", lambda: "/fake/materialize-cli")

        def fake_derive(albedo_rgb, **kwargs):
            h, w = albedo_rgb.shape[:2]
            return np.full((h, w, 3), 128, dtype=np.uint8), np.full((h, w, 3), 180, dtype=np.uint8)

        monkeypatch.setattr(mod, "derive_uv_masked_pbr", fake_derive)
        assert not patch_glb_pbr(p, force=True, logger=None).skipped

        g, bin_data = _load_glb(p)
        views = g["bufferViews"]

        def image_of(tex_index):
            tex = g["textures"][tex_index]
            source = tex.get("extensions", {}).get("KHR_texture_basisu", {}).get("source", tex.get("source"))
            view = views[g["images"][source]["bufferView"]]
            start = view.get("byteOffset", 0)
            return bytes(bin_data[start : start + view["byteLength"]])

        assert [im["name"] for im in g["images"] if views[im["bufferView"]]["byteLength"] == 0] == []
        slots = material_slots(g)
        assert image_of(slots["base"]) == chunks[0]
        for slot in ("mr", "normal", "ao"):
            assert image_of(slots[slot]).startswith(b"\xabKTX")
        # nem textura nem imagem órfã sobrevivem: albedo, normal e ORM
        assert [im["name"] for im in g["images"]] == ["albedo", "normal_m0", "orm_m0"]
        assert len(g["textures"]) == 3
        # a geometria continua a ler os mesmos bytes, agora uma view abaixo
        assert [a["bufferView"] for a in g["accessors"]] == [3, 4]
        for accessor, blob in zip(g["accessors"], chunks[4:], strict=True):
            view = views[accessor["bufferView"]]
            start = view.get("byteOffset", 0)
            assert bytes(bin_data[start : start + view["byteLength"]]) == blob

    def test_resolve_skips_legacy_materialize_without_only(self, tmp_path, monkeypatch):
        import aigamekit_shared.pbr_uv as pbr_uv

        legacy = tmp_path / "materialize"
        legacy.write_text("#!/bin/sh\necho 'Usage: materialize <input>'\n", encoding="utf-8")
        legacy.chmod(0o755)
        modern = tmp_path / "materialize-cli"
        modern.write_text("#!/bin/sh\necho 'Usage: materialize-cli --only --no-seamless'\n", encoding="utf-8")
        modern.chmod(0o755)

        pbr_uv._supports_modern_flags.cache_clear()
        monkeypatch.setenv("MATERIALIZE_BIN", str(legacy))
        monkeypatch.setattr(
            pbr_uv.shutil,
            "which",
            lambda name: str(modern) if name == "materialize-cli" else str(legacy),
        )
        resolved = pbr_uv.resolve_materialize_bin()
        assert resolved is not None
        assert Path(resolved).name != "materialize" or "materialize-cli" in resolved
        pbr_uv._supports_modern_flags.cache_clear()
