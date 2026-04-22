"""
Valores por defeito do Paint3D.

Constantes de configuração para Hunyuan3D-Paint 2.1 e upscale IA.

Defaults alinhados com o clone demo (6 views @ 512px, bake_exp=4).
Para GPUs com >=20 GB (A100): usar ``--max-views 8 --view-resolution 768``.
Para GPUs com menos VRAM, usar ``--low-vram-mode`` no CLI ou ``low_vram=True`` na API.

Configuracao por defeito (6 views @ 512px, single GPU 12GB):
- UNet: FP16 puro (sem quantizacao SDNQ/qint8)
- render_size=2048, texture_size=4096, max_views=6, view_resolution=512
- VAE: slicing + tiling
- Attention: NAO usar xformers — substituem os processors 5D customizados
  do ``UNet2p5DConditionModel``.
- torch.compile: desabilitado via TORCHDYNAMO_DISABLE=1

Modo low-VRAM (``--low-vram-mode``):
- UNet: SDNQ uint8 (pos-load) via gamedev_shared.sdnq
- render_size=1024, texture_size=2048, max_views=4, view_resolution=384

Qualidade de bake:
- bake_exp=4 (upstream): blending suave entre vistas.
- Upscale (Real-ESRGAN) desabilitado por defeito.
"""

from __future__ import annotations

DEFAULT_PAINT_HF_REPO = "tencent/Hunyuan3D-2.1"
DEFAULT_PAINT_SUBFOLDER = "hunyuan3d-paintpbr-v2-1"

DEFAULT_PAINT_RENDER_SIZE = 2048
DEFAULT_PAINT_TEXTURE_SIZE = 4096
DEFAULT_PAINT_CPU_OFFLOAD = False
DEFAULT_LOW_VRAM = False

LOW_VRAM_RENDER_SIZE = 1024
LOW_VRAM_TEXTURE_SIZE = 2048

DEFAULT_PAINT_MAX_VIEWS = 6
DEFAULT_PAINT_VIEW_RESOLUTION = 512

# Low-VRAM: 4 views @ 384px
LOW_VRAM_MAX_VIEWS = 4
LOW_VRAM_VIEW_RESOLUTION = 384

# bake_exp=4 (upstream): blending suave. Antes era 6 (transicoes mais nítidas).
DEFAULT_PAINT_BAKE_EXP = 4

DEFAULT_VAE_TILE_SIZE = 256
DEFAULT_ENABLE_VAE_SLICING = True
DEFAULT_ENABLE_VAE_TILING = True

DEFAULT_SMOOTH = True
DEFAULT_SMOOTH_PASSES = 6
DEFAULT_SMOOTH_DIAMETER = 11
DEFAULT_SMOOTH_SIGMA_COLOR = 75.0
DEFAULT_SMOOTH_SIGMA_SPACE = 50.0

DEFAULT_UPSCALE = False
DEFAULT_UPSCALE_FACTOR = 4

# GPU exclusive limit (MiB) — multi-GPU: checked only on device 0
DEFAULT_EXCLUSIVE_GPU_MAX_USED_MIB = 1536
LOW_VRAM_EXCLUSIVE_GPU_MAX_USED_MIB = 300
