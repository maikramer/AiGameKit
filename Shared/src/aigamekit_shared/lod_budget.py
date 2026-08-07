"""Orçamento de faces e texturas LOD em função do tamanho de ecrã (``char_m``).

Categoria define o bias de densidade (``target_faces`` à sua ``ref_m``). O
orçamento escala com a **silhueta** projectada — ``sqrt(d1·d2)`` dos dois eixos
maiores — porque o que consome ecrã (e portanto pixels por triângulo) é a área
projectada, não o volume. O diâmetro equivalente de volume ``(L·H·W)^(1/3)``
subestima grosseiramente qualquer objecto alongado: um herói de 1.55 m mede
0.70 m em volume-equivalente (= um balde) mas 0.92 m em silhueta.

``ref_m`` é o tamanho a que a categoria recebe o orçamento cheio e é por
categoria (um humanoide nunca chega aos 2 m de silhueta de um edifício —
com ``ref_m`` global de 2 m ficava sempre colado ao ``FLOOR``).

Atlas lod0 segue a ladder de paint; lod1/2 = /2 /4 com snap a múltiplos de 64 px.
"""

from __future__ import annotations

from aigamekit_shared.paint_budget import paint_texture_for_char

# Silhueta onde a categoria recebe o orçamento cheio (default; ver ``ref_m``).
LOD_FACE_REF_M = 2.0
LOD_FACE_POWER = 2.0
LOD_FACE_SCALE_FLOOR = 0.12
LOD_FACE_SCALE_CEIL = 1.0
# Piso absoluto de faces quando ``char_m`` está presente.
LOD_FACES_ABS_MIN = 800

LOD_TEX_SNAP = 64
LOD_TEX_MIN = 64


def silhouette_equivalent_meters(size_m: list[float] | tuple[float, ...] | None) -> float | None:
    """Silhueta equivalente ``sqrt(d1·d2)`` dos dois eixos maiores de ``size_m``.

    Proxy para a área projectada em ecrã (orçamento de faces e de atlas).
    Devolve ``None`` sem pista utilizável.
    """
    if size_m is None:
        return None
    dims = sorted((float(v) for v in size_m if float(v) > 0), reverse=True)
    if not dims:
        return None
    if len(dims) == 1:
        return dims[0]
    return float((dims[0] * dims[1]) ** 0.5)


def lod_face_scale(char_m: float, *, ref_m: float = LOD_FACE_REF_M) -> float:
    """Escala ``[FLOOR, CEIL]`` para ``(char_m / ref_m)^POWER``."""
    c = float(char_m)
    ref = float(ref_m) if float(ref_m) > 0 else LOD_FACE_REF_M
    if c <= 0:
        return LOD_FACE_SCALE_CEIL
    raw = (c / ref) ** LOD_FACE_POWER
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


def lod_texture_size_for_char(char_m: float, *, quality_cap: int, ref_m: float = LOD_FACE_REF_M) -> int:
    """Lado do atlas lod0 pela silhueta (mesmos buckets que paint) + snap64."""
    raw = paint_texture_for_char(char_m, quality_cap=quality_cap, ref_m=ref_m)
    return snap_tex_64(raw, cap=max(LOD_TEX_MIN, int(quality_cap)))


def lod_texture_ladder(lod0: int) -> tuple[int, int, int]:
    """``(lod0, lod1=/2, lod2=/4)`` com snap64 e piso 64."""
    t0 = snap_tex_64(int(lod0))
    t1 = snap_tex_64(t0 // 2)
    t2 = snap_tex_64(t0 // 4)
    return t0, t1, t2
