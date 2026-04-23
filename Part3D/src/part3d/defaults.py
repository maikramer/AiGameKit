"""
Valores por defeito do Part3D.

**Perfil padrão:** FP16 em alta VRAM (≥10 GB CUDA).
Todos os componentes permanecem na GPU; sem quantização.

Para GPUs com ~6 GB VRAM, usar ``--low-vram-mode`` que activa
CPU offloading sequencial + quantização automática.

Tamanhos dos pesos (FP16):
  model (DiT):     ~3.3 GB
  conditioner:     ~880 MB
  shapevae:        ~328 MB
  p3sam:           ~225 MB
  Total:           ~4.75 GB (pico ~5.2 GB durante denoising)
"""

from __future__ import annotations

DEFAULT_HF_REPO = "tencent/Hunyuan3D-Part"

DEFAULT_NUM_INFERENCE_STEPS = 30
DEFAULT_GUIDANCE_SCALE = -1.0

DEFAULT_OCTREE_RESOLUTION = 256
DEFAULT_NUM_CHUNKS = 20000
DEFAULT_MC_LEVEL = -1 / 512
DEFAULT_MC_ALGO = "mc"

DEFAULT_POSTPROCESS = True
DEFAULT_POSTPROCESS_THRESHOLD = 0.95

DEFAULT_DTYPE = "float16"
DEFAULT_CPU_OFFLOAD = False
DEFAULT_LOW_VRAM_MODE = False

DEFAULT_QUANTIZATION_MODE = "auto"
DEFAULT_QUANTIZE_DIT = False

DEFAULT_ENABLE_ATTENTION_SLICING = True
DEFAULT_TORCH_COMPILE = False
