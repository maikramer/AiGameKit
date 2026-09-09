#!/usr/bin/env python3
"""Unit tests for scripts/enrich_materials.py — run with plain unittest.

python3 -m unittest discover -s scripts -p 'test_enrich_materials.py' -v
"""

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import enrich_materials as em


def make_glb(doc: dict, binary: bytes = b"") -> bytes:
    json_blob = json.dumps(doc).encode()
    json_blob += b" " * (-len(json_blob) % 4)
    bin_padded = binary + b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(json_blob) + (8 + len(bin_padded) if bin_padded else 0)
    out = bytearray(struct.pack("<III", em.GLB_MAGIC, 2, total))
    out += struct.pack("<II", len(json_blob), em.CHUNK_JSON) + json_blob
    if bin_padded:
        out += struct.pack("<II", len(bin_padded), em.CHUNK_BIN) + bin_padded
    return bytes(out)


class GlbIoTests(unittest.TestCase):
    def test_roundtrip(self):
        doc = {"asset": {"version": "2.0"}, "images": [], "bufferViews": []}
        blob = make_glb(doc, b"\x01\x02\x03\x04")
        doc2, binary2 = em.parse_glb(blob)
        self.assertEqual(doc2["asset"]["version"], "2.0")
        self.assertEqual(binary2, b"\x01\x02\x03\x04")

    def test_rejects_non_glb(self):
        with self.assertRaises(ValueError):
            em.parse_glb(b"not a glb at all........")

    def test_image_bytes_slice(self):
        binary = b"AAAA" + b"TEXTUREBYTES" + b"BBBBB"
        doc = {
            "bufferViews": [
                {"buffer": 0, "byteOffset": 4, "byteLength": len(b"TEXTUREBYTES")},
                {"buffer": 0, "byteOffset": 16, "byteLength": 5},
            ],
            "images": [{"bufferView": 0}, {"bufferView": 1}],
        }
        blob = make_glb(doc, binary)
        doc2, binary2 = em.parse_glb(blob)
        self.assertEqual(em.image_bytes(doc2, binary2, doc2["images"][0]), b"TEXTUREBYTES")
        self.assertEqual(em.image_bytes(doc2, binary2, doc2["images"][1]), b"BBBBB")

    def test_build_glb_pads_binary(self):
        doc = {"bufferViews": [], "images": [], "buffers": [{"byteLength": 3}]}
        binary = b"abc"  # 3 bytes → padded a 4
        blob = em.build_glb(doc, binary)
        doc2, binary2 = em.parse_glb(blob)
        self.assertEqual(binary2, b"abc\x00")
        # O byteLength do buffer sobrevive intacto (quem o atualiza é o
        # enrich_glb, o build só re-emprequeta).
        self.assertEqual(doc2["buffers"][0]["byteLength"], 3)


class SynthesisTests(unittest.TestCase):
    def test_flat_albedo_is_flat_normal(self):
        flat = np.full((32, 32, 4), 128, dtype=np.uint8)
        normal = em.synthesize_normal(flat)
        self.assertTrue(np.allclose(normal[..., 0], 128, atol=1))
        self.assertTrue(np.allclose(normal[..., 1], 128, atol=1))
        self.assertTrue(np.all(normal[..., 2] > 200), "Z dominante numa superfície plana")

    def test_gradient_normal_tilts_against_slope(self):
        # Luma a subir para +X: a normal inclina para -X (canal R < 0.5).
        # Rampa ÍNGREME (passo ~7/texel) — uma rampa suave tilta <1 byte,
        # abaixo de qualquer limiar honesto.
        ramp = np.tile(np.linspace(20, 230, 64, dtype=np.uint8), (64, 1))
        albedo = np.stack([ramp] * 3 + [np.full_like(ramp, 255)], axis=-1)
        normal = em.synthesize_normal(albedo, strength=4.0)
        center = normal[16:-16, 16:-16, 0].astype(np.int32)
        self.assertTrue(
            np.all(center < 126),
            f"gradiente ascendente inclina para -X (media {center.mean():.1f})",
        )

    def test_orm_bounds(self):
        rng = np.random.default_rng(7)
        albedo = rng.integers(0, 255, (48, 48, 4), dtype=np.uint8)
        orm = em.synthesize_orm(albedo)
        # Metallic é SEMPRE zero no pool (B = 0).
        self.assertTrue(np.all(orm[..., 2] == 0))
        # AO e roughness ficam dentro das bandas pretendidas.
        self.assertTrue(orm[..., 0].min() >= int(0.55 * 255) - 1)
        self.assertTrue(orm[..., 1].max() <= 255)
        # Zona LISA (cor constante) → roughness MÁXIMA (sem detalhe),
        # AO neutro no topo da banda.
        smooth = np.full((48, 48, 4), 90, dtype=np.uint8)
        orm_s = em.synthesize_orm(smooth)
        self.assertTrue(orm_s[..., 1].mean() > int(0.9 * 255), f"liso → rough ({orm_s[..., 1].mean():.0f})")
        self.assertTrue(orm_s[..., 0].mean() > int(0.95 * 255), "AO neutro no plano")

    def test_emissive_mask_isolated_quartile(self):
        # Fundo cinza escuro + um retângulo quente brilhante.
        albedo = np.full((64, 64, 4), 40, dtype=np.uint8)
        albedo[24:40, 24:40, 0] = 255
        albedo[24:40, 24:40, 1] = 180
        albedo[24:40, 24:40, 2] = 60
        mask = em.emissive_mask(albedo)
        inside = mask[24:40, 24:40].mean()
        outside = mask.mean() - inside * 256 / 4096
        self.assertGreater(inside, 0.5, "região quente acende")
        self.assertLess(outside, 0.05, "fundo escuro fica apagado")


class HeuristicTests(unittest.TestCase):
    def test_wants_emissive_by_name(self):
        self.assertTrue(em.wants_emissive({"name": "Window_Glass_01"}))
        self.assertTrue(em.wants_emissive({"name": "LanternFlame"}))
        self.assertTrue(em.wants_emissive({"name": "Crystal_core"}))
        self.assertFalse(em.wants_emissive({"name": "wood_wall"}))
        self.assertFalse(em.wants_emissive({"name": "RoofTiles"}))
        self.assertFalse(em.wants_emissive({}))


class AppendTests(unittest.TestCase):
    def test_append_image_extends_views_in_order(self):
        doc = {"bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 4}], "images": [{"bufferView": 0}]}
        binary = bytearray(b"\x01\x02\x03\x04")
        idx = em.append_image(doc, binary, b"\xaa" * 5)
        self.assertEqual(idx, 1)
        self.assertEqual(doc["bufferViews"][1]["byteOffset"], 4)
        self.assertEqual(doc["bufferViews"][1]["byteLength"], 5)
        # pad do blob (5 → 8) alinha a appends seguintes
        idx2 = em.append_image(doc, binary, b"\xbb")
        self.assertEqual(doc["bufferViews"][2]["byteOffset"], 12)
        self.assertEqual(idx2, 2)


if __name__ == "__main__":
    unittest.main()
