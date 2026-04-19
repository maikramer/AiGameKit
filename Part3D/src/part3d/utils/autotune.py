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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh

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
_COND_MB_PER_PART = 2100  # inclui buffers temporários pico (worst-case single part)
_CONDITIONER_WEIGHTS_MB = 1900
_SAFETY_MARGIN_MB = 800  # margem grande para ativações intermediárias do DiT + margem de segurança

# Custo VRAM do pipeline completo X-Part (Cond + DiT + VAE juntos na GPU)
# Valores medidos empiricamente:
# DiT FP16 ≈ 3600 MB (3.3 GB pesos + overhead PyTorch/ativações)
# VAE ≈ 350 MB
# Custo por parte inclui: features do conditioner (~360 MB) + ativações do DiT (~400 MB)
_DIT_WEIGHTS_MB = 3600  # DiT FP16 medido (inclui pesos + overhead)
_DIT_WEIGHTS_MB_QUANTIZED = 1900  # DiT qint8 weight-only (estimativa conservadora + overhead)
_VAE_WEIGHTS_MB = 350
_COND_FEATURES_PER_PART_MB = 760  # features + ativações por parte


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


def mesh_geometry_score(mesh: trimesh.Trimesh) -> float:
    """Escalar ~0-4+ conforme complexidade (faces, vértices, extensão espacial)."""
    n_f = max(0, len(mesh.faces))
    n_v = max(0, len(mesh.vertices))
    verts = mesh.vertices.astype(np.float64)
    if n_v < 2:
        return 0.0
    ext_vec = np.max(verts, axis=0) - np.min(verts, axis=0)
    extent = float(np.max(ext_vec))
    extent = max(extent, 1e-8)
    area = float(mesh.area) if hasattr(mesh, "area") and mesh.area > 0 else 0.0
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


def autotune_segment(
    mesh: trimesh.Trimesh,
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


def _compute_cond_batch_size(num_parts: int, vram_gb: float | None) -> int:
    """Quantas partes cabem num forward do Conditioner sem OOM.

    Cálculo: (vram_total - pesos_conditioner - margem) / custo_por_parte.
    Resultado é clampado a [1, num_parts].
    """
    if vram_gb is None or vram_gb <= 0:
        return 1
    available_mb = vram_gb * 1024 - _CONDITIONER_WEIGHTS_MB - _SAFETY_MARGIN_MB
    if available_mb <= 0:
        return 1
    bs = max(1, int(available_mb / _COND_MB_PER_PART))
    return min(bs, num_parts)


def get_max_parts_for_vram(vram_gb: float | None, *, dit_quantized: bool = False) -> int | None:
    """Número máximo de partes que cabem no pipeline X-Part COMPLETO.

    O DiT requer todas as condições simultaneamente na GPU durante o denoising.
    Fórmula: VRAM >= DiT + VAE + sum(Cond por parte) + margem.

    Returns:
        int: máximo de partes (≥1), ou None se VRAM desconhecida.
    """
    if vram_gb is None or vram_gb <= 0:
        return None
    # DiT + VAE + N * cond + margem <= vram
    # N <= (vram - DiT - VAE - margem) / cond_por_parte
    dit_mb = _DIT_WEIGHTS_MB_QUANTIZED if dit_quantized else _DIT_WEIGHTS_MB
    available = vram_gb * 1024 - dit_mb - _VAE_WEIGHTS_MB - _SAFETY_MARGIN_MB
    if available <= 0:
        return 1  # Não cabe nenhuma, mas tentamos 1 mesmo assim
    n_max = int(available / _COND_FEATURES_PER_PART_MB)
    return max(1, min(n_max, 16))  # Cap a 16 para evitar timeouts extremos


def autotune_generate(
    mesh: trimesh.Trimesh,
    num_parts: int,
    *,
    vram_gb: float | None = None,
    dit_quantized: bool = False,
    low_vram: bool = False,
) -> GenerateAutotune:
    """
    Parâmetros X-Part depois de conhecer o número real de partes.

    Muitas partes → batch maior no Conditioner; reduzimos pontos e octree.
    O ``cond_batch_size`` controla quantas partes são codificadas de cada vez
    (chunked encoding) para evitar OOM na VRAM.
    """
    if not low_vram:
        dit_quantized = False
    if vram_gb is None:
        vram_gb = get_vram_gb()
    tier = _vram_tier_gb(vram_gb)
    g = mesh_geometry_score(mesh)

    geom_bump = 1 if g > 2.8 else (2 if g > 4.5 else 0)
    nparts = max(1, int(num_parts))
    parts_bump = 0 if nparts <= 6 else (1 if nparts <= 12 else (2 if nparts <= 20 else 3))

    idx = _clamp_idx(tier, geom_bump, parts_bump)
    cbs = _compute_cond_batch_size(nparts, vram_gb)
    max_parts = get_max_parts_for_vram(vram_gb, dit_quantized=dit_quantized)

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
    )


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
            "pressure_index": gen.pressure_index,
            "num_parts": gen.num_parts,
            "geometry_score": round(gen.geometry_score, 3),
        }
    return out
