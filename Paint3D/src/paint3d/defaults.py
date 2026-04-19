"""
Valores por defeito do Paint3D.

Constantes de configuração para Hunyuan3D-Paint 2.1 e upscale IA.

Defaults orientados a GPUs com >=8 GB VRAM (alta precisao, sem quantizacao).
Para GPUs com menos VRAM, usar ``--low-vram-mode`` no CLI ou ``low_vram=True`` na API.

Configuracao por defeito (alta VRAM):
- UNet: FP16 puro (sem quantizacao SDNQ/qint8)
- render_size=2048, texture_size=4096, cpu_offload=False
- VAE: slicing + tiling
- Attention: NAO usar xformers nem attention_slicing - substituem os processors
  5D customizados do ``UNet2p5DConditionModel``.
- Dtype: float16
- torch.compile: nao testado com UNet2p5D customizado; desligado por defeito

Modo low-VRAM (``--low-vram-mode``):
- UNet: SDNQ uint8 (pos-load, dequantize_fp32=False) via gamedev_shared.sdnq
- Se SDNQ indisponivel: qint8 pre-quantizado do upstream (optimum-quanto)
- render_size=1024, texture_size=2048, cpu_offload=True

Qualidade de bake:
- bake_exp=6 (era 4): transicoes mais nítidas entre vistas, menos sangramento.
- Upscale (Real-ESRGAN) desabilitado por defeito: pode ser ligado com --upscale.
  Roda em CPU, sem custo de VRAM, mas nem sempre melhora a qualidade percebida.
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

DEFAULT_PAINT_MAX_VIEWS = 4
DEFAULT_PAINT_VIEW_RESOLUTION = 512

DEFAULT_PAINT_BAKE_EXP = 6

DEFAULT_VAE_TILE_SIZE = 256
DEFAULT_ENABLE_VAE_SLICING = True
DEFAULT_ENABLE_VAE_TILING = True

DEFAULT_SMOOTH = True
DEFAULT_SMOOTH_PASSES = 2
DEFAULT_SMOOTH_DIAMETER = 9
DEFAULT_SMOOTH_SIGMA_COLOR = 50.0
DEFAULT_SMOOTH_SIGMA_SPACE = 50.0

DEFAULT_UPSCALE = False
DEFAULT_UPSCALE_FACTOR = 4
