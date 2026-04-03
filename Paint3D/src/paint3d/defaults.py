"""
Valores por defeito do Paint3D.

Constantes de configuração para Hunyuan3D-Paint 2.1 e upscale IA.

Projeto focado em GPUs com 6GB VRAM (RTX 4050 Laptop, Ada Lovelace, CC 8.9).

Configuração validada:
- UNet: SDNQ uint8 (pós-load, dequantize_fp32=False) via gamedev_shared.sdnq
- Se SDNQ indisponível: qint8 pré-quantizado do upstream (optimum-quanto)
- VAE: slicing + tiling — não usar TAESD (``AutoencoderTinyOutput`` não tem ``.latent_dist``)
- Attention: NÃO usar xformers nem attention_slicing — substituem os processors
  5D customizados (``SelfAttnProcessor2_0``, ``RefAttnProcessor2_0``,
  ``PoseRoPEAttnProcessor2_0``) do ``UNet2p5DConditionModel``.
- Dtype: float16 (pipeline carrega em FP16; SDNQ mantém FP16)
- torch.compile: não testado com UNet2p5D customizado; desligado por defeito
"""

from __future__ import annotations

DEFAULT_PAINT_HF_REPO = "tencent/Hunyuan3D-2.1"
DEFAULT_PAINT_SUBFOLDER = "hunyuan3d-paintpbr-v2-1"
DEFAULT_PAINT_CPU_OFFLOAD = True
DEFAULT_PAINT_MAX_VIEWS = 4
DEFAULT_PAINT_VIEW_RESOLUTION = 512

DEFAULT_VAE_TILE_SIZE = 256
DEFAULT_ENABLE_VAE_SLICING = True
DEFAULT_ENABLE_VAE_TILING = True

DEFAULT_UPSCALE = False
DEFAULT_UPSCALE_FACTOR = 4
