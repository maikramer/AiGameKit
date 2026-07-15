"""
Valores por defeito do Part3D.

**Perfil padrão:** FP16 em alta VRAM (≥10 GB CUDA).
Todos os componentes permanecem na GPU; sem quantização.

Para GPUs com ~6 GB VRAM, usar memory-efficient mode que activa
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

# Volume decode: auto → hierarchical (qualidade) / flashvdm (VRAM baixa).
# ``fast`` = legacy Space latent2mesh_2; ``vanilla`` = dense latents2mesh.
DEFAULT_VOLUME_DECODER = "auto"
DEFAULT_CHANNELS_LAST = True

DEFAULT_POSTPROCESS = True
# 0.95 funde painéis pequenos (porta) na moldura; 0.99 preserva partes ~1%.
DEFAULT_POSTPROCESS_THRESHOLD = 0.99
# Cutoffs injectados no Space auto_mask (anti-fuse porta/moldura).
SPACE_PART_AREA_MERGE = 0.0025  # was 0.01 in do_post_process
SPACE_AREA_RATIO_KEEP = 0.00025  # was 0.001 in 去除面积过小
# Merge de clusters de máscaras por bbox-IoU no Space (0.5 upstream funde
# porta+moldura quando as bboxes se sobrepõem; mais alto = menos fusão).
SPACE_BBOX_MERGE_IOU = 0.7

# Refinamento crease-aware das labels P3-SAM (pós-segmentação, CPU-only):
# ilhas pequenas absorvidas + fronteiras encaixadas em arestas vivas (ICM).
DEFAULT_REFINE_LABELS = True
DEFAULT_REFINE_ITERATIONS = 20
DEFAULT_REFINE_SMOOTH_ANGLE_DEG = 25.0
DEFAULT_REFINE_CONCAVE_FACTOR = 0.35
DEFAULT_REFINE_ISLAND_MIN_FRAC = 0.15
DEFAULT_REFINE_ISLAND_MIN_FACES = 12

# Face-split: fechar buracos de fronteira nas partes extraídas (bpy fill_holes)
# para que remover uma parte (ex. porta) não deixe geometria aberta/degenerada.
DEFAULT_CAP_PART_HOLES = True

DEFAULT_DTYPE = "float16"
DEFAULT_CPU_OFFLOAD = False
DEFAULT_MEMORY_EFFICIENT = False
DEFAULT_HW_AUTO = True

DEFAULT_QUANTIZATION_MODE = "auto"
DEFAULT_QUANTIZE_DIT = False

DEFAULT_ENABLE_ATTENTION_SLICING = True
DEFAULT_TORCH_COMPILE = False
DEFAULT_TORCH_COMPILE_MODE = "default"
