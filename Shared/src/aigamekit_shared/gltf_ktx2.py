"""Normalização do DFD de KTX2 UASTC em GLBs — channelType single-channel → RGB.

Contexto (2026-09-10): o Bevy 0.19 fatia o payload UASTC pelo block size do
formato-alvo derivado do DFD. Texturas grayscale (ex.: AO do Materialize) saem
do ``gltf-transform uastc`` com ``channelType = KHR_DF_CHANNEL_UASTC_RRRR``
(byte 4) → o transcoder escolhe BC4 (8 B/block) e fatia mal um payload UASTC
(16 B/block) → falha de transcode. O payload UASTC é idêntico em forma ao RGB
(o block é sempre 128 bits); apenas o byte do canal engana o consumer.

O fix é um patch de **1 byte** no DFD (canal do sample 0: 4 → 0 = RGB) — sem
recompressão, sem alterar o layout do GLB.

Layout empírico (verificado com ``ktx info`` do KTX-Software sobre o pool):
- KTX2: header com ``dfdByteOffset`` (u64@48 nos ficheiros standard; alguns
  writers usam pares u32 — o localizador aceita ambos e valida a assinatura).
- DFD: ``dfdTotalSize`` u32@+0, basic block @+8 (type u16=2), sample 0 com
  ``bitLength``@+30 (127 = block UASTC 128 bits) e ``channelType``@+31.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

KTX2_MAGIC = b"\xabKTX"

# KHR_DF_CHANNEL_UASTC_*: 0=RGB, 1=RGBA; 2/3/4 (RRR/RRG/RRRR) partem o Bevy.
_CHANNEL_OK = (0, 1)


@dataclass(frozen=True)
class DfdFinding:
    """Resultado do scan de uma imagem KTX2 embutida num GLB."""

    image_index: int
    image_name: str
    dfd_offset_in_ktx2: int
    channel_type: int
    bit_length: int

    @property
    def needs_fix(self) -> bool:
        return self.channel_type not in _CHANNEL_OK


def _locate_dfd(ktx2: bytes) -> tuple[int, int] | None:
    """Devolve ``(dfd_offset, dfd_total_size)`` ou ``None`` se não validar.

    Aceita ``dfdByteOffset`` como u64@48 (standard) ou u32@48 (writers com
    pares u32); valida que aponta para um DFD plausível (totalSize 24..256 e
    basic block type=2 no sítio esperado).
    """
    candidates: list[int] = []
    try:
        candidates.append(struct.unpack("<Q", ktx2[48:56])[0])
        candidates.append(struct.unpack("<I", ktx2[48:52])[0])
    except struct.error:
        return None
    for off in candidates:
        if not 0 < off < len(ktx2) - 24:
            continue
        (total,) = struct.unpack("<I", ktx2[off : off + 4])
        if 24 <= total <= 256 and ktx2[off + 8] == 0x02:
            return off, total
    return None


def scan_glb_ktx2_dfd(glb_path: str | Path) -> list[DfdFinding]:
    """Scan das imagens KTX2 de um GLB — DFD channelType de cada uma.

    Não altera nada; imagens não-KTX2 são ignoradas silenciosamente.
    """
    data = bytearray(Path(glb_path).read_bytes())
    views = _glb_binary_slice(data)
    if views is None:
        return []
    bin_start, json_doc = views
    findings: list[DfdFinding] = []
    for i, img in enumerate(json_doc.get("images", [])):
        bv_idx = img.get("bufferView")
        if bv_idx is None:
            continue
        bv = json_doc["bufferViews"][bv_idx]
        off = bv.get("byteOffset", 0)
        end = off + bv["byteLength"]
        k = bytes(data[bin_start + off : bin_start + end])
        if k[:4] != KTX2_MAGIC:
            continue
        loc = _locate_dfd(k)
        if loc is None:
            continue
        dfd_off, _total = loc
        findings.append(
            DfdFinding(
                image_index=i,
                image_name=str(img.get("name", "")),
                dfd_offset_in_ktx2=dfd_off,
                channel_type=k[dfd_off + 31],
                bit_length=k[dfd_off + 30],
            )
        )
    return findings


def fix_ktx2_dfd(ktx2_path: str | Path, *, dry_run: bool = False) -> int:
    """Normaliza channelType single-channel → RGB num ficheiro KTX2 avulso.

    Mesmo patch de 1 byte (DFD+31) do :func:`fix_glb_ktx2_dfd`, para KTX2
    produzidos fora de GLB (ex.: ``ktx create`` directo num PNG grayscale).
    Idempotente; escreve in-place.

    Returns:
        1 se corrigiu, 0 caso contrário (não-KTX2, DFD não localizado, já RGB
        ou ``dry_run``).
    """
    path = Path(ktx2_path)
    data = bytearray(path.read_bytes())
    if bytes(data[:4]) != KTX2_MAGIC:
        return 0
    loc = _locate_dfd(bytes(data))
    if loc is None:
        return 0
    chan_pos = loc[0] + 31
    if data[chan_pos] in _CHANNEL_OK:
        return 0
    if not dry_run:
        data[chan_pos] = 0  # KHR_DF_CHANNEL_UASTC_RGB
        path.write_bytes(bytes(data))
    return 1


def fix_glb_ktx2_dfd(glb_path: str | Path, *, dry_run: bool = False) -> int:
    """Normaliza channelType single-channel → RGB (0) nos KTX2 UASTC do GLB.

    Patch de 1 byte por textura (DFD+31); bitLength fica intacto (payload
    UASTC não muda). Idempotente. Escreve o ficheiro in-place (mesmo tamanho).

    Returns:
        Número de bytes corrigidos (0 se nada a fazer ou ``dry_run``).
    """
    path = Path(glb_path)
    data = bytearray(path.read_bytes())
    views = _glb_binary_slice(data)
    if views is None:
        return 0
    bin_start, json_doc = views
    fixed = 0
    for img in json_doc.get("images", []):
        bv_idx = img.get("bufferView")
        if bv_idx is None:
            continue
        bv = json_doc["bufferViews"][bv_idx]
        off = bv.get("byteOffset", 0)
        start = bin_start + off
        k = bytes(data[start : start + bv["byteLength"]])
        if k[:4] != KTX2_MAGIC:
            continue
        loc = _locate_dfd(k)
        if loc is None:
            continue
        dfd_off, _total = loc
        chan_pos = start + dfd_off + 31
        if data[chan_pos] not in _CHANNEL_OK:
            data[chan_pos] = 0  # KHR_DF_CHANNEL_UASTC_RGB
            fixed += 1
    if fixed and not dry_run:
        path.write_bytes(bytes(data))
    return fixed


def _glb_binary_slice(data: bytes) -> tuple[int, dict] | None:
    """``(byte_offset_do_BIN_chunk, json)`` ou ``None`` se não for GLB válido."""
    if len(data) < 28 or data[:4] != b"glTF":
        return None
    (jlen,) = struct.unpack("<I", data[12:16])
    try:
        json_doc = json.loads(data[20 : 20 + jlen])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return 20 + jlen + 8, json_doc
