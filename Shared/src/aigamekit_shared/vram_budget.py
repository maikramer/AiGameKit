"""Orçamento dinâmico de activação GPU (chunks / views / tiles).

Separação de camadas:

* **UMS admit** (:mod:`modelserver.vram_planner`) — pico estático pesos+act+safety
  *antes* de aceitar o job.
* **Runtime budget** (este módulo) — *depois* dos pesos/offload, dimensiona o
  batch de activação pela VRAM **livre** (``mem_get_info``). Evita OOM → CPU.

Text3D usa isto para ``num_chunks`` do decode; Paint3D para views/tiles/DINO;
qualquer backend UMS pode chamar a mesma fórmula.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# Fração da VRAM livre reservada a activação (resto: fragmentação / picos).
DEFAULT_VRAM_FRACTION = 0.7

# Text3D decode: custo aproximado por query do geo-decoder (cross-attn).
TEXT3D_BYTES_PER_QUERY = 96 * 1024
TEXT3D_CHUNKS_LO = 8_192
TEXT3D_CHUNKS_HI = 524_288
TEXT3D_BYTES_PER_QUERY_ENV = "TEXT3D_DECODE_BYTES_PER_QUERY"

# Paint3D: activação multiview UNet (por vista @ 512px, SDNQ/fp16 mix medido
# na 6 GB: ~6 vistas cabem com ~1.5-2 GiB livres após pesos+offload).
PAINT_BYTES_PER_VIEW_512 = 280 * 1024 * 1024
PAINT_VIEWS_LO = 2
PAINT_VIEWS_HI = 10
# DINO ViT-g precisa ~1.5 GiB livres para ficar na GPU sem empurrar o UNet.
PAINT_DINO_MIN_FREE_BYTES = int(1.6 * 1024**3)
# MeshRender (cudaMalloc fora do PyTorch) precisa de margem após pesos.
# Sem isto, dual-UNet residente deixa ~34 MiB livres e OOM no load_mesh.
PAINT_MESHRENDER_MIN_FREE_BYTES = 256 * 1024 * 1024


def budget_units(
    free_vram_bytes: int | None,
    bytes_per_unit: int,
    *,
    fraction: float = DEFAULT_VRAM_FRACTION,
    lo: int,
    hi: int,
) -> int | None:
    """Quantas unidades de activação cabem na VRAM livre.

    Args:
        free_vram_bytes: Bytes livres (``torch.cuda.mem_get_info()[0]``).
        bytes_per_unit: Custo estimado por unidade (query, vista, tile, …).
        fraction: Fração da VRAM livre utilizável.
        lo / hi: ``lo`` é piso *soft* (preferência de qualidade) — só aplica
            quando o orçamento cabe ≥ ``lo``. Nunca inflaciona além do que a
            VRAM livre permite (evita OOM ao forçar chunks/views mínimos).
            ``hi`` é tecto duro.

    Returns:
        Inteiro em ``[1, hi]`` (possivelmente ``< lo`` se VRAM apertada),
        ou ``None`` sem sinal de VRAM / nada cabe.
    """
    if free_vram_bytes is None or free_vram_bytes <= 0:
        return None
    if bytes_per_unit <= 0:
        raise ValueError("bytes_per_unit deve ser > 0")
    n = int(free_vram_bytes * float(fraction) / float(bytes_per_unit))
    if n < 1:
        return None
    hi_i = int(hi)
    lo_i = int(lo)
    capped = min(hi_i, n)
    # Soft floor: só forçar ``lo`` quando o budget já o comporta.
    if capped >= lo_i:
        return max(lo_i, capped)
    return capped


def free_vram_bytes(device: int | None = None) -> int | None:
    """VRAM livre em bytes via CUDA; ``None`` sem GPU / erro."""
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    try:
        if device is not None:
            with torch.cuda.device(int(device)):
                free_b, _total = torch.cuda.mem_get_info()
        else:
            free_b, _total = torch.cuda.mem_get_info()
        return int(free_b)
    except Exception:
        return None


def text3d_bytes_per_query() -> int:
    """Custo por query do decode (env ``TEXT3D_DECODE_BYTES_PER_QUERY``)."""
    raw = os.environ.get(TEXT3D_BYTES_PER_QUERY_ENV, "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return TEXT3D_BYTES_PER_QUERY


def text3d_num_chunks(
    free_vram_bytes: int | None,
    *,
    bytes_per_query: int | None = None,
    fraction: float = DEFAULT_VRAM_FRACTION,
    lo: int = TEXT3D_CHUNKS_LO,
    hi: int = TEXT3D_CHUNKS_HI,
) -> int | None:
    """Batch de queries do decode Omni (alias canónico partilhado)."""
    bpq = bytes_per_query if bytes_per_query and bytes_per_query > 0 else text3d_bytes_per_query()
    return budget_units(free_vram_bytes, bpq, fraction=fraction, lo=lo, hi=hi)


def paint_bytes_per_view(view_resolution: int) -> int:
    """Custo estimado de activação por vista multiview (escala com res²)."""
    r = max(256, int(view_resolution))
    return int(PAINT_BYTES_PER_VIEW_512 * (r / 512.0) ** 2)


def paint_esrgan_tile(free_vram_bytes: int | None, *, memory_efficient: bool) -> int:
    """Tile Real-ESRGAN: mais pequeno quando a VRAM livre aperta."""
    if free_vram_bytes is None:
        return 256 if memory_efficient else 512
    gib = free_vram_bytes / (1024**3)
    if gib < 1.0:
        return 128
    if gib < 2.0 or memory_efficient:
        return 256
    return 512


def paint_vae_tile(free_vram_bytes: int | None, *, default: int = 256) -> int:
    """Tile VAE: reduz sob pressão de VRAM."""
    if free_vram_bytes is None:
        return int(default)
    gib = free_vram_bytes / (1024**3)
    if gib < 1.2:
        return 128
    return int(default)


@dataclass(frozen=True)
class PaintRuntimeBudget:
    """Knobs de activação Paint3D após load/offload."""

    max_views: int
    view_resolution: int
    vae_tile_size: int
    esrgan_tile: int
    dino_device: str
    cfg_batch_chunking: bool
    offload_ref_unet: bool
    free_vram_bytes: int | None
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_views": self.max_views,
            "view_resolution": self.view_resolution,
            "vae_tile_size": self.vae_tile_size,
            "esrgan_tile": self.esrgan_tile,
            "dino_device": self.dino_device,
            "cfg_batch_chunking": self.cfg_batch_chunking,
            "offload_ref_unet": self.offload_ref_unet,
            "free_vram_bytes": self.free_vram_bytes,
            "notes": list(self.notes),
        }


def paint_runtime_budget(
    free_vram_bytes: int | None,
    *,
    requested_views: int,
    requested_resolution: int,
    memory_efficient: bool = False,
    force_dino_device: str | None = None,
) -> PaintRuntimeBudget:
    """Orça views/tiles/DINO pela VRAM livre (pós-load).

    Reduz ``max_views`` se necessário para caber; mantém resolução pedida
    (hw-auto já a escolheu). DINO fica em CUDA só com margem livre suficiente
    — evita empurrar o UNet para CPU por falta de headroom.
    """
    notes: list[str] = []
    res = max(256, int(requested_resolution))
    # Respeitar pedido (mesmo 1); não subir 1→PAINT_VIEWS_LO.
    want = max(1, int(requested_views))
    bpv = paint_bytes_per_view(res)
    capped = budget_units(
        free_vram_bytes,
        bpv,
        fraction=DEFAULT_VRAM_FRACTION,
        lo=min(PAINT_VIEWS_LO, want),
        hi=min(PAINT_VIEWS_HI, want),
    )
    if capped is None:
        # Sem sinal: mem_eff falha fechado (views mínimas); senão pedido.
        if memory_efficient:
            views = min(want, PAINT_VIEWS_LO)
            notes.append(f"sem sinal VRAM — views={views} (mem_eff fail-closed)")
        else:
            views = want
            notes.append("sem sinal VRAM — views pedidas")
    else:
        views = min(want, capped)
        if views < want:
            notes.append(f"views {want}->{views} (free act budget)")

    # Em cards apertadas força CFG chunking + ref offload mesmo se o tier
    # hw-auto não marcou memory_efficient (flutuação pós-load).
    tight = free_vram_bytes is not None and free_vram_bytes < 2.5 * 1024**3
    cfg = bool(memory_efficient or tight)
    ref_off = bool(memory_efficient or tight)
    if tight and not memory_efficient:
        notes.append("VRAM livre apertada — cfg_chunk+ref_offload ON")

    # memory_efficient: DINO SEMPRE em CPU. Com ~3 GiB livres pós-pesos a
    # heurística antiga (free≥1.6 GiB e não-tight) puxava DINO→CUDA (~2 GiB)
    # e OOMava a placa (MeshRender ficava com ~10-34 MiB).
    if force_dino_device:
        dino = force_dino_device
    elif memory_efficient or tight:
        dino = "cpu"
        if memory_efficient:
            notes.append("DINO->cpu (memory_efficient)")
        elif free_vram_bytes is not None:
            notes.append("DINO->cpu (headroom)")
    elif free_vram_bytes is not None and free_vram_bytes >= PAINT_DINO_MIN_FREE_BYTES:
        dino = "cuda"
    else:
        dino = "cpu"
        if free_vram_bytes is not None:
            notes.append("DINO->cpu (headroom)")

    return PaintRuntimeBudget(
        max_views=views,
        view_resolution=res,
        vae_tile_size=paint_vae_tile(free_vram_bytes),
        esrgan_tile=paint_esrgan_tile(free_vram_bytes, memory_efficient=memory_efficient or tight),
        dino_device=dino,
        cfg_batch_chunking=cfg,
        offload_ref_unet=ref_off,
        free_vram_bytes=free_vram_bytes,
        notes=tuple(notes),
    )
