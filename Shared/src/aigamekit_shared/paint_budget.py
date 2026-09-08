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
# Texels por triângulo: 5 = 2ª duplicação do orçamento (10, que já era 2x o
# sweet-spot antigo de 20); paint soft persistia no cap — mais faces = melhor
# projeção multi-vista.
PAINT_TEXELS_PER_FACE = 5.0
# Piso: props pequenos / LOD-like ainda unwrapam bem. Não vincula no ladder
# atual (256 → ~7k raw, 512 → ~29k raw); mantido como salvaguarda.
PAINT_FACES_MIN = 6_000
# Tecto: xatlas + raster multi-vista (~36 candidatos + N views). Acima disto
# o tempo explode com ganho mínimo no UNet (independente de faces).
# Hunyuan upstream remesh default = 40k; 320k (2x160k) para qualidade de paint
# em buildings — unwrap/raster ainda tolerável (~2x tempo do cap anterior).
PAINT_FACES_MAX = 320_000
# Tecto por VRAM (o que sobra para inference): o pico do worker de paint =
# UNet + raster ∝ faces. Âncoras medidas no pool (4050 6 GB): 160k nunca OOM,
# 298k-343k OOM intermitente, 288k-318k passa na maioria → 6 GB fica abaixo
# do cap de produto. Linear entre as âncoras, clamp ao cap de produto.
PAINT_FACES_AT_4GB = 160_000
PAINT_FACES_AT_8GB = 320_000
# Cache da VRAM total (NVML, GPU 0; ``None`` = sem GPU/CI → cap de produto).
_UNPROBED = object()
_VRAM_TOTAL_MIB: int | None | object = _UNPROBED
# V/F típico em malha triangular welded (antes do UV split).
PAINT_VERTS_PER_FACE = 0.55

# Ladder de atlas por silhueta (metros), calibrada a ``PAINT_TEX_REF_M``.
_PAINT_TEX_CHAR_BUCKET_M = 0.5
_PAINT_TEX_CHAR_PROP_M = 1.2
_PAINT_TEX_CHAR_MID_M = 3.5
# Silhueta de referência dos buckets acima. ``ref_m`` menor (ex. humanoid=1.0)
# desloca a ladder para cima: o mesmo asset conta como "maior" no orçamento.
PAINT_TEX_REF_M = 2.0


def paint_texture_for_char(char_m: float, *, quality_cap: int, ref_m: float = PAINT_TEX_REF_M) -> int:
    """Lado do atlas (power-of-2) para o tamanho mundo, nunca acima do tier.

    Args:
        char_m: Silhueta equivalente ``sqrt(d1·d2)`` em metros.
        quality_cap: Tecto do tier QualityEngine / profile (ex. medium=2048).
        ref_m: Silhueta de referência da categoria (default 2 m). Os buckets são
            avaliados sobre ``char_m · (PAINT_TEX_REF_M / ref_m)``.

    Returns:
        512 (balde) / 1024 (prop) / 2048 / 4096 (casa+), clampado a ``quality_cap``.
    """
    cap = max(256, int(quality_cap))
    c = float(char_m)
    if c <= 0:
        return cap
    ref = float(ref_m) if float(ref_m) > 0 else PAINT_TEX_REF_M
    c *= PAINT_TEX_REF_M / ref
    if c <= _PAINT_TEX_CHAR_BUCKET_M:
        ladder = 512
    elif c <= _PAINT_TEX_CHAR_PROP_M:
        ladder = 1024
    elif c <= _PAINT_TEX_CHAR_MID_M:
        ladder = 2048
    else:
        ladder = 4096
    return int(min(ladder, cap))


def _vram_total_mib() -> int | None:
    """VRAM total da GPU 0 via NVML (cacheado; ``None`` sem GPU/erro)."""
    global _VRAM_TOTAL_MIB
    if _VRAM_TOTAL_MIB is _UNPROBED:
        try:
            from .gpu import gpu_total_mib

            _VRAM_TOTAL_MIB = gpu_total_mib(0)
        except Exception:
            _VRAM_TOTAL_MIB = None
    return _VRAM_TOTAL_MIB if isinstance(_VRAM_TOTAL_MIB, int) else None


def paint_faces_cap_for_vram(vram_total_mib: int | None = None) -> int:
    """Tecto de faces do ``_to_paint`` consoante a VRAM disponível para inference.

    O pico do worker de paint = UNet (~constante) + raster/unwrap ∝ faces.
    Sem VRAM conhecida (CI / import sem NVML) devolve o cap de produto.

    Args:
        vram_total_mib: VRAM total em MiB (default: auto-deteta GPU 0).

    Returns:
        Cap efectivo em ``[PAINT_FACES_MIN, PAINT_FACES_MAX]`` — linear entre
        as âncoras medidas (4 GiB → 160k, 8 GiB → 320k).
    """
    total = _vram_total_mib() if vram_total_mib is None else int(vram_total_mib)
    if not total or total <= 0:
        return PAINT_FACES_MAX
    lo_mib, lo_cap = 4096, PAINT_FACES_AT_4GB
    hi_mib, hi_cap = 8192, PAINT_FACES_AT_8GB
    if total <= lo_mib:
        cap = float(lo_cap)
    elif total >= hi_mib:
        cap = float(hi_cap)
    else:
        frac = (total - lo_mib) / (hi_mib - lo_mib)
        cap = lo_cap + frac * (hi_cap - lo_cap)
    return int(max(PAINT_FACES_MIN, min(PAINT_FACES_MAX, cap)))


def paint_target_faces(texture_size: int, vram_total_mib: int | None = None) -> int:
    """Faces óptimas para paint dado o tamanho do atlas.

    Args:
        texture_size: Lado do atlas (ex. 1024, 2048, 4096).
        vram_total_mib: VRAM total em MiB — o tecto por VRAM só reduz,
            nunca aumenta além do orçamento de produto (default: auto-deteta).

    Returns:
        Inteiro em ``[PAINT_FACES_MIN, min(PAINT_FACES_MAX, cap por VRAM)]``.
    """
    t = max(256, int(texture_size))
    raw = int((t * t * PAINT_UV_PACKING) / PAINT_TEXELS_PER_FACE)
    return max(PAINT_FACES_MIN, min(PAINT_FACES_MAX, raw, paint_faces_cap_for_vram(vram_total_mib)))


def paint_target_vertices(texture_size: int) -> int:
    """Estimativa de vértices soldados para :func:`paint_target_faces`."""
    return round(paint_target_faces(texture_size) * PAINT_VERTS_PER_FACE)


def texels_per_face(texture_size: int, faces: int, *, packing: float = PAINT_UV_PACKING) -> float:
    """Texels úteis por face (diagnóstico / logs)."""
    if faces <= 0:
        return 0.0
    t = max(1, int(texture_size))
    return (t * t * float(packing)) / float(faces)
