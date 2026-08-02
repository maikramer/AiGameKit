"""Orçamento de faces e texturas LOD em função do volume mundo (``char_m``).

Categoria define o bias de densidade (``target_faces`` a ~2 m). Volume escala
faces ~ área ``(char/REF)^2``. Atlas lod0 segue a ladder de paint; lod1/2 = /2 /4
com snap a múltiplos de 64 px.
"""

from __future__ import annotations

from aigamekit_shared.paint_budget import paint_texture_for_char

# Diâmetro equivalente onde a categoria recebe o orçamento cheio.
LOD_FACE_REF_M = 2.0
LOD_FACE_POWER = 2.0
LOD_FACE_SCALE_FLOOR = 0.12
LOD_FACE_SCALE_CEIL = 1.0
# Piso absoluto de faces quando ``char_m`` está presente.
LOD_FACES_ABS_MIN = 800

LOD_TEX_SNAP = 64
LOD_TEX_MIN = 64


def lod_face_scale(char_m: float) -> float:
    """Escala ``[FLOOR, CEIL]`` para ``(char_m / REF)^POWER``."""
    c = float(char_m)
    if c <= 0:
        return LOD_FACE_SCALE_CEIL
    raw = (c / LOD_FACE_REF_M) ** LOD_FACE_POWER
    return float(max(LOD_FACE_SCALE_FLOOR, min(LOD_FACE_SCALE_CEIL, raw)))


def snap_tex_64(size: int, *, cap: int | None = None, floor: int = LOD_TEX_MIN) -> int:
    """Arredonda o lado do atlas ao múltiplo de 64 mais próximo, com piso/tecto."""
    n = int(size)
    lo = max(LOD_TEX_MIN, int(floor))
    if n <= 0:
        n = lo
    snapped = int(round(n / LOD_TEX_SNAP) * LOD_TEX_SNAP)
    snapped = max(lo, snapped)
    if cap is not None and int(cap) > 0:
        snapped = min(snapped, int(cap))
        # Cap pode não ser múltiplo de 64 — re-snap para baixo se necessário.
        if snapped > lo and snapped % LOD_TEX_SNAP != 0:
            snapped = max(lo, (snapped // LOD_TEX_SNAP) * LOD_TEX_SNAP)
    return int(max(lo, snapped))


def lod_texture_size_for_char(char_m: float, *, quality_cap: int) -> int:
    """Lado do atlas lod0 por volume (mesmos buckets que paint) + snap64."""
    raw = paint_texture_for_char(char_m, quality_cap=quality_cap)
    return snap_tex_64(raw, cap=max(LOD_TEX_MIN, int(quality_cap)))


def lod_texture_ladder(lod0: int) -> tuple[int, int, int]:
    """``(lod0, lod1=/2, lod2=/4)`` com snap64 e piso 64."""
    t0 = snap_tex_64(int(lod0))
    t1 = snap_tex_64(t0 // 2)
    t2 = snap_tex_64(t0 // 4)
    return t0, t1, t2
