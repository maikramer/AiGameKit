# Texture2D — Seamless 2.0 e qualidade (2026-09-11)

Estudo + implementação de melhorias de seamless-ness e qualidade do Texture2D
(SD1.5). A/B validado na RTX 4050 6 GB (seed 42, "seamless red brick wall
texture, mortar lines"), tudo via vramd exceto onde indicado.

## Descobertas raiz

### 1. A métrica de tileability media a coisa errada (bug)

A métrica antiga (`tileability.py`) comparava as primeiras N colunas com as
últimas N por **igualdade** (`MSE(strip_0, strip_{W-1})`). Tileability exige
**continuidade** na costura — a última coluna deve parecer a vizinha natural
da primeira, não ser-lhe igual. Consequência: qualquer textura de alta
frequência (tijolo com argamassa, grão, veios) pontuava ~0 mesmo perfeitamente
tileable — **incluindo todas as texturas do pool partilhado** (cobblestone,
desert_sand, dirt: 0.0 com a métrica antiga). A métrica nova (seam-continuity):

```
excess = max(0, seam_mse − adjacent_mse)      # costura vs gradiente interno
score  = 1 − clamp(excess / (3·adjacent_mse + 25))   # piso abs p/ texturas planas
```

- Discrimina: tijolo circular (late/full) PASS limpo; modo `off` (SD puro) e
  decode tiled FICAM <0.85 e são detetados.
- Ruído/grão fino pontuam alto (correto: costura estatisticamente contínua).
- Padrão periódico suave: ~0.90 (conservador por causa do piso absoluto).

### 2. O VAE tiling do diffusers parte a costura circular (P0)

O generator passava `target_resolution=1024` ao planner lowvram, o que ligava
`vae_tiling` em praticamente toda a run (o tiled decode engaja para imagens
>256 px). O `tiled_decode` do diffusers fatura o latent **sem wrap** e junta
tiles com blend linear — a costura exacta que o circular constrói no latent
perde-se no espaço-píxel. Confirmação A/B: `full` (decode integral) PASS limpo;
`full_tiled` (--vae-tiling) FICA abaixo de 0.85 e precisa do heal.

Correcção: o pipeline corre sempre com `output_type="latent"` e o decode é da
tool — **integral** sempre que a resolução/VRAM permite (≤768 sempre; 1024 se
VRAM livre ≥3 GiB; fallback OOM); quando o tiling é inevitável, o latent é
**circular-padded** (8 latents) antes do tiling e o resultado recortado
(wrap-preserving). O planner recebe `target_resolution=512` (nativa).

### 3. Circular constante degrada qualidade (pattern-diffusion)

Receita Apache-2.0 do [pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion):
circular em todas as convs degrada FID/CLIP de forma mensurável; a combinação
**noise rolling** (`torch.roll` dos latents por metade a cada step) durante os
primeiros ~80% + **circular só na fase final** não mede degradação. Nova flag
`--seamless-mode late|full|off` (default `late`); com `torch.compile` ativo
crai para `full` (o grafo assa o padding_mode). Nota: o roll tem paridade —
rolls ímpares deixam o latent meia-volta; o switch repõe a orientação.

### 4. Direct >512² duplica conteúdo; hires-fix resolve

SD1.5 é treinado a 512²; gerar directo a 768/1024 produz duplicações. Novo
caminho: gerar à nativa (max dim 512, aspect preservado) → latent upscale
bilinear → **refine com strength 0.55** (`add_noise` no timestep correspondente
+ loop CFG manual — atenção: `add_noise` espera timestep 1-D, 0-d tensor lança
"iteration over a 0-d tensor"). Tiers `high` (768²) e `highest` (1024²) usam
isto automaticamente com `refine_steps` 12/16 do QualityEngine.

### 5. Extras

- **VAE ft-mse** default (`stabilityai/sd-vae-ft-mse`; `TEXTURE2D_VAE_ID=none`
  desliga) — melhor decode; A/B: base 0.9973 vs ft-mse 1.0 (ambos PASS limpo).
- **Score no sidecar** de cada geração (`tileability` no JSON) + **auto-heal**
  (`--seam-heal`, default on): cross-fade simétrico da banda de borda (a última
  coluna passa a ser igual à primeira) quando score < 0.85.
- Negative base ganha "visible seam, tiling artifacts".
- GameAssets: wave texture2d agora resolve via QualityEngine (defaults
  28/7.0 alinhados com o tier medium, era 20/7.5 sem tier); token morto
  `smlstxtr` removido dos hints; `regen_textures.py` guidance 3.5→7.0.

## A/B (RTX 4050 6 GB, seed 42, brick wall)

| Run | Config | Resultado |
|-----|--------|-----------|
| `late` | default novo (roll + circular tardio + decode integral + ft-mse) | **1.0 limpo** (sem heal) |
| `full` | circular do início, decode integral | **1.0 limpo** |
| `full_tiled` | circular + `--vae-tiling` | <0.85 → heal → PASS (confirma P0) |
| `off` | SD1.5 puro | <0.85 → heal → PASS (baseline não-seamless detetado) |
| `basevae` | late + VAE do checkpoint (in-process) | 0.9973 limpo |
| `hires1024` | 512 + refine 16 @1024 (tier highest; decode tiled auto) | <0.85 → heal → PASS |
| `direct1024` | 1024 directo (`--no-hires`) | <0.85 → heal → PASS |

Nota: os runs 1024 usaram decode tiled auto (VRAM livre <3 GiB na 6 GB com
worker GO residente); em GPUs maiores decodem integral.

## Métrica antiga vs pool (baseline histórica)

Texturas do pool VibeGame (código antigo, métrica antiga = igualdade):
asphalt 0.63, cobblestone_road/desert_sand/dirt 0.0 — a métrica antiga não
distinguía "costura real" de "alta frequência do material". Com a métrica de
continuidade estas passam a pontuar conforme a costura real.

## Suítes

Texture2D 294 ✓ (métrica nova, modos, decode policy, hires, heal, payload,
CLI) · Shared 1313 ✓ (tiers high/highest com `refine_steps`) · GameAssets
774 ✓ (wave quality + hints).

## Futuro (não feito)

- Sampling estilo Tiled Diffusion (CVPR 2025) para SD1.5 (contexto toroidal
  por janelas) — upgrade maior do modo late.
- ESRGAN x2 circular-padded como alternativa ao latent refine (o core está no
  Paint3D, acoplado ao bpy).
- TexTile (classificador aprendido) como métrica pesada opcional de CI.
