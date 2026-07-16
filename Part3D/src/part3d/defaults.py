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
SPACE_MASK_NMS_IOU = 0.9
# Merge secundário: exige overlap de máscara + bbox. 0.0 funde demais (chapel v11
# → 4 partes); 0.25 preserva porta/moldura sem fragmentar.
SPACE_SECONDARY_MASK_IOU = 0.5
SPACE_MIN_CLUSTER_SUPPORT = 3
# 1.0 = desativado; <1.0 preserva clusters de 1-2 prompts com IoU alto (tende a
# fragmentar - chapel v6/v7 explodiu para 58-91 partes). Opt-in via CLI.
SPACE_MIN_PREDICTED_IOU = 1.0
SPACE_PROMPT_BATCH_SIZE = 4
# Kernel qualidade: multi-head pool + consensus NMS (custa CPU, não VRAM).
SPACE_MULTI_HEAD = True
SPACE_HEAD_MIN_SCORE = 0.5
SPACE_HEAD_SCORE_RATIO = 0.85
SPACE_CONSENSUS = True
SPACE_CONSENSUS_VOTE = 0.5

# Refinamento crease-aware das labels P3-SAM (pós-segmentação, CPU-only):
# ilhas pequenas absorvidas + fronteiras encaixadas em arestas vivas (ICM).
DEFAULT_REFINE_LABELS = True
DEFAULT_REFINE_ITERATIONS = 20
DEFAULT_REFINE_SMOOTH_ANGLE_DEG = 25.0
DEFAULT_REFINE_CONCAVE_FACTOR = 0.35
DEFAULT_REFINE_ISLAND_MIN_FRAC = 0.15
DEFAULT_REFINE_ISLAND_MIN_FACES = 12
DEFAULT_REFINE_DATA_WEIGHT = 0.35
DEFAULT_REFINE_BOUNDARY_HOPS = 2

# Passe local P3-SAM em labels grandes. Desligado no perfil normal; o preset
# --fine-parts e tiers altos podem ativá-lo sem aumentar o pico de VRAM.
DEFAULT_DETAIL_LEVELS = 0
DEFAULT_DETAIL_PARENT_MIN_AREA_FRAC = 0.18
DEFAULT_DETAIL_CHILD_MIN_AREA_FRAC = 0.01
DEFAULT_DETAIL_MAX_DOMINANT_FRAC = 0.95
DEFAULT_DETAIL_MAX_PARENTS = 2
DEFAULT_DETAIL_POINT_NUM = 32000
DEFAULT_DETAIL_PROMPT_NUM = 128

# Segmentação: p3sam (semântica upstream), geometry (regiões de superfície
# conectadas e delimitadas por creases), hybrid (P3-SAM + refine geométrico).
# Nenhum modo contém regras específicas de categoria de asset.
DEFAULT_SEGMENT_MODE = "p3sam"
DEFAULT_FINE_SEGMENT_MODE = "hybrid"

# Export de partes após segmentação:
#   xpart  — só Hunyuan X-Part (regen)
#   faces  — só face-split (topologia original)
#   hybrid — X-Part em partes compactas; paredes grandes + falhas MC → face-split
DEFAULT_PARTS_MODE = "xpart"
DEFAULT_FINE_PARTS_MODE = "hybrid"
# Acima disto: X-Part ainda corre, mas com octree reduzido (não desactiva a fase).
DEFAULT_XPART_MAX_AREA_FRAC = 0.10
DEFAULT_XPART_LARGE_OCTREE = 128
# Pad relativo das AABBs de geração X-Part (não altera labels de face).
# Caixas justas cortam o MC nas bordas → volumes abertos / AABB-limit.
DEFAULT_AABB_MARGIN_FRAC = 0.05
# Partes demasiado finas/alongadas: X-Part derrete (escadas, bandeiras, painéis).
# OFF por defeito — colar face topology + carve cria escada dupla / furos na
# parede quando a feature está colada ao volume. Opt-in via CLI.
DEFAULT_PRESERVE_THIN_TOPOLOGY = False
DEFAULT_XPART_SKIP_THIN_RATIO = 0.20  # min_extent/max_extent ≤ isto → skip
DEFAULT_XPART_SKIP_ASPECT = 5.0  # max/min ≥ isto → skip

# Face-split: fechar buracos de fronteira nas partes extraídas (bpy fill_holes).
# OFF por defeito: caps tapavam recessos legítimos (janelas/portas) com
# membranas facetadas; com o guard anti-NaN os cortes já ficam limpos.
DEFAULT_CAP_PART_HOLES = False

# Pós-X-Part: cada face fica na parte cuja superfície está mais perto.
# Mata sobra/overlap sem carve. OFF até haver selagem/voxel-MC — cortar
# faces abre sólidos watertight. Opt-in: --exclusive-partition.
DEFAULT_EXCLUSIVE_PARTITION = False
DEFAULT_EXCLUSIVE_SAMPLES_PER_PART = 4000

DEFAULT_DTYPE = "float16"
DEFAULT_CPU_OFFLOAD = False
DEFAULT_MEMORY_EFFICIENT = False
DEFAULT_HW_AUTO = True

DEFAULT_QUANTIZATION_MODE = "auto"
DEFAULT_QUANTIZE_DIT = False

DEFAULT_ENABLE_ATTENTION_SLICING = True
DEFAULT_TORCH_COMPILE = False
DEFAULT_TORCH_COMPILE_MODE = "default"
