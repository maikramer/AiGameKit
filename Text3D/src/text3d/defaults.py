"""
Valores por defeito do Text3D.

**Perfil padrão (validado):** combinação estável em ~6 GB VRAM (CUDA) com boa qualidade
na prática (text-to-3D: robô, veículo, planta, etc.). O pico de VRAM costuma ser no
*volume decoding* do Hunyuan; estes valores evitam OOM nessa fase.

Para hardware mais capaz, usa as constantes *HQ* ou flags CLI maiores
(--octree-resolution, --num-chunks, --steps).
"""

from __future__ import annotations

import math
import os

# --- Orientação ao gravar mesh (Hunyuan3D → motor Y-up) ---
# Rotação em torno do eixo X (radianos). O pipeline hy3dgen devolve malha numa convenção
# onde -90° em X fazia o modelo sair de cabeça para baixo no Godot; +90° alinha com Y+.
# Sobrescrever: TEXT3D_EXPORT_ROTATION_X_RAD ou TEXT3D_EXPORT_ROTATION_X_DEG, ou
# ``text3d generate --export-rotation-x-deg``.
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
    return math.pi / 2


# --- Origem ao gravar mesh (após rotação Y-up) ---
# Godot/Blender: personagens costumam ter origem entre os pés, Y=0 no chão, X/Z centrados.
# ``feet`` = base da AABB em Y=0 e centro em XZ. ``center`` = centro da caixa em (0,0,0).
# ``none`` = não transladar (útil para depuração ou viewers que já centram).
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
# 1024² puxa muita VRAM no FLUX; 768 é um compromisso estável em ~6GB.
DEFAULT_T2D_WIDTH = 768
DEFAULT_T2D_HEIGHT = 768

# 8 steps com prompt enhancement v2 produz imagens mais limpas de sombra/iluminação.
# O modelo SDNQ ignora guidance (step-wise distilled), mas 8 steps dá melhor aderência.
DEFAULT_T2D_STEPS = 8
DEFAULT_T2D_GUIDANCE = 1.0

# FLUX.2 Klein 4B não cabe em ~5-6GB com pipe.to(cuda); usar enable_model_cpu_offload.
# Desliga com t2d_full_gpu=True (CLI --t2d-full-gpu) em GPUs grandes.
DEFAULT_T2D_CPU_OFFLOAD = True

# --- Hunyuan3D-2.1 (shape) — mesmo perfil que o CLI usa por defeito ---
DEFAULT_SUBFOLDER = "hunyuan3d-dit-v2-1"

DEFAULT_HY_STEPS = 24
DEFAULT_HY_GUIDANCE = 5.0
# octree_resolution controla a grelha do volume (grid (N+1)³) onde o marching cubes extrai
# a superfície. Valores baixos (128) geram triângulos degenerados em zonas de curvatura
# alta (cabeça, dedos). 256 produz triângulos ~4x mais uniformes sem impacto significativo
# em VRAM (~65 MB vs ~8 MB para o grid; o gargalo real é o volume decoding em chunks).
# O default do Hunyuan3D upstream é 384; usamos 256 como compromisso para ~6GB VRAM.
DEFAULT_OCTREE_RESOLUTION = 256
DEFAULT_NUM_CHUNKS = 8000

# Pós-processo ao gravar mesh (CLI): 0 = só maior componente + merge; 1-2 suaviza superfície.
DEFAULT_MESH_SMOOTH = 0

# Isotropic remeshing (pymeshlab): reconstrói topologia com triângulos uniformes,
# fecha buracos do marching cubes e elimina faces degeneradas. Padrão: ligado.
DEFAULT_REMESH = True
DEFAULT_REMESH_RESOLUTION = 150

# --- Referência "alta qualidade" (model card HF / GPU com bastante VRAM) ---
# Ex.: --octree-resolution 384 --num-chunks 20000 --steps 30
HUNYUAN_HQ_OCTREE = 384
HUNYUAN_HQ_NUM_CHUNKS = 20000
HUNYUAN_HQ_STEPS = 30

# Marching cubes (Hunyuan): 0 = defeito do pipeline; valores pequenos podem alterar superfície.
DEFAULT_MC_LEVEL = 0.0

# Perfis CLI `--preset`: substituem steps + octree + num_chunks de uma vez.
# fast: qualidade razoável, menos VRAM/tempo; balanced: boa qualidade ~6GB; hq: model card HF.
PRESET_HUNYUAN = {
    "fast": {"steps": 18, "octree": 128, "chunks": 4096},
    "balanced": {
        "steps": DEFAULT_HY_STEPS,
        "octree": DEFAULT_OCTREE_RESOLUTION,
        "chunks": DEFAULT_NUM_CHUNKS,
    },
    "hq": {"steps": HUNYUAN_HQ_STEPS, "octree": HUNYUAN_HQ_OCTREE, "chunks": HUNYUAN_HQ_NUM_CHUNKS},
}
