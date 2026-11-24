"""Enriquecimento PBR: normal + AO derivados do albedo baked via Materialize.

O Hunyuan-Paint 2.1 só gera branches ``albedo`` + ``mr`` — o bake não produz
normal, occlusion nem emissive (normal/position maps são condicionamento do
modelo, não output). Este módulo delega a ``aigamekit_shared.pbr_uv`` para:

- preencher a gutter UV por Voronoi antes do Materialize (evita arestas falsas
  e halos escuros nas bordas das ilhas do xatlas);
- derivar ``height``, ``normal`` (OpenGL ``+Y`` verificado) e ``ao`` multi-escala;
- atenuar suavemente normal→``(128,128,255)`` e AO→``255`` junto às costuras UV
  e dilatar por Voronoi na gutter para mipmapping limpo.

Skip gracioso por design: binário ausente, GPU sem adapter wgpu ou falha do
processo → warning e ``None`` — o asset sai albedo+MR como antes, o paint
nunca quebra por causa do enriquecimento.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from aigamekit_shared.pbr_uv import (
    calibrate_matte_roughness,
    derive_uv_masked_pbr,
    infer_pbr_preset,
    rasterize_uv_coverage,
    voronoi_fill,
)
from aigamekit_shared.pbr_uv import (
    resolve_materialize_bin as _shared_resolve_materialize_bin,
)

__all__ = [
    "calibrate_matte_roughness",
    "enrich_maps_from_albedo",
    "get_pbr_mr_clamp",
    "infer_pbr_preset",
    "pbr_enrich_enabled",
    "rasterize_uv_coverage",
    "resolve_materialize_bin",
    "sanitize_pbr_mr",
    "set_pbr_enrich_override",
    "set_pbr_mr_clamp",
    "voronoi_fill",
]

_TIMEOUT_S = 600

# Override por-request (worker vramd): o ``--no-pbr-enrich`` do CLI vive no
# processo que faz o pedido; o worker é outro processo e herdava o default ON.
# ``None`` → cai para o env (CLI interactiva).
_ENABLED_OVERRIDE: bool | None = None
_ROUGHNESS_MIN_OVERRIDE: float | None = None
_METALLIC_MAX_OVERRIDE: float | None = None


def set_pbr_enrich_override(enabled: bool | None) -> None:
    """Liga/desliga o enriquecimento para o próximo job do worker."""
    global _ENABLED_OVERRIDE
    _ENABLED_OVERRIDE = None if enabled is None else bool(enabled)


def set_pbr_mr_clamp(roughness_min: float | None = None, metallic_max: float | None = None) -> None:
    """Define piso de roughness / tecto de metallic para estilos mates (ex. guache/têmpera)."""
    global _ROUGHNESS_MIN_OVERRIDE, _METALLIC_MAX_OVERRIDE
    _ROUGHNESS_MIN_OVERRIDE = None if roughness_min is None else float(roughness_min)
    _METALLIC_MAX_OVERRIDE = None if metallic_max is None else float(metallic_max)


def get_pbr_mr_clamp() -> tuple[float | None, float | None]:
    """Devolve ``(roughness_min, metallic_max)`` efectivos (override > env > None)."""
    rmin = _ROUGHNESS_MIN_OVERRIDE
    if rmin is None:
        raw = os.environ.get("PAINT3D_ROUGHNESS_MIN", "").strip()
        if raw:
            rmin = float(raw)
    mmax = _METALLIC_MAX_OVERRIDE
    if mmax is None:
        raw = os.environ.get("PAINT3D_METALLIC_MAX", "").strip()
        if raw:
            mmax = float(raw)
    return rmin, mmax


def sanitize_pbr_mr(
    albedo_u8: np.ndarray,
    roughness_u8: np.ndarray,
    metallic_u8: np.ndarray,
    *,
    roughness_min: float | None = None,
    metallic_max: float | None = None,
    protect_skin: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Protege pele contra brilho plástico/metálico e aplica limites de estilo mate no MR.

    O Hunyuan3D-Paint 2.1 alucina frequentemente ``roughness ≈ 0.20-0.35`` em pele
    (braços/mãos parecem plástico molhado) e ``metallic > 0.5`` em rostos/cabelo.
    1. Com ``protect_skin=True``, uma máscara contínua suave de tons de pele puxa
       a pele para dielétrico mate (``roughness >= 0.76``, ``metallic <= 0.04``).
    2. Com ``roughness_min`` / ``metallic_max`` (ex. ``0.72`` / ``0.15`` para guache),
       remapeia o piso de rugosidade preservando contraste relativo e limita picos
       metálicos alucinados em toda a malha.
    """
    r = roughness_u8.astype(np.float32) / 255.0
    m = metallic_u8.astype(np.float32) / 255.0

    if protect_skin and albedo_u8 is not None and albedo_u8.shape[:2] == r.shape[:2]:
        af = albedo_u8.astype(np.float32) / 255.0
        red, grn, blu = af[..., 0], af[..., 1], af[..., 2]
        # Máscara suave de tons quentes de pele (exclui ouro saturado e branco de fundo)
        w_lum = np.clip((red - 0.35) / 0.15, 0.0, 1.0) * np.clip((0.95 - blu) / 0.12, 0.0, 1.0)
        w_rg = np.clip((red - grn - 0.04) / 0.08, 0.0, 1.0)
        w_gb = np.clip((grn - blu - 0.02) / 0.06, 0.0, 1.0)
        w_not_gold = 1.0 - np.clip(((grn / np.maximum(red, 1e-4)) - 0.84) / 0.08, 0.0, 1.0)
        skin_w = np.clip(w_lum * w_rg * w_gb * w_not_gold, 0.0, 1.0)
        skin_r_target = np.clip(0.76 + (r - 0.20) * (0.16 / 0.80), 0.76, 0.94)
        r = r * (1.0 - skin_w) + np.maximum(r, skin_r_target) * skin_w
        m = m * (1.0 - skin_w) + np.minimum(m, 0.04) * skin_w

    if roughness_min is not None:
        rmin = float(np.clip(roughness_min, 0.0, 0.95))
        r_ceil = max(rmin + 0.02, 0.94)
        r_lifted = rmin + np.clip((r - 0.15) / 0.85, 0.0, 1.0) * (r_ceil - rmin)
        r = np.maximum(r, np.clip(r_lifted, rmin, r_ceil))

    if metallic_max is not None:
        mmax = float(np.clip(metallic_max, 0.0, 1.0))
        m = np.minimum(m, mmax)

    r_out = np.clip(np.round(r * 255.0), 0, 255).astype(np.uint8)
    m_out = np.clip(np.round(m * 255.0), 0, 255).astype(np.uint8)
    return r_out, m_out


def pbr_enrich_enabled() -> bool:
    """``PAINT3D_PBR_ENRICH`` — default ON; ``0`` desliga o enriquecimento."""
    if _ENABLED_OVERRIDE is not None:
        return _ENABLED_OVERRIDE
    return os.environ.get("PAINT3D_PBR_ENRICH", "").strip() != "0"


def resolve_materialize_bin() -> str | None:
    """Resolve ``materialize-cli`` compatível (``--only``/``--no-seamless``)."""
    return _shared_resolve_materialize_bin()


def enrich_maps_from_albedo(
    albedo: np.ndarray,
    *,
    preset: str | None = None,
    coverage_u8: np.ndarray | None = None,
    mesh_hint: str | Path | None = None,
    logger: Any = None,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Deriva ``(normal, ao)`` OpenGL Y-up livres de costuras UV via Materialize.

    Args:
        albedo: ``(H, W, 3)`` uint8 RGB.
        preset: Preset do Materialize (``default``, ``stone``, ``wood``, ``auto``, …);
            env ``PAINT3D_PBR_PRESET`` sobrepõe; ``auto`` infere a partir de ``mesh_hint``.
        coverage_u8: Máscara binária ``(H, W)`` uint8 das ilhas UV (``255`` dentro,
            ``0`` na gutter). Elimina arestas falsas nas costuras do atlas.
        mesh_hint: Nome/caminho do mesh para inferência de preset quando ``preset="auto"``.
        logger: Logger opcional (avisos de skip).

    Returns:
        ``(normal, ao)`` como ``(H, W, 3)`` uint8 RGB na resolução do albedo,
        ou ``None`` quando desligado/indisponível.
    """
    if not pbr_enrich_enabled():
        return None
    resolved_preset = os.environ.get("PAINT3D_PBR_PRESET", "").strip() or (preset or "default")
    if resolved_preset == "auto":
        resolved_preset = infer_pbr_preset(mesh_hint or "")

    materialize_bin = resolve_materialize_bin()
    if materialize_bin is None:
        if logger is not None:
            logger.warning(
                "PBR enrich: binário materialize-cli não encontrado "
                "(MATERIALIZE_BIN / Materialize/target/release / PATH) — asset sai sem normal/AO"
            )
        return None

    # Encaminha subprocess.run quando monkeypatched nos testes unitários.
    import aigamekit_shared.pbr_uv as _pbr_uv

    orig_run = _pbr_uv.subprocess.run
    if subprocess.run is not orig_run:
        _pbr_uv.subprocess.run = subprocess.run
    try:
        return derive_uv_masked_pbr(
            albedo,
            coverage_u8=coverage_u8,
            preset=resolved_preset,
            materialize_bin=materialize_bin,
            logger=logger,
        )
    finally:
        _pbr_uv.subprocess.run = orig_run
