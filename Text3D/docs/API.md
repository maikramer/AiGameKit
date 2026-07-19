# API Python — Text3D

## HunyuanTextTo3DGenerator

Fluxo: **Text2D** (texto → imagem) → `unload` → **Hunyuan3DDiTFlowMatchingPipeline** (imagem → `trimesh.Trimesh`).

### Importação

```python
from text3d import HunyuanTextTo3DGenerator, defaults
from text3d.utils import save_mesh
```

**Textura e PBR no GLB** não fazem parte deste pacote: usa o projeto **[Paint3D](../../Paint3D)** (`paint3d texture` — saída PBR com Hunyuan3D-Paint 2.1). Ver [Paint3D/README.md](../../Paint3D/README.md), [Paint3D/docs/PAINT_SETUP.md](../../Paint3D/docs/PAINT_SETUP.md) e [PBR_MATERIALIZE.md](PBR_MATERIALIZE.md) (GLB vs textura difusa).

Padrões de qualidade/memória: módulo `text3d.defaults` (perfil ~6GB; `HUNYUAN_HQ_*` para GPU grande). O dicionário `defaults.PRESET_HUNYUAN` espelha o CLI `--preset fast|balanced|hq`.

### Inicialização

```python
gen = HunyuanTextTo3DGenerator(
    device=None,              # "cuda" | "cpu" | auto
    verbose=False,
    cache_dir=None,
    hunyuan_model_id="tencent/Hunyuan3D-Omni",
)
```

### `generate(prompt, ...)`

Text-to-3D completo.

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `t2d_width`, `t2d_height` | `defaults.DEFAULT_T2D_*` (768) | Text2D; 1024 em GPU grande |
| `t2d_steps` | `defaults.DEFAULT_T2D_STEPS` (8) | Passos Text2D (8 steps melhora aderência ao prompt de iluminação) |
| `t2d_guidance` | `defaults.DEFAULT_T2D_GUIDANCE` (1.0) | Guidance Text2D (SDNQ ~1.0) |
| `text2d_model_id` | None | Override HF Text2D |
| `t2d_seed` | None | Seed Text2D |
| `num_inference_steps` | `defaults.DEFAULT_HY_STEPS` (24) | Passos Hunyuan; HQ: `HUNYUAN_HQ_STEPS` |
| `guidance_scale` | `defaults.DEFAULT_HY_GUIDANCE` (5.0) | Guidance Hunyuan |
| `octree_resolution` | `defaults.DEFAULT_OCTREE_RESOLUTION` (128) | HQ: `HUNYUAN_HQ_OCTREE` |
| `num_chunks` | `defaults.DEFAULT_NUM_CHUNKS` (4096) | HQ: `HUNYUAN_HQ_NUM_CHUNKS` |
| `hy_seed` | None | Seed Hunyuan |
| `mc_level` | `defaults.DEFAULT_MC_LEVEL` (0.0) | Nível marching cubes |
| `t2d_full_gpu` | False | Se True, FLUX inteiro na GPU (só em máquinas com muita VRAM) |
| `return_reference_image` | False | Se True, devolve `(mesh, pil_image)` para usar depois com **Paint3D** |

Por defeito ``t2d_full_gpu=False`` combina com ``defaults.DEFAULT_T2D_CPU_OFFLOAD`` (offload no Text2D em CUDA).

**Retorno:** `trimesh.Trimesh`, ou `(trimesh.Trimesh, PIL.Image)` se `return_reference_image=True` (malha bruta; o CLI aplica `repair_mesh` antes de gravar).

### Textura e PBR (Paint3D)

Depois de `generate` ou `generate_from_image`, grava o GLB e chama o CLI ou a API do pacote **Paint3D**:

```bash
paint3d texture mesh.glb -i ref.png -o mesh_tex.glb
```

O ficheiro de saída inclui material PBR do pipeline 2.1. Para **mapas PBR a partir de uma imagem difusa**, usa o CLI **[Materialize](../../Materialize)** (não o fluxo GLB).

**Pré-requisitos do Paint:** código Hunyuan3D-2.1 (`hy3dpaint`), rasterizador GPU, Real-ESRGAN — ver [Paint3D/docs/PAINT_SETUP.md](../../Paint3D/docs/PAINT_SETUP.md).

### Pós-processo de malha (`repair_mesh`)

Em código Python, após `generate` / `generate_from_image`, podes alinhar ao CLI:

```python
from text3d.utils import repair_mesh, save_mesh

mesh = gen.generate("prompt")
mesh = repair_mesh(mesh, smooth_iterations=0)  # ou 1–2 para suavizar
save_mesh(mesh, "out.glb")
```

`repair_mesh` faz merge de vértices, mantém a **maior componente conexa** (remove ilhas pequenas) e, se `smooth_iterations > 0`, suavização Laplaciana. Isto **não** fecha buracos nem “cola” pés ao chão: geometria fina e desconexões vêm sobretudo do modelo e dos parâmetros Hunyuan (`octree_resolution`, `num_chunks`, `num_inference_steps`).

### `generate_from_image(image, ...)`

Apenas Hunyuan (caminho, `Path` ou `PIL.Image`).

### Context manager

```python
with HunyuanTextTo3DGenerator() as gen:
    mesh = gen.generate("prompt")
```

## Autotune do decode (bounds / mc_level / chunks)

Fórmulas puras em [`src/text3d/decode_tune.py`](../src/text3d/decode_tune.py);
o generator resolve-as no momento do decode:

| Knob | Default | Comportamento |
|------|---------|---------------|
| `bounds_mode` | `auto` | Com controlo bbox, o grid MC encolhe ao aspecto da bbox (`bounds_for_bbox`) — voxels mais finos no eixo fino (anti-buracos em portas/espadas/edifícios). `cube` = cubo clássico ±1.01. |
| `mc_level` | `auto` | Iso-nível ligeiro negativo ∝ 1/octree (`auto_mc_level`) — fecha pinholes MC; equivale ao upstream −1/512 em octree 512. Número = valor fixo. |
| `auto_num_chunks` | `True` | Batch de queries do decode calculado da VRAM livre (`mem_get_info` × 0.7 / bytes-por-query); clamp 8k–512k. Env: `TEXT3D_DECODE_BYTES_PER_QUERY`. |
| decoder | — | Vanilla/hierarchical com octree ≥ 448 é trocado por flashvdm (surface-focused): o field interior é ruído e o grid denso visita-o todo. |

Pós-decode, componentes fechados totalmente contidos na shell principal com
volume < 15% são removidos (`utils.mesh_metrics.drop_internal_components`) —
é o «octree alto gera geometria aleatória dentro da mesh».

**Tectos de octree** (`bbox_tune`): `octree = min(alvo, tecto VRAM, tecto
informativo do latent)`. O tecto informativo (`LATENT_DETAIL_CEILING`, default
448; env `TEXT3D_LATENT_OCTREE_CEILING`) existe porque acima da capacidade do
latent o sampling só resolve ruído. Calibra-o com:

```bash
text3d bench-decode --image ref.png --octrees 256,320,384,448,512 \
    --decoders vanilla,flashvdm --bbox-preset building -o bench_out/
# bench_report.json → summary.recommended_latent_ceiling
```

**Alvo percetual** (`bbox_tune.target_voxel_for`): o voxel-alvo escala com a
distância mínima de inspeção da categoria (arma 0.5 m → voxel fino; árvore
2.5 m → grosso) e com o tier `--quality`. O decode com prefetch (CUDA streams)
sobrepõe as transferências CPU→GPU dos chunks com o compute do geo-decoder.

## Exportação

### `save_mesh(mesh, path, format=..., rotate=True)`

Aceita **`trimesh.Trimesh`** (Hunyuan) ou **array numpy** (legado diffusers).

```python
save_mesh(mesh, "out.glb", format="glb")
```

### `convert_mesh`, `get_mesh_info`

Ver [`src/text3d/utils/export.py`](../src/text3d/utils/export.py).

## Memória

`text3d.utils.memory`: `get_system_info`, `get_gpu_info`, `format_bytes`.

## Referências

- [Hunyuan3D-Omni](https://huggingface.co/tencent/Hunyuan3D-Omni) (image + controlos geométricos)
- [Código Hunyuan3D-Omni](https://github.com/Tencent-Hunyuan/Hunyuan3D-Omni)
