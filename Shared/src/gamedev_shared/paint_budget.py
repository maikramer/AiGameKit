"""Orçamento de malha para Paint3D em função da resolução do atlas.

O paint Hunyuan (xatlas + raster multi-vista + bake) escala com **faces**,
nao com texels do UNet. Um ``_clean`` de marching-cubes (1-2M faces) gasta
horas no unwrap/bake; o atlas 2k/4k nao beneficia - fica ~1 texel/tri.

Derivacao do alvo:

* texels uteis no atlas ~ ``texture_size^2 * packing`` (packing UV ~0.55);
* queremos ``~texels_per_face`` por triangulo (16-24 = detalhe sem over-tess);
* ``faces = usable / texels_per_face``, clampado a um tecto de unwrap/raster.

Vértices soldados típicos ~ ``0.55 x faces`` (malha triangular welded).
Após UV unwrap o Paint3D explode verts por canto - o knobs relevante é faces.

O lado do atlas também escala com o tamanho mundo (``char_m``): um balde
não precisa do mesmo 2k/4k que uma casa — paint mais rústico, ``_to_paint`` menor.
"""

from __future__ import annotations

# Densidade de packing UV realista (xatlas em assets de jogo).
PAINT_UV_PACKING = 0.55
# Texels por triângulo: 10 = orçamento dobrado face ao sweet-spot antigo (20);
# paint em 80k mostrou-se soft — mais faces = melhor projeção multi-vista.
PAINT_TEXELS_PER_FACE = 10.0
# Piso: props pequenos / LOD-like ainda unwrapam bem (atlas 512 → ~14k raw).
PAINT_FACES_MIN = 6_000
# Tecto: xatlas + raster multi-vista (~36 candidatos + N views). Acima disto
# o tempo explode com ganho mínimo no UNet (independente de faces).
# Hunyuan upstream remesh default = 40k; 160k (2x80k) para qualidade de paint
# em buildings — unwrap/raster ainda tolerável (~2x tempo do cap antigo).
PAINT_FACES_MAX = 160_000
# V/F típico em malha triangular welded (antes do UV split).
PAINT_VERTS_PER_FACE = 0.55

# Ladder de atlas por diâmetro equivalente de volume (metros).
_PAINT_TEX_CHAR_BUCKET_M = 0.5
_PAINT_TEX_CHAR_PROP_M = 1.2
_PAINT_TEX_CHAR_MID_M = 3.5


def paint_texture_for_char(char_m: float, *, quality_cap: int) -> int:
    """Lado do atlas (power-of-2) para o tamanho mundo, nunca acima do tier.

    Args:
        char_m: Diâmetro equivalente ``(L·H·W)^(1/3)`` em metros.
        quality_cap: Tecto do tier QualityEngine / profile (ex. medium=2048).

    Returns:
        512 (balde) / 1024 (prop) / 2048 / 4096 (casa+), clampado a ``quality_cap``.
    """
    cap = max(256, int(quality_cap))
    c = float(char_m)
    if c <= 0:
        return cap
    if c <= _PAINT_TEX_CHAR_BUCKET_M:
        ladder = 512
    elif c <= _PAINT_TEX_CHAR_PROP_M:
        ladder = 1024
    elif c <= _PAINT_TEX_CHAR_MID_M:
        ladder = 2048
    else:
        ladder = 4096
    return int(min(ladder, cap))


def paint_target_faces(texture_size: int) -> int:
    """Faces óptimas para paint dado o tamanho do atlas.

    Args:
        texture_size: Lado do atlas (ex. 1024, 2048, 4096).

    Returns:
        Inteiro em ``[PAINT_FACES_MIN, PAINT_FACES_MAX]``.
    """
    t = max(256, int(texture_size))
    raw = int((t * t * PAINT_UV_PACKING) / PAINT_TEXELS_PER_FACE)
    return max(PAINT_FACES_MIN, min(PAINT_FACES_MAX, raw))


def paint_target_vertices(texture_size: int) -> int:
    """Estimativa de vértices soldados para :func:`paint_target_faces`."""
    return round(paint_target_faces(texture_size) * PAINT_VERTS_PER_FACE)


def texels_per_face(texture_size: int, faces: int, *, packing: float = PAINT_UV_PACKING) -> float:
    """Texels úteis por face (diagnóstico / logs)."""
    if faces <= 0:
        return 0.0
    t = max(1, int(texture_size))
    return (t * t * float(packing)) / float(faces)
