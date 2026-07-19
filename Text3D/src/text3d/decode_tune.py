"""Fórmulas puras de tuning do decode Omni (bounds, mc_level, chunks).

Sem torch — testável em CI. O generator resolve estes valores no momento do
decode; o CLI apenas escolhe modos (``auto``/``cube``/valores explícitos).

Três problemas atacados:

1. **Bounds anisotrópicos** (:func:`bounds_for_bbox`): o grid de marching cubes
   usa o MESMO número de células por eixo sobre ``bounds``. O default histórico
   é o cubo ``±1.01`` — 25%+ da resolução desperdiçada em margem vazia e, pior,
   assets achatados (porta, espada, edifício) ficam com paredes sub-voxel no
   eixo fino → buracos. Encolher ``bounds`` ao aspecto da bbox dá voxels
   proporcionais ao objecto sem custo de VRAM/tempo.

2. **mc_level auto** (:func:`auto_mc_level`): iso-nível ligeiramente negativo
   engorda a superfície ~fracção de voxel e fecha pinholes do MC; escala com o
   octree para o efeito ser constante em unidades-mundo.

3. **Chunks dinâmicos** (:func:`auto_num_chunks`): ``num_chunks`` é o batch de
   queries ao geo-decoder — deve escalar com a VRAM livre no decode (depois dos
   pesos colocados/offloaded), não com o octree. Com group offload leaf+stream
   os pesos saem da GPU e a ativação pode usar quase tudo.
"""

from __future__ import annotations

from gamedev_shared.vram_budget import (
    DEFAULT_VRAM_FRACTION as CHUNKS_VRAM_FRACTION,
)
from gamedev_shared.vram_budget import (
    TEXT3D_CHUNKS_HI as MAX_NUM_CHUNKS,
)
from gamedev_shared.vram_budget import (
    TEXT3D_CHUNKS_LO as MIN_NUM_CHUNKS,
)

# Margem do cubo clássico do Omni (box_v upstream).
DEFAULT_BOX_V = 1.01

# Folga sobre o aspecto da bbox: o modelo não respeita a bbox na perfeição;
# cortar rente clipava o field e abria buracos na fronteira.
BOUNDS_REL_MARGIN = 0.06
BOUNDS_ABS_SLACK = 0.03
# Piso por eixo (fracção do eixo maior): tolera leakage em eixos muito finos
# (espada 0.06 → nunca amostrar abaixo de 0.20 do cubo).
BOUNDS_MIN_AXIS_FRAC = 0.20

# mc_level auto: -1 «logit» distribuído pelo octree (≈ upstream -1/512 em 512).
AUTO_MC_LEVEL_SCALE = -1.0
AUTO_MC_LEVEL_MIN = -0.01

# Octree a partir do qual o decoder denso (vanilla) vira armadilha: o field
# interior é ruído e o grid denso visita-o todo. Decoders surface-focused
# (flashvdm) amostram só perto da superfície.
SURFACE_DECODER_MIN_OCTREE = 448


def bounds_for_bbox(
    bbox: list[float] | tuple[float, ...] | None,
    *,
    box_v: float = DEFAULT_BOX_V,
    rel_margin: float = BOUNDS_REL_MARGIN,
    abs_slack: float = BOUNDS_ABS_SLACK,
    min_axis_frac: float = BOUNDS_MIN_AXIS_FRAC,
) -> list[float] | None:
    """Bounds 6-float por-eixo a partir da bbox Omni (aspect L,H,W).

    Args:
        bbox: 3 floats ``[L,H,W]`` (aspect 0-1) ou AABB 6-float; ``None`` → sem
            sinal (devolve ``None`` = manter cubo).
        box_v: extensão máxima por eixo (margem clássica do Omni).
        rel_margin / abs_slack: folga sobre o aspecto (anti-clipping).
        min_axis_frac: piso por eixo como fracção do eixo maior.

    Returns:
        ``[-x,-y,-z,x,y,z]`` ou ``None`` quando não há ganho (aspecto ~cúbico
        ou bbox inválida) — caller mantém o cubo clássico.
    """
    if bbox is None:
        return None
    vals = [float(v) for v in bbox]
    if len(vals) == 6:
        vals = [abs(vals[3] - vals[0]), abs(vals[4] - vals[1]), abs(vals[5] - vals[2])]
    if len(vals) != 3:
        return None
    m = max(vals)
    if m <= 0 or min(vals) < 0:
        return None
    aspect = [v / m for v in vals]

    extents = []
    for a in aspect:
        e = a * (1.0 + rel_margin) + abs_slack
        e = max(e, min_axis_frac)
        extents.append(min(box_v, e))

    # Aspecto ~cúbico → cubo clássico (evita churn de fingerprint sem ganho).
    if min(extents) >= 0.95 * box_v:
        return None
    ex, ey, ez = extents
    return [-ex, -ey, -ez, ex, ey, ez]


def auto_mc_level(octree_resolution: int) -> float:
    """Iso-nível auto: ligeiro negativo ∝ 1/octree (fecha pinholes MC).

    Equivale ao default upstream ``-1/512`` em octree 512 e escala para manter
    a «engorda» ~constante em unidades-mundo noutros octrees.
    """
    o = max(int(octree_resolution), 64)
    return max(AUTO_MC_LEVEL_MIN, AUTO_MC_LEVEL_SCALE / float(o))


def resolve_mc_level(mc_level: float | str | None, octree_resolution: int) -> float:
    """``"auto"``/``None`` → :func:`auto_mc_level`; número → passthrough."""
    if mc_level is None:
        return auto_mc_level(octree_resolution)
    if isinstance(mc_level, str):
        key = mc_level.strip().lower()
        if key in ("auto", ""):
            return auto_mc_level(octree_resolution)
        return float(mc_level)
    return float(mc_level)


def prefer_surface_decoder(volume_decoder: str, octree_resolution: int) -> bool:
    """True se o decoder denso deve ser trocado por surface-focused (flashvdm).

    Vanilla a octree ≥ :data:`SURFACE_DECODER_MIN_OCTREE` visita o interior
    inteiro do field (ruído) — mais lixo interno E mais VRAM.
    """
    return volume_decoder in ("vanilla", "hierarchical") and int(octree_resolution) >= SURFACE_DECODER_MIN_OCTREE


def bytes_per_query_default() -> int:
    """Custo por query (env ``TEXT3D_DECODE_BYTES_PER_QUERY`` > default)."""
    from gamedev_shared.vram_budget import text3d_bytes_per_query

    return text3d_bytes_per_query()


def auto_num_chunks(
    free_vram_bytes: int | None,
    *,
    bytes_per_query: int | None = None,
    fraction: float = CHUNKS_VRAM_FRACTION,
    lo: int = MIN_NUM_CHUNKS,
    hi: int = MAX_NUM_CHUNKS,
) -> int | None:
    """Batch de queries do decode a partir da VRAM livre (clamp ``lo``..``hi``).

    ``None`` sem sinal de VRAM (CPU / mem_get_info indisponível) — caller
    mantém o ``num_chunks`` estático do tier. Implementação canónica:
    :func:`gamedev_shared.vram_budget.text3d_num_chunks`.
    """
    from gamedev_shared.vram_budget import text3d_num_chunks

    return text3d_num_chunks(
        free_vram_bytes,
        bytes_per_query=bytes_per_query,
        fraction=fraction,
        lo=lo,
        hi=hi,
    )
