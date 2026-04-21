"""
Valores por defeito do Text3D.

**Perfil padrão:** qualidade elevada (model card HF), pensado para GPUs com >= 8 GB VRAM.
Octree 384, 20000 chunks, 30 steps Hunyuan, sem quantização SDNQ, remesh desligado,
reparo "light" (merge+weld+normals).

Para hardware modesto (~6 GB VRAM), usar ``--low-vram`` que activa o perfil antigo:
SDNQ INT4, octree 256, 8000 chunks, 24 steps, remesh ligado, reparo "full".
"""

from __future__ import annotations

import math
import os

# --- Orientação ao gravar mesh (Hunyuan3D → motor Y-up) ---
_rotation_x_rad_override: float | None = None


def set_export_rotation_x_rad_override(value: float | None) -> None:
    """Usado pelo CLI ``generate``; None = voltar a env/defeito."""
    global _rotation_x_rad_override
    _rotation_x_rad_override = value


def get_export_rotation_x_rad() -> float:
    """Rotação X Hunyuan → espaço de export (Y-up)."""
    if _rotation_x_rad_override is not None:
        return float(_rotation_x_rad_override)
    env = os.environ.get("TEXT3D_EXPORT_ROTATION_X_RAD")
    if env is not None and str(env).strip() != "":
        return float(env)
    env_deg = os.environ.get("TEXT3D_EXPORT_ROTATION_X_DEG")
    if env_deg is not None and str(env_deg).strip() != "":
        return float(env_deg) * math.pi / 180.0
    return 0.0


# --- Origem ao gravar mesh (após rotação Y-up) ---
DEFAULT_EXPORT_ORIGIN = "feet"

_origin_override: str | None = None

_VALID_ORIGINS = frozenset({"feet", "center", "none"})


def set_export_origin_override(value: str | None) -> None:
    """Usado pelo CLI ``generate``; None = voltar a env/defeito."""
    global _origin_override
    if value is not None and value not in _VALID_ORIGINS:
        raise ValueError(f"export origin inválido: {value!r}")
    _origin_override = value


def get_export_origin() -> str:
    """Modo de origem após rotação: ``feet`` | ``center`` | ``none``."""
    if _origin_override is not None:
        return _origin_override
    env = os.environ.get("TEXT3D_EXPORT_ORIGIN", "").strip().lower()
    if env in _VALID_ORIGINS:
        return env
    return DEFAULT_EXPORT_ORIGIN


# --- Text2D (imagem intermédia) ---
DEFAULT_T2D_WIDTH = 768
DEFAULT_T2D_HEIGHT = 768

DEFAULT_T2D_STEPS = 8
DEFAULT_T2D_GUIDANCE = 1.0

DEFAULT_T2D_CPU_OFFLOAD = True

# --- Hunyuan3D-2.1 (shape) — perfil padrão (qualidade elevada) ---
DEFAULT_SUBFOLDER = "hunyuan3d-dit-v2-1"

DEFAULT_HY_STEPS = 30
DEFAULT_HY_GUIDANCE = 5.0
DEFAULT_OCTREE_RESOLUTION = 384
DEFAULT_NUM_CHUNKS = 20000

DEFAULT_MC_LEVEL = 0.0

DEFAULT_REMOVE_BG = True
DEFAULT_MAX_FACES = 40000

# --- Perfil "low VRAM" (~6 GB): activado com ``--low-vram`` ---
LOW_VRAM_OCTREE = 256
LOW_VRAM_NUM_CHUNKS = 8000
LOW_VRAM_STEPS = 24

# Perfis CLI `--preset`: fast=baixo VRAM, balanced=~6GB, hq=padrão actual.
PRESET_HUNYUAN = {
    "fast": {"steps": 18, "octree": 128, "chunks": 4096},
    "balanced": {
        "steps": LOW_VRAM_STEPS,
        "octree": LOW_VRAM_OCTREE,
        "chunks": LOW_VRAM_NUM_CHUNKS,
    },
    "hq": {"steps": DEFAULT_HY_STEPS, "octree": DEFAULT_OCTREE_RESOLUTION, "chunks": DEFAULT_NUM_CHUNKS},
}
