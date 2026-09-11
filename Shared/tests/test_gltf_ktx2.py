"""Normalização DFD de KTX2 UASTC em GLBs (channelType RRRR → RGB).

Cobertura do módulo ``aigamekit_shared.gltf_ktx2`` — o fix de 1 byte para o
transcode do Bevy 0.19 (texturas grayscale AO com DFD single-channel fazem o
transcoder escolher BC4 8 B/block e fatiar mal o payload UASTC 16 B/block).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from aigamekit_shared.gltf_ktx2 import fix_glb_ktx2_dfd, scan_glb_ktx2_dfd


def _ktx2(channel_type: int = 4, bit_length: int = 127) -> bytes:
    """KTX2 sintético mínimo com DFD no layout dos ficheiros do pool."""
    dfd_total = 44
    dfd_off = 96 + 24  # header 96 + 1 levelIndex (não usado pelo localizador)
    # DFD: totalSize u32 | 4 bytes pad | basic block (type u16=2, size u16=40,
    # colorModel=3 UASTC, primaries, transfer, flags, dims, bytesPlane=16) |
    # sample0: bitLength@+30, channelType@+31
    dfd = bytearray(dfd_total)
    struct.pack_into("<I", dfd, 0, dfd_total)
    dfd[8:10] = struct.pack("<H", 2)  # descriptorType BASICFORMAT
    struct.pack_into("<H", dfd, 10, 40)  # block size
    dfd[12 + 4] = 3  # colorModel UASTC (offset +16 do DFD... só coerência)
    dfd[12] = 0xA6  # padrão observado nos ficheiros reais
    dfd[16] = 3
    dfd[20] = 16  # bytesPlane0 = 16 (UASTC 128-bit block)
    dfd[30] = bit_length
    dfd[31] = channel_type

    k = bytearray()
    k += b"\xabKTX 20\xbb\r\n\x1a\n"
    k += struct.pack("<13I", 0, 1, 4, 4, 0, 0, 1, 1, 2, dfd_off, dfd_total, 0, 0)
    k += b"\x00" * (dfd_off - len(k))
    k += dfd
    k += b"\x00" * 64  # payload fictício
    return bytes(k)


def _glb(images: list[bytes], names: list[str] | None = None) -> bytes:
    """GLB mínimo com as imagens KTX2/PNG embutidas no BIN chunk."""
    bin_payload = bytearray()
    views = []
    for img in images:
        off = len(bin_payload)
        bin_payload += img
        views.append({"byteOffset": off, "byteLength": len(img)})
    json_doc = {
        "asset": {"version": "2.0"},
        "bufferViews": views,
        "images": [
            {"bufferView": i, "mimeType": "image/ktx2", "name": (names[i] if names else f"img{i}")}
            for i in range(len(images))
        ],
    }
    json_bytes = json.dumps(json_doc).encode()
    while len(json_bytes) % 4:
        json_bytes += b" "
    glb = bytearray()
    glb += b"glTF" + struct.pack("<II", 2, 0)
    glb += struct.pack("<I", len(json_bytes)) + b"JSON" + json_bytes
    glb += struct.pack("<I", len(bin_payload)) + b"BIN\x00" + bin_payload
    struct.pack_into("<I", glb, 8, len(glb))
    return bytes(glb)


class TestScanGlbKtx2Dfd:
    def test_detects_single_channel(self, tmp_path: Path) -> None:
        p = tmp_path / "a.glb"
        p.write_bytes(_glb([_ktx2(channel_type=4)], ["ao"]))
        findings = scan_glb_ktx2_dfd(p)
        assert len(findings) == 1
        assert findings[0].channel_type == 4
        assert findings[0].bit_length == 127
        assert findings[0].image_name == "ao"
        assert findings[0].needs_fix is True

    @pytest.mark.parametrize("chan", [0, 3])
    def test_rgb_and_rgba_are_ok(self, tmp_path: Path, chan: int) -> None:
        p = tmp_path / "a.glb"
        p.write_bytes(_glb([_ktx2(channel_type=chan)]))
        findings = scan_glb_ktx2_dfd(p)
        assert len(findings) == 1
        assert findings[0].needs_fix is False

    def test_ignores_png_and_non_glb(self, tmp_path: Path) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        p = tmp_path / "a.glb"
        p.write_bytes(_glb([png]))
        assert scan_glb_ktx2_dfd(p) == []
        other = tmp_path / "b.bin"
        other.write_bytes(b"not a glb")
        assert scan_glb_ktx2_dfd(other) == []

    def test_mixed_images(self, tmp_path: Path) -> None:
        # 0=RGB, 3=RGBA (icons), 4=RRR (quebra o Bevy), 5=RRRG (canal real).
        p = tmp_path / "a.glb"
        p.write_bytes(_glb([_ktx2(0), _ktx2(4), _ktx2(3), _ktx2(5)]))
        findings = scan_glb_ktx2_dfd(p)
        assert [f.channel_type for f in findings] == [0, 4, 3, 5]
        assert [f.needs_fix for f in findings] == [False, True, False, False]


class TestFixGlbKtx2Dfd:
    def test_patches_single_byte(self, tmp_path: Path) -> None:
        p = tmp_path / "a.glb"
        original = _glb([_ktx2(channel_type=4)], ["ao"])
        p.write_bytes(original)
        n = fix_glb_ktx2_dfd(p)
        assert n == 1
        data = p.read_bytes()
        assert len(data) == len(original)  # mesmo tamanho — patch in-place
        findings = scan_glb_ktx2_dfd(p)
        assert findings[0].channel_type == 0
        assert findings[0].bit_length == 127  # payload intacto

    def test_idempotent(self, tmp_path: Path) -> None:
        p = tmp_path / "a.glb"
        p.write_bytes(_glb([_ktx2(4), _ktx2(0)]))
        assert fix_glb_ktx2_dfd(p) == 1
        assert fix_glb_ktx2_dfd(p) == 0  # segunda passada: nada a fazer

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        p = tmp_path / "a.glb"
        original = _glb([_ktx2(4)])
        p.write_bytes(original)
        assert fix_glb_ktx2_dfd(p, dry_run=True) == 1
        assert p.read_bytes() == original

    def test_only_bad_images_counted(self, tmp_path: Path) -> None:
        p = tmp_path / "a.glb"
        p.write_bytes(_glb([_ktx2(0), _ktx2(4), _ktx2(4), _ktx2(3)]))
        assert fix_glb_ktx2_dfd(p) == 2  # RGBA(3) intocado

    def test_non_glb_returns_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "x.bin"
        p.write_bytes(b"junk")
        assert fix_glb_ktx2_dfd(p) == 0


class TestLayoutAgainstPool:
    """Contrato do localizador DFD: u64@48 standard e u32@48 (writer compacto)."""

    def _dfd_at(self, k: bytes) -> bytes | None:
        import aigamekit_shared.gltf_ktx2 as m

        loc = m._locate_dfd(k)
        return k[loc[0] : loc[0] + loc[1]] if loc else None

    def test_locates_standard_u64_offset(self) -> None:
        k = _ktx2(4)
        dfd = self._dfd_at(k)
        assert dfd is not None and dfd[31] == 4 and dfd[30] == 127

    def test_locates_u32_pair_offset(self) -> None:
        # Reescrever o header com pares u32 (off@48, len@52) como alguns writers.
        k = bytearray(_ktx2(4))
        struct.pack_into("<I", k, 48, 120)
        struct.pack_into("<I", k, 52, 44)
        # DFD efetivamente a 120 (96+24): já está lá pelo offset default
        dfd = self._dfd_at(bytes(k))
        assert dfd is not None

    def test_rejects_garbage(self) -> None:
        import aigamekit_shared.gltf_ktx2 as m

        assert m._locate_dfd(b"\x00" * 512) is None


def test_rrr_variant_fixed(tmp_path: Path) -> None:
    # Apenas RRR (4) é normalizado — o valor que faz o Bevy escolher BC4.
    p = tmp_path / "a.glb"
    p.write_bytes(_glb([_ktx2(4)]))
    assert fix_glb_ktx2_dfd(p) == 1
    assert scan_glb_ktx2_dfd(p)[0].channel_type == 0


class TestFixKtx2Standalone:
    """fix_ktx2_dfd sobre ficheiros KTX2 avulsos (ktx create directo)."""

    def test_rgba_and_dual_channel_untouched(self, tmp_path: Path) -> None:
        from aigamekit_shared.gltf_ktx2 import fix_glb_ktx2_dfd

        p = tmp_path / "a.glb"
        original = _glb([_ktx2(3), _ktx2(5), _ktx2(6)])
        p.write_bytes(original)
        assert fix_glb_ktx2_dfd(p) == 0
        assert p.read_bytes() == original

    def test_patches_and_is_idempotent(self, tmp_path: Path) -> None:
        from aigamekit_shared.gltf_ktx2 import fix_ktx2_dfd

        p = tmp_path / "ao.ktx2"
        p.write_bytes(_ktx2(4))
        assert fix_ktx2_dfd(p) == 1
        assert fix_ktx2_dfd(p) == 0
        data = p.read_bytes()
        dfd_off = 96 + 24
        assert data[dfd_off + 31] == 0

    def test_rgb_untouched_and_non_ktx2(self, tmp_path: Path) -> None:
        from aigamekit_shared.gltf_ktx2 import fix_ktx2_dfd

        p = tmp_path / "ok.ktx2"
        original = _ktx2(0)
        p.write_bytes(original)
        assert fix_ktx2_dfd(p) == 0
        assert p.read_bytes() == original
        junk = tmp_path / "j.bin"
        junk.write_bytes(b"\x00" * 64)
        assert fix_ktx2_dfd(junk) == 0

    def test_dry_run(self, tmp_path: Path) -> None:
        from aigamekit_shared.gltf_ktx2 import fix_ktx2_dfd

        p = tmp_path / "ao.ktx2"
        original = _ktx2(4)
        p.write_bytes(original)
        assert fix_ktx2_dfd(p, dry_run=True) == 1
        assert p.read_bytes() == original
