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

## A/B visual — o score não viu a corrupção (2026-09-11, corrigido)

Inspeção visual das imagens do A/B acima: **`late`, `basevae`, `hires1024` e
`direct1024` estavam visualmente destruídos** (cores invertidas/saturadas,
bandas horizontais, ghost/double-exposure, estrutura em papa); só `full` estava
bom. A métrica de continuidade deu scores altos a todos — ela mede a costura da
**borda**, e a corrupção do solver é global (a borda continua wrap-consistente).
**Lição: A/B de seamless precisa sempre de inspeção visual; o score só guarda
a costura.**

Causa raiz (uma só, explicava os 4): o **noise rolling é incompatível com o
`DPMSolverMultistepScheduler`**. O multistep extrapola cada step com predições
anteriores cacheadas (`model_outputs`); rodar os latents por metade a cada step
(e trocar o padding do UNet a 80%) deixa a história do solver desalinhada da
orientação corrente — o solver mistura versões rodadas do mesmo sinal. É por
isso que o pattern-diffusion usa **DDPM** (stepper stateless). O `full` nunca
perturba a trajectória → DPM karras ficava correto.

Fixes (mesma data):

1. **Modo `late` → `DDIMScheduler`** (stateless por step, determinístico com
   eta=0): `_select_scheduler` em `generator.py` troca por-request a partir da
   config original do checkpoint; `full`/`off` restauram o DPM karras
   (instâncias cacheadas). Metadata ganhou `scheduler`.
2. **`basevae` em fp16 → decode fp32**: o VAE original do SD1.5 tem o overflow
   clássico do decoder em fp16 (cores invertidas/saturadas — razão histórica do
   ft-mse). Sem o swap ft-mse (`TEXTURE2D_VAE_ID=none`, ou fallback de rede),
   `_decode_latents` faz upcast fp32 do VAE+latent e repõe fp16 no fim. Com
   ft-mse (default) nada muda — é estável em fp16.

Suspeita descartada: o `_tiled_circular` (decode tiled com pad circular) — os
runs 1024 corrompiam pelo modo late, não pelo tiling; re-testado após o fix do
scheduler.

## Métrica antiga vs pool (baseline histórica)

Texturas do pool antigo (código antigo, métrica antiga = igualdade):
asphalt 0.63, cobblestone_road/desert_sand/dirt 0.0 — a métrica antiga não
distinguía "costura real" de "alta frequência do material". Com a métrica de
continuidade estas passam a pontuar conforme a costura real.

## Suítes

Texture2D 294 ✓ (métrica nova, modos, decode policy, hires, heal, payload,
CLI) · Shared 1313 ✓ (tiers high/highest com `refine_steps`) · GameAssets
774 ✓ (wave quality + hints).

## Precisão do pedido (2026-11-23)

Encontrado a gerar as texturas de madeira das cercas do Vael. Medido com o
tokenizador real do SD1.5 (`CLIPTokenizer`, local).

### 1. O prompt trunca a 77 tokens sem aviso

Um prompt de utilizador corrente (78 tokens) com `--preset wood` (15 tokens à
cabeça) dava 92: o modelo nunca viu os últimos ~17 — «flat top-down view,
texture fills the whole frame, stylized game texture». O negativo era pior
(106 tokens): a base, o preset e o utilizador repetiam «blurry, low quality»
e «frame, border, text, watermark», e perdiam 29 tokens da cauda.

Correcção (`prompt_budget.py`): se o texto clássico couber, fica igual (um
asset de antes reproduz-se com a mesma seed); se não couber, o utilizador vem
inteiro e o preset só empresta as cláusulas que cabem; no negativo saem as
repetições e fica a prioridade base > utilizador > preset. O sidecar passa a
trazer `prompt_tokens`, `negative_prompt_tokens`, `preset_dropped`,
`negative_dropped` e `prompt_user_truncated`.

### 2. O preset pisava os parâmetros explícitos

`generate()` lia `preset_params["num_inference_steps"]` incondicionalmente:
`--quality high -s 28` corria a 50 passos e guidance 7,5 (o preset Wood).
Agora o CLI declara o que o utilizador escolheu (`explicit`, incluindo um
tier escolhido a sério) e o preset só preenche o resto; sem nada declarado o
preset ganha, como antes. As chaves viajam no payload do vramd e no adapter.

### 3. A costura boa não basta: o pormenor isolado repete-se

Um nó com halo passa o score de tileability (0,96) e repete-se à mesma
posição em cada tile. Métrica nova, `score_isolated_detail`: passa-bando
(blur 6 px − blur 64 px, fronteira circular) agregado numa vizinhança de
10 px; `spot` = máximo − percentil 99. Medido em 1024²:

| textura | máx − p99 |
| --- | --- |
| 17 texturas aceites do pool | 0,014 – 0,103 |
| nó sintético (centro, costura, canto, sem halo, 8 px) | 0,169 – 0,299 |
| carvalho «aceite» só pela costura | 0,130 |

Duas armadilhas de calibração: a referência no percentil 99,9 caía **dentro**
de um nó com halo (ele ocupa > 0,1% da imagem) e o valor descia em vez de
subir; e um blur sem fronteira circular penalizava a vizinhança da costura.
Limiar recomendado para madeira: 0,12 (`--max-isolated-detail`, com re-roll
`seed + 1` até `--attempts`). Cerca de 0,4 s por 1024².

Suite: Texture2D 350 ✓ (307 anteriores + 43 de precisão), sem GPU.

### 4. O decode em tiles deixa manchas — e a folga era mal medida

A porta do pormenor isolado reprovava seed atrás de seed no carvalho
(6/6 ≥ 0,19) e o crivo a 512 passava em 12/12 (0,01–0,04). O «nó» não vinha
da seed, do prompt nem do refine: era o **decode do VAE em tiles** a 1024².
Mesma seed, mesmo prompt (RTX 4050, 2026-11-23):

| configuração | destaque | tileabilidade |
| --- | --- | --- |
| base 512² | 0,027 | 0,859 |
| hires, refine 55%, decode em tiles | 0,408 | 0,978 |
| hires, refine 45% / 35% / 25%, decode em tiles | 0,403 / 0,396 / 0,399 | — |
| hires, refine 55%, decode **integral** | **0,076** | 1,000 |

A mancha é a attention do bloco médio do VAE a ver uma janela por tile: os
tiles discordam e a zona de mistura fica com uma borra que parece um nó. O
tiling era escolhido porque `torch.cuda.mem_get_info()` dizia haver menos de
3 GB livres — mas o **cache reservado do PyTorch conta como ocupado para o
driver**. Esvaziando o cache antes de medir, a GPU de 6 GB escolhe o decode
integral e ele cabe sem OOM (o caminho de OOM → tiles continua lá para quem
tem mesmo pouco). A lição para o portão: quando uma métrica de qualidade
reprova sempre, isole o estágio antes de mexer no prompt.

### 5. O SD1.5 tem um prior de tom médio: a paleta nunca sai do prompt

A passar os nove conjuntos de madeira para os portões acima, a cor média de
**todas** as imagens ficou num intervalo estreito, qualquer que fosse o prompt
(1024², 2026-11-23):

| conjunto | pedido (ART.md §4) | o que saiu |
| --- | --- | --- |
| `wood_pine_dark` | Pinho Negro `#232B24` | `#7F6A5C`, lum. 109 |
| `wood_swamp_tar` | Alcatrão `#3D352E` | `#7A5D3F`, lum. 97 |
| `wood_lime_washed` | Cal `#E6DEC8` | `#937E66`, lum. 128 |
| `wood_sun_bleached` | Madeira Sol `#C2B39B` | `#816D59`, lum. 112 |
| `wood_weathered_grey` | Barro `#4D4237` | `#6E5F53`, lum. 98 |
| `wood_oak_hewn` | Carvalho `#7A5C3A` | `#795C45`, lum. 96 |

Nove prompts, de quase preto a marfim: castanhos a 96–128 de 255. Só o carvalho
acertou, porque o alvo era o prior. Nenhum prompt corrige isto — é a
distribuição do modelo.

Correcção: `grading.match_mean_color` (`--match-color #RRGGBB`). Afim por
canal com **uma escala comum**: a média vai ao alvo e o contraste só comprime
pelo necessário para o miolo (percentis 1–99) caber em 0–255. A escala comum
preserva a cor das variações (o veio quente continua quente sobre um alvo
escuro); escalas por canal mudavam os tons. Num alvo escuro com contraste
moderado a escala fica 1 — só a média muda. O sidecar traz `match_color`
(`before`, `after`, `scale`). A estrutura não muda: tábuas e flocos continuam
lá depois da graduação, e é para isso que servem os portões seguintes.

### 6. O eixo troca sozinho; frestas de tábuas passam todos os portões

O pormenor isolado e a costura não vêem duas falhas do "wood texture":

- **tábuas com frestas** — o prompt pedia uma superfície contínua e o modelo
  desenhou tábuas horizontais com juntas escuras;
- **veio deitado** — o prompt pedia «vertical grain running top to bottom» e
  as linhas saíram da esquerda para a direita.

Métrica nova, `score_axis_banding`: a média **com sinal** de cada linha (e de
cada coluna) do passa-bando, em unidades do percentil 99 do módulo. O sinal
importa: uma linha só tem média grande se a estrutura for coerente ao longo
dela toda — uma fresta ou um veio atravessam-na. Medido nas nove imagens:

| textura | linhas | colunas |
| --- | --- | --- |
| pinho escuro (tábuas com frestas) | 0,87 | 0,08 |
| pinho de abrigo (veio deitado) | 0,68 | 0,18 |
| carvalho, sol, alcatrão, ferro velho (veio direito) | 0,11–0,20 | 0,27–0,44 |
| corda (torcida, diagonal) | 0,17 | 0,08 |

Limiar 0,5 (`--max-banding`) separa as frestas do veio direito; um veio largo
pode passar 0,5 e pede um limiar mais alto. `--axis v` roda 90° quando o eixo
errado domina (`≥ 0,25` e `≥ 1,5×` o outro) — a corda diagonal, com 0,17,
não roda. A rotação preserva a tileabilidade (o toro é simétrico) e troca os
eixos. Só em imagem quadrada.

### 7. Flocos: muitos, portanto dentro do percentil 99

A cal lavada saía com flocos claros sobre um fundo liso, e o portão do
pormenor isolado dava 0,110 (aprovado): são dezenas de flocos por tile, o
percentil 99 cai **dentro** deles e `máx − p99` fica pequeno. Métrica nova, `tail_ratio`:
quanto a cauda (percentil 99,9) foge da mediana em unidades do "normal
brilhante" (percentil 95) — o simétrico no lado escuro. Uma textura densa tem
uma distribuição em sino e dá 1,5–3,2 em todas as oito outras; a cal com
flocos deu **28**. Limiar 5 (`--max-tail-ratio`).

Com os três portões o ciclo passa a ser um só (`quality_gates` no sidecar):
cada reprovação volta a gerar com a seed seguinte; se nenhuma passar, devolve
a de menor razão valor/limiar — não há ordem entre um nó e uma fresta.

Suite: Texture2D 387 ✓ (307 anteriores + 80 de precisão), sem GPU.

## Futuro (não feito)

- Sampling estilo Tiled Diffusion (CVPR 2025) para SD1.5 (contexto toroidal
  por janelas) — upgrade maior do modo late.
- ESRGAN x2 circular-padded como alternativa ao latent refine (o core está no
  Paint3D, acoplado ao bpy).
- TexTile (classificador aprendido) como métrica pesada opcional de CI.
