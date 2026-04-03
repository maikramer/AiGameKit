# Guia de Otimização para RTX 4050 6GB

Este guia documenta as configurações validadas para executar as ferramentas GameDev
em GPUs com 6GB VRAM, especialmente a NVIDIA RTX 4050 Laptop (Ada Lovelace, CC 8.9).

## Índice
1. [Configuração Validada](#configuração-validada)
2. [Paint3D — Otimizações](#paint3d--otimizações)
3. [Text3D — Otimizações](#text3d--otimizações)
4. [Troubleshooting](#troubleshooting)

---

## Configuração Validada

### Hardware
- **GPU**: NVIDIA RTX 4050 Laptop, 6141 MiB VRAM
- **Compute Capability**: 8.9 (Ada Lovelace)
- **BF16**: Suportado

### Configuração CUDA (obrigatória para 6GB)

```bash
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,max_split_size_mb:64,garbage_collection_threshold:0.6"
```

---

## Paint3D — Otimizações

### Configuração validada (testada e estável)

```bash
python -m paint3d texture mesh.glb imagem.jpg --rtx4050-mode
```

O modo RTX 4050 aplica automaticamente:

| Parâmetro | Valor | Nota |
|-----------|-------|------|
| Quantização | SDNQ uint8 (pós-load) | Aplicado via `gamedev_shared.sdnq` |
| dtype | float16 | Pipeline carrega em FP16; SDNQ mantém FP16 |
| xformers | **OFF** | Quebra attention processors 5D customizados |
| attention_slicing | **OFF** | Quebra attention processors 5D customizados |
| tiny_vae | **OFF** | `AutoencoderTinyOutput` não tem `.latent_dist` |
| VAE slicing | ON | |
| VAE tiling | ON (tile_size=128) | |
| torch.compile | OFF | |
| CPU offload | ON | |

### Resultados dos testes

| Configuração | VRAM Pico | Output | Status |
|--------------|-----------|--------|--------|
| SDNQ uint8 + FP16 + VAE tiling | ~5.5GB | 1.9 MB GLB | **OK** |
| Sem otimizações (baseline) | OOM | — | Falha |
| int4 + BF16 + xformers + tiny_vae | Erro | — | Quebra pipeline |

### O que NÃO funciona em Paint3D

Estas otimizações foram testadas e **causam erros** no pipeline Hunyuan3D-Paint:

1. **xformers** — Substitui os attention processors customizados 5D
   (`SelfAttnProcessor2_0`, `RefAttnProcessor2_0`, `PoseRoPEAttnProcessor2_0`)
   que o `UNet2p5DConditionModel` usa para multiview PBR.
   Erro: `"too many values to unpack (expected 3)"`

2. **attention_slicing** — Mesmo problema que xformers.
   Substitui os processors customizados por `SlicedAttnProcessor`.

3. **TinyVAE (TAESD)** — `AutoencoderTinyOutput` usa `.latents` em vez
   de `.latent_dist`. O pipeline vendido chama `.latent_dist` em
   `pipeline.py:158` e `unet/model.py:231`.
   Erro: `'AutoencoderTinyOutput' object has no attribute 'latent_dist'`

4. **SDNQ uint8 com `dequantize_fp32=True`** — Causa `CUBLAS_STATUS_NOT_SUPPORTED`
   no kernel `_int_mm` porque int8→FP32 dequantization conflita com o pipeline FP16.
   Solução: usar `dequantize_fp32=False` (dequantiza para FP16).

5. **NF4/int4 via bitsandbytes** — Não aplicável ao UNet2p5D (não é um modelo HuggingFace padrão).

---

## Text3D — Otimizações

### Resultados dos testes

| Preset | Steps | Octree | VRAM | Output | Tempo |
|--------|-------|--------|------|--------|-------|
| balanced | 24 | 256 | ~5.5GB | 2.9 MB | ~3-4 min |
| hq | 30 | 384 | ~5.5GB | 1.0 MB | ~5.9 min |

Ambos os presets cabem em 6GB sem OOM. O preset `hq` faz auto-retry quando
necessário (máximo 2 retries para backing plates).

```bash
# Preset balanced (padrão)
python -m text3d generate "descrição do objeto"

# Preset high quality
python -m text3d generate "descrição do objeto" --preset hq
```

---

## Ferramentas que já funcionam bem (sem mudanças)

- **Text2D**: SDNQ 4-bit já integrado e otimizado
- **Part3D**: Funciona em 6GB sem otimizações extras
- **Text2Sound**: Funciona em 6GB sem otimizações extras

---

## Troubleshooting

### Erro: CUDA Out of Memory

```bash
# 1. Verificar CUDA allocator
echo $PYTORCH_CUDA_ALLOC_CONF
# Deve conter: expandable_segments:True,max_split_size_mb:64,garbage_collection_threshold:0.6

# 2. Fechar outros programas que usam GPU (navegador, Discord, etc.)

# 3. Reduzir view resolution no Paint3D
python -m paint3d texture mesh.glb imagem.jpg --rtx4050-mode --view-resolution 256
```

### Erro: "too many values to unpack (expected 3)"

Causa: xformers ou attention_slicing ativados no Paint3D.
Solução: desabilitar ambos.

```bash
python -m paint3d texture mesh.glb imagem.jpg --rtx4050-mode
# O modo RTX4050 já desabilita xformers e attention_slicing automaticamente.
```

### Erro: "'AutoencoderTinyOutput' has no attribute 'latent_dist'"

Causa: TinyVAE ativado no Paint3D.
Solução: não usar `--tiny-vae`.

### Erro: CUBLAS_STATUS_NOT_SUPPORTED

Causa: SDNQ com `dequantize_fp32=True` em pipeline FP16.
Solução: o código já usa `dequantize_fp32=False` por padrão.

### Monitorar VRAM em tempo real

```bash
watch -n 0.5 nvidia-smi
```
