"""
Ajuste automático de parâmetros Part3D com base na geometria e VRAM.

Objectivo: reduzir pico de VRAM (Conditioner com muitas partes x pontos, VAE decode)
sem exigir que o utilizador conheça octree, chunks ou tamanhos de nuvem de pontos.

Estratégia para GPUs com pouca VRAM (≤8 GB):
  O Conditioner codifica ``(num_parts, 81920, dim)`` de uma vez.  Com muitas partes
  isto excede a VRAM facilmente (cada parte ≈ 81920 x 6 x 2B ≈ 1 MB de input, mas
  as activações intermédias no cross-attention explodem).
  → O autotune calcula ``cond_batch_size``: quantas partes processar de cada vez.
    O pipeline faz um loop, acumula resultados na CPU e concatena no fim.

Anti-OOM (<=6-8 GB):
  - ``cond_batch_size`` = 1
  - ``max_parts`` no DiT = 1 (features de várias partes estoiram o pico)
  - ``torch.compile`` conta overhead; DiT compile desaconselhado c/ offload em VRAM baixa
  - Preferir VRAM **livre** (`mem_get_info`) quando disponível
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Limites (alinhados a defaults.py)
# ---------------------------------------------------------------------------

_POINT_NUM_LEVELS = (50000, 45000, 40000, 32000, 24000)
_PROMPT_NUM_LEVELS = (128, 96, 64, 48, 32)
_SURFACE_PC_SIZE = 81920  # fixo pelo encoder X-Part (split hardcoded no modelo)
_BBOX_PC_SIZE = 81920
_OCTREE_LEVELS = (256, 224, 192, 160, 128)
_NUM_CHUNKS_LEVELS = (20000, 16000, 12000, 8000, 5000)
_STEPS_LEVELS = (50, 45, 40, 35, 28)

# Custo de VRAM do Conditioner (empírico, RTX 4050 5.6 GB, FP16/BF16).
#
# Com batch=3 partes: PyTorch alocou 5.29 GB, depois pediu +960 MB → OOM.
# Pico real por parte ≈ (5290 - 1900) / 3 ≈ 1130 MB de activações persistentes
# + buffers temporários do cross-attention que puxam mais ~800-960 MB no pico.
# Com batch=1 o pico é ≈ 1900 + 1130 + 960 ≈ 3990 MB → cabe em 5.6 GB.
#
# Para garantir que o cálculo de batch=1 funciona em ≤6 GB:
_COND_MB_PER_PART = 2200  # inclui buffers temporários pico (worst-case single part)
_CONDITIONER_WEIGHTS_MB = 1900
_SAFETY_MARGIN_MB = 1000  # fragmentação + DiT/VAE residual

# Custo VRAM do pipeline completo X-Part (Cond + DiT + VAE juntos na GPU)
# DiT FP16 ≈ 3600 MB; qint8 ≈ 1900 MB. Activação multi-parte no denoise ≈ 900 MB/parte.
_DIT_WEIGHTS_MB = 3600
_DIT_WEIGHTS_MB_QUANTIZED = 1900
_DIT_COMPILE_OVERHEAD_MB = 600  # Inductor / cudagraphs residual
_VAE_WEIGHTS_MB = 350
_COND_FEATURES_PER_PART_MB = 760
_DIT_ACT_PER_PART_MB = 900  # pico denoise multi-parte (subestimado antes → OOM)

# Abaixo deste total: sempre cond_batch=1 e max_parts DiT=1 (salvo casos largos).
_LOW_VRAM_GB = 7.5
_TIGHT_VRAM_GB = 6.5


@dataclass(frozen=True)
class SegmentAutotune:
    point_num: int
    prompt_num: int
    pressure_index: int
    geometry_score: float
    vram_tier: int


@dataclass(frozen=True)
class GenerateAutotune:
    octree_resolution: int
    num_chunks: int
    num_inference_steps: int
    surface_pc_size: int
    bbox_num_points: int
    cond_batch_size: int
    max_parts_allowed: int  # Limite de partes pela VRAM (0 = ilimitado)
    pressure_index: int
    num_parts: int
    geometry_score: float
    compile_dit: bool = True  # False → pipeline deve skip compile no DiT


def _mesh_surface_area(mesh: Any) -> float:
    """Compute total surface area from vertices/faces (triangulated meshes)."""
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces)
    if len(f) == 0 or len(v) == 0:
        return 0.0
    v0, v1, v2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(np.sum(0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)))


def mesh_geometry_score(mesh: Any) -> float:
    """Escalar ~0-4+ conforme complexidade (faces, vértices, extensão espacial)."""
    n_f = max(0, len(mesh.faces))
    n_v = max(0, len(mesh.vertices))
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    if n_v < 2:
        return 0.0
    ext_vec = np.max(verts, axis=0) - np.min(verts, axis=0)
    extent = float(np.max(ext_vec))
    extent = max(extent, 1e-8)
    area = _mesh_surface_area(mesh)
    # Log-scale para não dominar meshes enormes
    g_faces = float(np.log1p(n_f / 4000.0))
    g_verts = 0.5 * float(np.log1p(n_v / 4000.0))
    g_extent = 0.3 * float(np.log1p(extent * 10.0))
    g_area = 0.2 * float(np.log1p(area / 50.0)) if area > 0 else 0.0
    return g_faces + g_verts + g_extent + g_area


def _vram_tier_gb(vram_gb: float | None) -> int:
    """0 = muita VRAM; 4 = pouca. None (CPU/só estimativa) → conservador."""
    if vram_gb is None:
        return 3
    if vram_gb >= 18.0:
        return 0
    if vram_gb >= 12.0:
        return 1
    if vram_gb >= 9.0:
        return 2
    if vram_gb >= 6.5:
        return 3
    return 4


def _clamp_idx(base: int, *bumps: int) -> int:
    return int(np.clip(base + sum(bumps), 0, len(_OCTREE_LEVELS) - 1))


def get_vram_gb() -> float | None:
    """VRAM total da GPU 0 em GB, ou None se sem CUDA."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return float(torch.cuda.get_device_properties(0).total_memory) / (1024**3)
    except Exception:
        return None


def get_free_vram_gb() -> float | None:
    """VRAM livre na GPU 0 (após fragmentação), ou None se sem CUDA."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free_b, _total_b = torch.cuda.mem_get_info(0)
        return float(free_b) / (1024**3)
    except Exception:
        return None


def _budget_gb(*, vram_gb: float | None, free_vram_gb: float | None) -> float | None:
    """Orçamento efectivo: min(total, free*0.95+pesos_já_fora) — preferir free se apertado."""
    if vram_gb is None and free_vram_gb is None:
        return None
    if free_vram_gb is None:
        return vram_gb
    if vram_gb is None:
        return free_vram_gb
    # Free reflecte fragmentação; se free << total, planear com free + margem.
    if free_vram_gb < vram_gb * 0.55:
        return max(free_vram_gb, 1.0)
    return vram_gb


def autotune_segment(
    mesh: Any,
    *,
    vram_gb: float | None = None,
    estimated_num_parts: int | None = None,
) -> SegmentAutotune:
    """
    Parâmetros P3-SAM antes da segmentação.

    ``estimated_num_parts`` heurística se ainda não segmentámos (ex.: de faces).
    """
    if vram_gb is None:
        vram_gb = get_vram_gb()
    tier = _vram_tier_gb(vram_gb)
    g = mesh_geometry_score(mesh)

    # Heurística de partes antes de correr SAM: meshes densos tendem a mais regiões
    n_f = len(mesh.faces)
    if estimated_num_parts is None:
        est = 4 + int(np.log1p(n_f / 2500.0))
        estimated_num_parts = int(np.clip(est, 3, 48))

    geom_bump = 1 if g > 2.5 else (2 if g > 4.0 else 0)
    parts_bump = 0 if estimated_num_parts <= 8 else (1 if estimated_num_parts <= 16 else 2)

    idx = _clamp_idx(tier, geom_bump, parts_bump)

    return SegmentAutotune(
        point_num=_POINT_NUM_LEVELS[idx],
        prompt_num=_PROMPT_NUM_LEVELS[idx],
        pressure_index=idx,
        geometry_score=g,
        vram_tier=tier,
    )


def _compute_cond_batch_size(
    num_parts: int,
    vram_gb: float | None,
    *,
    free_vram_gb: float | None = None,
) -> int:
    """Quantas partes cabem num forward do Conditioner sem OOM.

    Cálculo: (vram_orçamento - pesos_conditioner - margem) / custo_por_parte.
    Em VRAM ≤7.5 GB força 1. Resultado clampado a [1, num_parts].
    """
    nparts = max(1, int(num_parts))
    budget = _budget_gb(vram_gb=vram_gb, free_vram_gb=free_vram_gb)
    if budget is None or budget <= 0:
        return 1
    # Empírico: ≤7.5 GB nunca cabe >1 parte no Conditioner sem risco.
    if (vram_gb is not None and vram_gb <= _LOW_VRAM_GB) or budget <= _LOW_VRAM_GB:
        return 1
    available_mb = budget * 1024 - _CONDITIONER_WEIGHTS_MB - _SAFETY_MARGIN_MB
    if available_mb <= 0:
        return 1
    bs = max(1, int(available_mb / _COND_MB_PER_PART))
    return min(bs, nparts)


def get_max_parts_for_vram(
    vram_gb: float | None,
    *,
    dit_quantized: bool = False,
    compile_active: bool = False,
    free_vram_gb: float | None = None,
) -> int | None:
    """Número máximo de partes no DiT por batch sem OOM.

    O DiT requer todas as condições do batch na GPU durante o denoising.
    Fórmula: VRAM >= DiT(+compile) + N*(cond_feat + dit_act) + margem.

    Returns:
        int: máximo de partes (≥1), ou None se VRAM desconhecida.
    """
    budget = _budget_gb(vram_gb=vram_gb, free_vram_gb=free_vram_gb)
    if budget is None or budget <= 0:
        return None

    # ≤6.5 GB: uma parte por batch — multi-parte denoise OOMa na 4050.
    if (vram_gb is not None and vram_gb <= _TIGHT_VRAM_GB) or budget <= _TIGHT_VRAM_GB:
        return 1

    dit_mb = _DIT_WEIGHTS_MB_QUANTIZED if dit_quantized else _DIT_WEIGHTS_MB
    if compile_active:
        dit_mb += _DIT_COMPILE_OVERHEAD_MB
    per_part = _COND_FEATURES_PER_PART_MB + _DIT_ACT_PER_PART_MB
    available = budget * 1024 - dit_mb - _SAFETY_MARGIN_MB
    if available <= 0:
        return 1
    n_max = int(available / per_part)
    # ≤7.5 GB: no máximo 2 mesmo se a fórmula for generosa.
    cap = 2 if (vram_gb is not None and vram_gb <= _LOW_VRAM_GB) else 16
    return max(1, min(n_max, cap))


def should_compile_dit(
    *,
    vram_gb: float | None,
    memory_efficient: bool = False,
    cpu_offload: bool = False,
    free_vram_gb: float | None = None,
) -> bool:
    """DiT + torch.compile em ≤8 GB com offload → pico/fragmentação → OOM.

    VAE compile continua seguro; DiT compile só com VRAM folgada e sem offload.
    """
    budget = _budget_gb(vram_gb=vram_gb, free_vram_gb=free_vram_gb)
    if budget is None:
        return not (memory_efficient or cpu_offload)
    if memory_efficient or cpu_offload:
        return budget >= 10.0
    return budget >= 8.0


def autotune_generate(
    mesh: Any,
    num_parts: int,
    *,
    vram_gb: float | None = None,
    dit_quantized: bool = False,
    memory_efficient: bool = False,
    compile_active: bool = False,
    cpu_offload: bool = False,
    free_vram_gb: float | None = None,
) -> GenerateAutotune:
    """
    Parâmetros X-Part depois de conhecer o número real de partes.

    Muitas partes → batch maior no Conditioner; reduzimos pontos e octree.
    O ``cond_batch_size`` controla quantas partes são codificadas de cada vez
    (chunked encoding) para evitar OOM na VRAM.
    """
    if not memory_efficient:
        dit_quantized = False
    if vram_gb is None:
        vram_gb = get_vram_gb()
    if free_vram_gb is None:
        free_vram_gb = get_free_vram_gb()
    tier = _vram_tier_gb(vram_gb)
    g = mesh_geometry_score(mesh)

    geom_bump = 1 if g > 2.8 else (2 if g > 4.5 else 0)
    nparts = max(1, int(num_parts))
    parts_bump = 0 if nparts <= 6 else (1 if nparts <= 12 else (2 if nparts <= 20 else 3))

    idx = _clamp_idx(tier, geom_bump, parts_bump)
    compile_dit = bool(compile_active) and should_compile_dit(
        vram_gb=vram_gb,
        memory_efficient=memory_efficient,
        cpu_offload=cpu_offload,
        free_vram_gb=free_vram_gb,
    )
    cbs = _compute_cond_batch_size(nparts, vram_gb, free_vram_gb=free_vram_gb)
    max_parts = get_max_parts_for_vram(
        vram_gb,
        dit_quantized=dit_quantized,
        compile_active=compile_dit,
        free_vram_gb=free_vram_gb,
    )

    # Para VRAM muito limitada sem DiT quantizado, usar menos steps (DiT pode ir a CPU)
    steps = _STEPS_LEVELS[idx]
    if vram_gb is not None and vram_gb < 8.0 and not dit_quantized:
        steps = min(steps, 20)  # Máximo 20 steps para CPU

    # ShapeVAE (latent2mesh fast path) exige octree >= 256; níveis mais baixos falham em runtime.
    octree = max(256, int(_OCTREE_LEVELS[idx]))
    return GenerateAutotune(
        octree_resolution=octree,
        num_chunks=_NUM_CHUNKS_LEVELS[idx],
        num_inference_steps=steps,
        surface_pc_size=_SURFACE_PC_SIZE,
        bbox_num_points=_BBOX_PC_SIZE,
        cond_batch_size=cbs,
        max_parts_allowed=max_parts if max_parts is not None else 0,
        pressure_index=idx,
        num_parts=nparts,
        geometry_score=g,
        compile_dit=compile_dit,
    )


def refresh_generate_limits(
    *,
    num_parts: int,
    vram_gb: float | None,
    dit_quantized: bool,
    compile_active: bool,
    cond_batch_size: int,
    max_parts_allowed: int,
) -> tuple[int, int]:
    """Reavalia cond_batch / max_parts com VRAM livre actual (entre batches)."""
    free = get_free_vram_gb()
    cbs = _compute_cond_batch_size(num_parts, vram_gb, free_vram_gb=free)
    cbs = min(max(1, cond_batch_size), cbs) if cond_batch_size > 0 else cbs
    mp = get_max_parts_for_vram(
        vram_gb,
        dit_quantized=dit_quantized,
        compile_active=compile_active,
        free_vram_gb=free,
    )
    if mp is None:
        mp = max(1, max_parts_allowed) if max_parts_allowed > 0 else max(1, num_parts)
    if max_parts_allowed > 0:
        mp = min(mp, max_parts_allowed)
    return max(1, cbs), max(1, mp)


def autotune_summary(seg: SegmentAutotune | None, gen: GenerateAutotune | None) -> dict[str, Any]:
    """Útil para logging / CLI."""
    out: dict[str, Any] = {}
    if seg is not None:
        out["segment"] = {
            "point_num": seg.point_num,
            "prompt_num": seg.prompt_num,
            "pressure_index": seg.pressure_index,
            "geometry_score": round(seg.geometry_score, 3),
            "vram_tier": seg.vram_tier,
        }
    if gen is not None:
        out["generate"] = {
            "octree_resolution": gen.octree_resolution,
            "num_chunks": gen.num_chunks,
            "num_inference_steps": gen.num_inference_steps,
            "surface_pc_size": gen.surface_pc_size,
            "bbox_num_points": gen.bbox_num_points,
            "cond_batch_size": gen.cond_batch_size,
            "max_parts_allowed": gen.max_parts_allowed,
            "compile_dit": gen.compile_dit,
            "pressure_index": gen.pressure_index,
            "num_parts": gen.num_parts,
            "geometry_score": round(gen.geometry_score, 3),
        }
    return out
