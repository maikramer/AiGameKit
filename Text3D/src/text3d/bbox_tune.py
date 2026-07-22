"""Autotune Hunyuan + repair scale a partir do tamanho mundo (metros).

Premissa: **genérico por escala física**, sem regras por tipo de objecto.
``category`` / ``bbox_preset`` são só *priors* de ``char_m`` quando falta
``size_m`` — igreja 10 m e humanoid 1.7 m usam a mesma fórmula.

O Omni gera no cubo normalizado; o marching cubes usa ``octree_resolution``.
Após escala para metros, ``voxel_m ≈ char_m / octree``. Este módulo:

1. Sobe octree/steps/chunks para aproximar um voxel-alvo (tecto VRAM).
2. Sugere ``morph_close`` em metros (~N voxels) para fundir double-shell
   fino do MC — escala com o asset, não com “building vs prop”.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Referência: props ~1.5 m ficam bem com octree do tier base (ex. balanced 256).
_REF_M = 1.5
# Alvo aproximado de voxel em metros (buracos MC ~1-2 voxels).
_TARGET_VOXEL_M = 0.025
# Escala relativa ao base do tier (evita OOM / tempos absurdos).
_MAX_SCALE = 3.0
_MIN_SCALE = 0.75
# Octree: calcular → se <160 → 160; senão degraus de 32 até 512.
_OCTREE_FLOOR = 160
_OCTREE_CEILING = 512
_OCTREE_STEP = 32

# Morph-close / «voxel merge»: N × voxel_m MC. Default ~1/8 voxel (1 vox
# derretia detalhe — capela). Auto ON: funde double-shell sub-voxel; remesh
# interno usa grid ≥ octree (ver ``morphological_close``). Opt-out:
# ``--morph-close 0``. Override: ``--morph-close-voxels`` / category map.
DEFAULT_MORPH_VOXELS = 0.125
_MORPH_VOXELS = DEFAULT_MORPH_VOXELS  # alias interno
_MORPH_FRAC_LO = 0.0002  # 0.02% char
_MORPH_FRAC_HI = 0.004  # 0.4% char
_MORPH_ABS_LO = 0.001  # metros
_MORPH_ABS_HI = 0.08

# Distância mínima de inspeção por categoria (metros): quão perto o jogador
# chega do asset num jogo típico. O alvo de voxel escala com isto — um mosquito
# decorativo nunca é visto a 5 cm; a parede de uma casa é inspecionada a ~1.5 m
# mas o detalhe sub-voxel vem de textura/normal bake, não de geometria.
_CATEGORY_APPROACH_M: dict[str, float] = {
    "weapon": 0.5,  # primeira pessoa, nas mãos
    "tool": 0.5,
    "chest": 0.8,
    "prop": 1.0,
    "furniture": 1.0,
    "door": 1.0,
    "humanoid": 1.0,
    "creature": 1.2,
    "rock": 1.5,
    "terrain": 2.0,  # cliffs / formações — vistas de longe
    "building": 1.5,  # aproximas-te da parede, não da fachada inteira
    "environment": 1.5,
    "vehicle": 1.5,
    "tree": 2.5,
}
_DEFAULT_APPROACH_M = 1.0

# Voxel-merge (N × voxel_m) por categoria. Rochas/cliffs: 3× default — menos
# detalhe geométrico, mais fecho de buracos MC / base aberta.
_CATEGORY_MORPH_VOXELS: dict[str, float] = {
    "terrain": 3.0 * DEFAULT_MORPH_VOXELS,  # 0.375
    "rock": 3.0 * DEFAULT_MORPH_VOXELS,
}

# bbox_preset → chave de approach (presets sem categoria própria).
_PRESET_APPROACH_KEY: dict[str, str] = {
    "chapel": "building",
    "building": "building",
    "tree": "tree",
    "furniture": "furniture",
    "humanoid": "humanoid",
    "humanoid-child": "humanoid",
    "quadruped": "creature",
    "sword": "weapon",
    "shield": "weapon",
    "crate": "prop",
    "door": "door",
    "barrel": "prop",
    "chest": "chest",
    "cube": "prop",
}

# Voxel-alvo por metro de distância de inspeção (parity: approach 1 m → 2.5 cm).
_VOXEL_PER_APPROACH_M = 0.025
_TARGET_VOXEL_LO = 0.012
_TARGET_VOXEL_HI = 0.06

# Multiplicador do alvo por tier de qualidade (QualityEngine): tiers baixos
# aceitam voxels maiores; tiers altos apertam (mais octree dentro dos tectos).
_QUALITY_VOXEL_FACTOR: dict[str, float] = {
    "fast": 1.6,
    "low": 1.3,
    "medium": 1.0,
    "high": 0.8,
    "highest": 0.65,
}


def target_voxel_for(
    category: str | None = None,
    bbox_preset: str | None = None,
    quality: str | None = None,
) -> float:
    """Voxel-alvo (metros) percetual: distância de inspeção x tier de qualidade.

    Sem sinal de categoria/preset usa o approach default (1 m → alvo clássico
    de 2.5 cm — paridade com o comportamento antigo).
    """
    approach = _DEFAULT_APPROACH_M
    cat_key = (category or "").strip().lower()
    preset_key = (bbox_preset or "").strip().lower()
    if cat_key in _CATEGORY_APPROACH_M:
        approach = _CATEGORY_APPROACH_M[cat_key]
    elif preset_key:
        mapped = _PRESET_APPROACH_KEY.get(preset_key)
        if mapped:
            approach = _CATEGORY_APPROACH_M.get(mapped, _DEFAULT_APPROACH_M)
    factor = _QUALITY_VOXEL_FACTOR.get((quality or "").strip().lower(), 1.0)
    raw = _VOXEL_PER_APPROACH_M * approach * factor
    return float(max(_TARGET_VOXEL_LO, min(_TARGET_VOXEL_HI, raw)))


# Fallback quando não há size_m: eixo maior típico (prior só — mesma fórmula).
_CATEGORY_CHAR_M: dict[str, float] = {
    "building": 6.0,
    "environment": 4.0,
    "furniture": 1.5,
    "prop": 1.0,
    "chest": 0.8,
    "door": 2.2,
    "weapon": 0.9,
    "tool": 0.7,
    "humanoid": 1.7,
    "creature": 1.5,
    "rock": 1.2,
    "tree": 5.0,
    "vehicle": 4.0,
}

_PRESET_CHAR_M: dict[str, float] = {
    "chapel": 7.0,
    "building": 6.0,
    "tree": 5.0,
    "furniture": 1.5,
    "humanoid": 1.7,
    "humanoid-child": 1.2,
    "quadruped": 1.4,
    "sword": 0.9,
    "shield": 1.0,
    "crate": 0.8,
    "door": 2.2,
    "barrel": 0.9,
    "chest": 0.8,
    "cube": 1.0,
}

# Ladder 160..512 step 32 (ver ``_snap_octree``).
_OCTREE_LADDER = tuple(range(_OCTREE_FLOOR, _OCTREE_CEILING + 1, _OCTREE_STEP))

# Tecto INFORMATIVO do latent (≠ tecto VRAM): acima disto o field do VAE não
# tem detalhe novo — sampling mais fino só resolve ruído interior (componentes
# soltos dentro da shell, «geração a vazar»). Calibrável com
# ``text3d bench-decode`` (summary.recommended_latent_ceiling); override via
# env TEXT3D_LATENT_OCTREE_CEILING.
LATENT_DETAIL_CEILING = 448
_LATENT_CEILING_ENV = "TEXT3D_LATENT_OCTREE_CEILING"


def latent_detail_ceiling() -> int:
    """Tecto informativo efectivo (env > constante)."""
    import os

    raw = os.environ.get(_LATENT_CEILING_ENV, "").strip()
    if raw:
        try:
            v = int(raw)
            if v >= 128:
                return v
        except ValueError:
            pass
    return LATENT_DETAIL_CEILING


@dataclass(frozen=True)
class BBoxTuneResult:
    """Resultado do autotune escala → Hunyuan (+ hint de morph-close)."""

    steps: int
    octree: int
    chunks: int
    char_m: float
    scale: float
    source: str  # "size_m" | "category" | "bbox_preset" | "none"
    applied: bool
    voxel_m: float = 0.0
    morph_close: float | None = None


def characteristic_meters(
    size_m: list[float] | tuple[float, ...] | None = None,
    *,
    category: str | None = None,
    bbox_preset: str | None = None,
) -> tuple[float | None, str]:
    """Eixo maior em metros + origem do sinal.

    Returns:
        ``(char_m, source)`` — ``char_m`` é ``None`` se não houver pista.
    """
    if size_m is not None:
        arr = [float(v) for v in size_m]
        if len(arr) == 3 and max(arr) > 0:
            return float(max(arr)), "size_m"

    if bbox_preset:
        key = str(bbox_preset).strip().lower()
        if key in _PRESET_CHAR_M:
            return _PRESET_CHAR_M[key], "bbox_preset"

    if category:
        key = str(category).strip().lower()
        if key in _CATEGORY_CHAR_M:
            return _CATEGORY_CHAR_M[key], "category"

    return None, "none"


def voxel_meters(char_m: float, octree: int) -> float:
    """Tamanho aproximado do voxel-mundo após escala Omni → metros."""
    if char_m <= 0 or octree <= 0:
        return 0.0
    return float(char_m) / float(octree)


def morph_close_voxels_for(
    category: str | None = None,
    *,
    explicit: float | None = None,
) -> float:
    """N de «voxel merge» (morph-close): explícito > category > default ``0.125``.

    ``terrain`` / ``rock`` → 3× default (cliffs/rochas: mais fecho, menos detalhe).
    """
    if explicit is not None:
        v = float(explicit)
        if v < 0:
            raise ValueError(f"morph_close_voxels deve ser >= 0, recebeu {explicit!r}")
        return v
    key = (category or "").strip().lower()
    if key in _CATEGORY_MORPH_VOXELS:
        return float(_CATEGORY_MORPH_VOXELS[key])
    return float(DEFAULT_MORPH_VOXELS)


def morph_close_meters(
    char_m: float,
    octree: int | None = None,
    *,
    voxels: float | None = None,
    category: str | None = None,
    target_voxel_m: float = _TARGET_VOXEL_M,
) -> float | None:
    """Distância de fecho morfológico (metros) escalada ao asset.

    Fórmula: ``~N x voxel_m``, com clamp absoluto e relativo a ``char_m``.
    ``N`` = ``voxels`` ou :func:`morph_close_voxels_for` (category/default).
    Sem ``octree``, assume ``target_voxel_m``. Devolve ``None`` se ``char_m`` inválido.
    """
    if char_m is None or char_m <= 0:
        return None
    n = float(voxels) if voxels is not None else morph_close_voxels_for(category)
    if n <= 0:
        return None
    voxel_m = voxel_meters(char_m, octree) if octree is not None and octree > 0 else float(target_voxel_m)
    raw = n * voxel_m
    lo = max(_MORPH_ABS_LO, _MORPH_FRAC_LO * float(char_m))
    hi = min(_MORPH_ABS_HI, _MORPH_FRAC_HI * float(char_m))
    if hi < lo:
        hi = lo
    return float(max(lo, min(hi, raw)))


def max_octree_for_vram(
    total_vram_gib: float | None,
    *,
    group_offload: bool = False,
) -> int:
    """Tecto de octree por VRAM aproximada (single-GPU efectiva).

    Com ``group_offload=True`` (DiT em leaf+stream), pesos quase não ocupam VRAM
    residente — tecto sobe para aproveitar a GPU em marching cubes / qualidade high.
    """
    if total_vram_gib is None:
        return 448 if group_offload else 384
    if group_offload:
        # Stream pesos → VRAM ≈ ativação + MC. Sacrifica tempo, enche a GPU.
        # 6 GB + sdnq-int4 + group_offload aguenta 448 (validado longhouse ~10 m).
        if total_vram_gib >= 10.0:
            return 512
        if total_vram_gib >= 6.0:
            return 448
        if total_vram_gib >= 5.0:
            return 384
        return 320
    if total_vram_gib >= 12.0:
        return 512
    if total_vram_gib >= 10.0:
        return 448
    if total_vram_gib >= 7.5:
        return 384
    if total_vram_gib >= 6.0:
        return 320
    return 256


def _snap_octree(value: int, *, lo: int = _OCTREE_FLOOR, hi: int = _OCTREE_CEILING) -> int:
    """Piso 160; acima disso, degrau de 32 até 512 (ou ``hi``/``lo`` mais apertados)."""
    floor = max(_OCTREE_FLOOR, int(lo))
    ceil = min(_OCTREE_CEILING, int(hi))
    if ceil < floor:
        ceil = floor
    v = int(value)
    if v <= floor:
        return floor
    # Nearest multiple of step above floor.
    snapped = floor + int(round((v - floor) / _OCTREE_STEP)) * _OCTREE_STEP
    return max(floor, min(ceil, snapped))


def tune_hunyuan_for_bbox(
    *,
    base_steps: int,
    base_octree: int,
    base_chunks: int,
    size_m: list[float] | tuple[float, ...] | None = None,
    category: str | None = None,
    bbox_preset: str | None = None,
    total_vram_gib: float | None = None,
    min_octree: int = _OCTREE_FLOOR,
    volume_decoder: str | None = None,
    group_offload: bool = False,
    target_voxel_m: float | None = None,
    quality: str | None = None,
) -> BBoxTuneResult:
    """Escala soft steps/octree/chunks + morph hint com o tamanho mundo.

    Args:
        base_*: Valores já resolvidos (quality/preset/hw-auto).
        size_m: ``[L,H,W]`` em metros (preferido).
        category / bbox_preset: prior de tamanho típico (não muda a fórmula).
        total_vram_gib: tecto de octree.
        min_octree: piso efectivo (≥160).
        volume_decoder: ignorado no piso (flashvdm abaixo de 256 → decode denso).
        group_offload: tecto mais alto (pesos em stream → VRAM para MC).
        target_voxel_m: voxel-mundo alvo explícito; ``None`` → percetual via
            :func:`target_voxel_for` (categoria/preset + tier de qualidade).
        quality: tier QualityEngine (só afeta o alvo percetual).

    Returns:
        Novo trio + morph/voxel. ``applied=False`` se não houver sinal de tamanho.
    """
    char_m, source = characteristic_meters(size_m, category=category, bbox_preset=bbox_preset)
    if char_m is None or char_m <= 0:
        return BBoxTuneResult(
            steps=int(base_steps),
            octree=int(base_octree),
            chunks=int(base_chunks),
            char_m=0.0,
            scale=1.0,
            source=source,
            applied=False,
            voxel_m=0.0,
            morph_close=None,
        )

    lo = max(_OCTREE_FLOOR, int(min_octree))
    # ``volume_decoder`` não sobe o piso: flashvdm abaixo de 256 → decode denso.

    # min(tecto VRAM, tecto latent, 512): acima do latent só gera ruído interno.
    hi = max(
        lo,
        min(
            max_octree_for_vram(total_vram_gib, group_offload=group_offload),
            latent_detail_ceiling(),
            _OCTREE_CEILING,
        ),
    )

    # Escala pelo eixo maior vs referência de prop.
    scale = max(_MIN_SCALE, min(_MAX_SCALE, float(char_m) / _REF_M))
    base_o = int(base_octree)
    # Alvo absoluto de voxel: char_m / voxel ~ octree desejado.
    if target_voxel_m is not None and target_voxel_m > 0:
        tv = float(target_voxel_m)
    else:
        tv = target_voxel_for(category, bbox_preset, quality)
    desired_abs = round(float(char_m) / tv)
    desired_scale = round(base_o * scale)
    # Média geométrica tamanho×tier; snap: <160→160, senão ±32 até 512/hi.
    desired = round((desired_abs * desired_scale) ** 0.5)
    octree = _snap_octree(desired, lo=lo, hi=hi)

    # Steps sobem mais suave que o octree (custo tempo linear-ish).
    step_scale = 0.9 + 0.1 * scale
    steps = round(int(base_steps) * step_scale)
    steps = max(16, min(60, int(steps)))

    # num_chunks é batch de queries ao geo-decoder — função da VRAM livre no
    # decode (auto_num_chunks no generator), não do octree. O valor do tier
    # fica como fallback estático para quando não há sinal de VRAM.
    chunks = int(base_chunks)

    vox = voxel_meters(float(char_m), octree)
    morph = morph_close_meters(float(char_m), octree, category=category, target_voxel_m=tv)

    changed = octree != int(base_octree) or steps != int(base_steps) or chunks != int(base_chunks)
    return BBoxTuneResult(
        steps=steps,
        octree=octree,
        chunks=chunks,
        char_m=float(char_m),
        scale=float(scale),
        source=source,
        applied=changed or source != "none",
        voxel_m=vox,
        morph_close=morph,
    )


def apply_bbox_tune(
    *,
    steps: int,
    octree: int,
    chunks: int,
    size_m: list[float] | tuple[float, ...] | None,
    category: str | None,
    bbox_preset: str | None,
    total_vram_gib: float | None,
    volume_decoder: str | None,
    tune_steps: bool,
    tune_octree: bool,
    tune_chunks: bool,
    group_offload: bool = False,
    target_voxel_m: float | None = None,
    quality: str | None = None,
) -> tuple[int, int, int, BBoxTuneResult]:
    """Aplica tune só nos eixos pedidos (soft flags).

    Returns:
        ``(steps, octree, chunks, result)``.
    """
    result = tune_hunyuan_for_bbox(
        base_steps=steps,
        base_octree=octree,
        base_chunks=chunks,
        size_m=size_m,
        category=category,
        bbox_preset=bbox_preset,
        total_vram_gib=total_vram_gib,
        volume_decoder=volume_decoder,
        group_offload=group_offload,
        target_voxel_m=target_voxel_m,
        quality=quality,
    )
    out_steps = result.steps if tune_steps else steps
    out_octree = result.octree if tune_octree else octree
    out_chunks = result.chunks if tune_chunks else chunks
    # Recalcular morph/voxel com octree efectivo (soft flags podem fixar octree).
    if result.char_m > 0:
        tv_eff = target_voxel_m if target_voxel_m else target_voxel_for(category, bbox_preset, quality)
        vox = voxel_meters(result.char_m, out_octree)
        morph = morph_close_meters(result.char_m, out_octree, category=category, target_voxel_m=tv_eff)
        result = BBoxTuneResult(
            steps=out_steps,
            octree=out_octree,
            chunks=out_chunks,
            char_m=result.char_m,
            scale=result.scale,
            source=result.source,
            applied=result.applied,
            voxel_m=vox,
            morph_close=morph,
        )
    return out_steps, out_octree, out_chunks, result


def resolve_morph_close(
    *,
    explicit: float | None,
    size_m: list[float] | tuple[float, ...] | None = None,
    category: str | None = None,
    bbox_preset: str | None = None,
    octree: int | None = None,
    auto: bool = True,
    morph_close_voxels: float | None = None,
) -> float | None:
    """Resolve morph-close soft: metros explícitos > auto(N×voxel) > None.

    - ``explicit > 0``: metros absolutos.
    - ``explicit == 0``: desligado.
    - ``explicit is None``: auto ``morph_close_voxels`` (ou category/default)
      × voxel_m da escala física. ``terrain``/``rock`` = 3× default.
      ``auto=False`` desliga.
    """
    if explicit is not None:
        if float(explicit) <= 0:
            return None
        return float(explicit)
    if not auto:
        return None
    char_m, _src = characteristic_meters(size_m, category=category, bbox_preset=bbox_preset)
    if char_m is None:
        return None
    n = morph_close_voxels_for(category, explicit=morph_close_voxels)
    return morph_close_meters(char_m, octree, voxels=n, category=category)


def scale_factor_to_meters(
    current_max: float,
    size_m: list[float] | tuple[float, ...] | None,
    *,
    tol: float = 0.02,
) -> float | None:
    """Factor de escala mesh (unidades Omni ~2u) → metros reais de ``size_m``.

    Só com ``size_m`` explícito (priors de categoria são para morph/octree,
    não para redimensionar mundo). ``None`` se não há sinal ou já em escala.
    """
    if size_m is None or current_max <= 0:
        return None
    arr = [float(v) for v in size_m]
    if len(arr) != 3 or max(arr) <= 0:
        return None
    factor = max(arr) / float(current_max)
    if abs(factor - 1.0) <= tol:
        return None
    return float(factor)


def size_m_from_mapping(raw: Any) -> list[float] | None:
    """Normaliza ``size_m`` vindo de CLI CSV / JSON / lista."""
    if raw is None:
        return None
    if isinstance(raw, str):
        from .omni_presets import parse_bbox_csv

        vals = parse_bbox_csv(raw)
    else:
        vals = [float(v) for v in raw]
    if len(vals) != 3:
        raise ValueError(f"size_m espera 3 floats L,H,W, recebeu {len(vals)}")
    return vals
