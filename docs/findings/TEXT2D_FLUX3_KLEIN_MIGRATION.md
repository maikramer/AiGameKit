# Text2D → FLUX.3 Image Klein — migração e o que mudou (2026-10)

Migração dura do Text2D de `black-forest-labs/FLUX.2-klein-4B|9B` (pipeline
clássico `Flux2KleinPipeline`) para **`black-forest-labs/flux-3-image-klein`**
(ModularPipeline). Sem fallback: um só happy path.

## O modelo

- Transformer **5.6B guidance-distilled** (flow map) + text encoder
  **Qwen3-VL-4B** (Apache-2.0) + VAE `AutoencoderKLFlux2`. Latents packed
  `(B, 128, H/16, W/16)`.
- **16 steps** por defeito; faixa treinada **4-16**. **Sem guidance** (o
  checkpoint distilled não tem guider nem `negative_prompt`) — `--guidance`/
  `guidance_scale` continuam aceites mas são **inertes** (aviso único no log).
- Dois workflows: `text2image` e `image_conditioned` (imagem → i2i; até **10
  referências**; tamanho de saída default = 1.ª referência).
- **Layout por boxes** `{id, desc, bbox:[y_min,x_min,y_max,x_max]}` em 0-1 —
  guia colocação/escala, não é máscara dura. Em edits: `from: "target"` +
  `tgt_bbox` para adicionar; `from: "ref_image_N"` + `src_bbox`/`tgt_bbox` para
  mover.
- **Não tem silhouette conditioning nativo** — a silhueta entra como imagem de
  referência normal. O Text2D ganhou `text2d silhouette` (rembg + threshold de
  alpha → sujeito branco em fundo transparente) e `--ref-silhouette` (extrai e
  usa como `--ref`) para consistência de forma entre poses (animação 2D).
- **Prompt upsampler** (`flux-3-image-klein-pup`, Qwen3.5-9B): a BFL recomenda
  usar sempre; no Text2D é **opt-in** (`--upsample`). Default OFF = o bloco
  `prompt_upsampler` é **popado do ModularPipeline antes do init/load**, logo os
  ~19 GB de pesos do pup nunca são carregados.
- **Licença:** FLUX Kommunity License v1.0; repo **gated** (aceitar no Hub +
  `HF_TOKEN`). O text encoder Qwen3-VL é Apache-2.0.

## diffusers 0.42 — ModularPipeline (armadilhas)

- Suporte chegou no **diffusers 0.42.0** (`Flux3ModularPipeline`,
  `Flux3DistilledModularPipeline`, `Flux3AutoBlocks`, `Flux3DistilledAutoBlocks`,
  `Flux3Transformer2DModel`). Venvs das tools que importam text2d in-process
  (Text3D) têm de ter >=0.42.
- **Lazy load**: `ModularPipeline.from_pretrained(repo)` só resolve o índice
  modular; os pesos entram em `pipe.load_components(dtype={...})`. Para poupar o
  pup:
  ```python
  blocks = ModularPipeline.from_pretrained(repo).blocks
  blocks.sub_blocks.pop("prompt_upsampler", None)   # antes do init_pipeline
  pipe = blocks.init_pipeline(repo)
  pipe.load_components(dtype={"vae": torch.float32, "default": torch.bfloat16})
  ```
- `__call__(prompt=..., output="images")` devolve a lista de imagens diretamente
  (sem `output` devolve o `PipelineState`). `callback_on_step_end` é aceite
  (o nosso `attach_step_hooks` funciona); **não há `_interrupt`** — abortar =
  levantar exceção no callback.
- Componentes são acessíveis como atributos (`pipe.transformer`,
  `pipe.text_encoder`, `pipe.vae`, `pipe.guider`) e `setattr` é a via documentada
  — por isso o **SDNQ runtime** (`quantize_model` + `setattr`) e o nosso planner
  `getattr`-based (GO+streams, channels_last, compile) continuam a funcionar.
- **Rungs clássicos de offload não existem** no ModularPipeline
  (`enable_model_cpu_offload`/`enable_sequential_cpu_offload`) — o
  `apply_offload_plan` faz `hasattr` e avisa "placement pode ser no-op" se o
  plano pedir `model_cpu`/`sequential_cpu`. Na prática o caminho de produção é
  **group offload + CUDA streams** (o default em GPUs pequenas) ou full-GPU;
  se um dia for preciso model_cpu num ModularPipeline, a alternativa da BFL é
  `ComponentsManager.enable_auto_cpu_offload()`.
- `load_components(**kwargs)` encaminha kwargs a cada componente (incl.
  `cache_dir`), e salta componentes já presentes (`getattr(pipe, name) is not
  None`) — o que torna seguro quantizar com `setattr` depois do load.

## SDNQ

- Quantiza-se em runtime `transformer`, `text_encoder` e (só com `--upsample`)
  `prompt_upsampler`; o **VAE fica fp32** (qualidade do decode). Não há
  `text_encoder_2`.
- **`dequantize_fp32=False` é obrigatório neste pipeline.** O ModularPipeline
  deriva o dtype de compute de `transformer.dtype` (dtype do 1.º parâmetro
  flutuante). Com o default do preset (`dequantize_fp32=True`) as scales ficam
  **fp32** → o pipeline cria latents fp32 e os lineares não-quantizados (bf16)
  rebentam com `expected mat1 and mat2 to have the same dtype, but got: float
  != c10::BFloat16`. Com scales bf16 o compute fica bf16 e o dequant é
  consistente (validado E2E na 4050).
- **Matmul quantizado desligado**: o FLUX.3 calcula o embedding de timestep em
  float32 e o kernel int8 do SDNQ não faz autocast — a aceleração Triton fica
  off (`apply_quantized_matmul(enabled=False)`); a via dequant+linear (default)
  funciona e a 512²/4 steps gera em ~6 s na 4050.
- Presets ladder mantém-se: int4 base com GO; int3/int2 só quando o headroom
  `usable - activation` aperta (<2.5 / <1.5 GiB).

## E2E medido (RTX 4050 Laptop 6 GB, int4 + GO+streams)

- Load ~32 s (cache quente), generate 512²/4 steps ~5-8 s; 1024²/16 steps
  ~45 s. Pico ~5.4 GiB (admit 5824 MiB) — em linha com o FLUX.2.
- `vramd calibrate text2d`: weights 0.37 GiB + activation 4.73 GiB + context
  0.23 GiB → `vram_mib 5440`, `admit_peak_mib 5824` (entrada em
  `calibrated/backends-6g.yaml`).
- Referências (1 ref): saída no tamanho da 1.ª ref; edição consistente.
  Boxes: colocação respeitada (modelo trata como guia, não máscara; cores podem
  desviar). Silhueta: forma copiada com fidelidade — caso de uso de animação 2D
  validado.
- Aviso benigno do GO: "some layers were not executed during the forward pass"
  (prefetch tracing em blocos condicionais do FLUX.3) — a geração corre e o
  pico medido mantém-se dentro do admit.
- **Preflight restrito**: o repo tem 43 GB (variants fp8r/nvfp4 + o
  `model.safetensors` single-file original) mas o pipeline só usa ~20 GB. O
  `_preflight_download` ignora `variants/*` e `model.safetensors`.
- **`--upsample` validado E2E** (pup 9B público, ~18 GB): warmup 462,7 s
  (download+load+quant int4), generate **334 s/imagem** a 256²/10 steps na
  4050 (o pup é streamado leaf-level pelo GO). A caption expandida saiu
  perfeita — "full profile facing left… left leg reaches forward, right arm
  swings back…" — e corrigiu uma direção que o prompt curto não conseguia.
  Custo alto: usar em imagens críticas, não em batch/sprite sheets.
  A caption vem em `metadata["caption"]` (e `output=["images","prompt"]`).
- Com `--upsample`, `prompt_upsampler` entra no `offload_modules` do GO —
  sem isso o pup ficaria na CPU (minutos por caption).

## 2D animation / sprite sheets (teste de capacidades)

Teste real: 10 quadros de um cavaleiro a andar, 8 direções + 2 fases de passo,
256², pós-processado para 96² + paleta 32 cores (look 16-bit).

- **Consistência de personagem com `--ref` é excelente** — o mesmo herói,
  cores e proporções em todos os quadros (a referência domina).
- **Direções**: instruções curtas ("side view", "walking left") são
  insuficientes no image-conditioned — o modelo mantém a vista da referência.
  Com **captions completas do alvo** (como a BFL recomenda: descrever a imagem
  final, não a edição), S/E/N saem certos e os diagonais parcialmente.
- **Truque de produção**: para sprites simétricos, gerar E e espelhar → W;
  NE→NW; SE→SW. Consistência perfeita e metade das gerações.
- Com `--upsample` a direção difícil (perfil esquerdo) sai certa — o pup
  escreve a caption completa, ao custo acima.
- Receita 16-bit: prompt "16-bit SNES-era pixel art, flat colors, crisp pixels,
  no anti-aliasing", gerar 256², cortar fundo (rembg), downscale 96²
  (LANCZOS) + `quantize(colors=32, MEDIANCUT)`, upscale NEAREST para preview.

### Como dar variação de pose (medido)

Medida de referência: **fração de pixels com alpha diferente** entre frames
consecutivos (0 = frames idênticos). É a métrica que está em
`text2d.poses.alpha_difference` e no `--measure` do `walk-cycle`.

| Técnica | Variação entre frames | Notas |
|---------|----------------------|-------|
| Referência + prompt genérico ("a andar") | **8%** | frames quase idênticos |
| Referência + **frase de pose** (pernas/braços) | **16-26%** | a pose muda, a personagem mantém-se |
| **Silhueta da pose desenhada** como referência | 14-29% | a forma desenhada é copiada (as pernas seguem os ângulos) |
| `text2d walk-cycle` v1 (referência + prompt por frame) | 21-27% | substituído pela v2 (abaixo): o número incluía o salto de escala entre frames |

Regras apuradas:

- **A referência manda na pose**: prompts de instrução curta ("side view",
  "walking left") são ignorados no image-conditioned. Descrever a **imagem
  final** (quem é, de que vista, em que pose) é o que funciona.
- **Vocabulário por vista**: de perfil fala-se de "leading/trailing leg" e
  "front/back arm"; de frente, de "left/right leg/arm" (biblioteca
  `text2d.poses`).
- **Direções espelhadas**: gerar a canónica (E) e espelhar dá a personagem
  idêntica nos dois sets (W) — e metade das gerações.
- Medição sem pipeline de sprites dá valores enganadores: o PNG cru tem fundo
  opaco → alpha igual → "0% de diferença" mesmo com poses diferentes. Medir
  **após** rembg + downscale.

### Walk-cycle v2: folha única guiada por silhuetas

A receita v1 (um frame por fase a 256², referência + prompt de pose) dava
frames **repetidos e borrados**. Causas encontradas (2026-10, RTX 4050):

| Sintoma | Causa | Correção |
|---------|-------|----------|
| Frames 1-3 iguais à referência | a "referência idle" era gerada com `poses[0]` (= `walk_contact_front`) **e** o texto de direção traz "walking to the right" → identidade a meio da passada; o image-conditioned copia a pose da referência | identidade **parada** (`facing_prompt` corta o "walking"; idle frontal sem "facing the viewer", que contradizia `north`) |
| Borrão / "pixel art" sujo | geração a 256² (fora da resolução útil do FLUX.3) + LANCZOS 256→96 | identidade a 512², folha 1024×320; downscale por área (BOX) |
| Cores a cintilar no GIF | `quantize` por frame (paleta diferente em cada) | paleta MEDIANCUT única para o ciclo |
| Personagem salta/encolhe no loop | cada frame com escala/enquadramento próprios; a métrica alpha media esse salto como "variação" | uma só geração + alinhamento (tronco/pés) antes de medir |
| Lento (4 min 40 s) | refs relativos ao vramd → worker noutro cwd não os encontra → fallback in-process (recarrega o modelo) | refs absolutos (também em `generate --ref` e `batch`) |

Técnicas medidas (variação média entre frames consecutivos após alinhar, a 96 px):

| Técnica | Resultado |
|---------|-----------|
| Folha 4 células, só prompt | identidade perfeita, **pose igual** nas 4 (o modelo replica) |
| Silhueta por frame (512², refs identidade+silhueta) | pose varia, escala oscila entre frames |
| **Folha + folha de silhuetas + identidade parada** | identidade, escala e chão iguais; perfil 6-11%, frente 4.6% |
| Idem com identidade a meio da passada | **2.8%** — cópia (é o que o aviso apanha: < 4% perfil, < 3% frente) |

Regras apuradas:

- **De perfil**, contacto da frente e de trás têm a mesma silhueta: os membros
  do lado de trás vão a **cinzento** atrás do tronco (pista de profundidade).
- **De frente**, pernas em "V" lateral fazem o modelo virar a personagem de
  perfil. A passada frontal desenha-se com pernas **verticais**: pé da frente
  mais abaixo, calcanhar de trás levantado (D, neutro, E, neutro).
- Silhueta sozinha (de frente) não chega — a pose de cada célula vai também
  por extenso no prompt (`frame 1: ...`). Texto e silhueta têm de concordar.
- Ordem do ciclo: `contact_front, passing, contact_back, down` (alternar
  passada aberta e pernas juntas; v1 tinha 3 passadas abertas seguidas).
- **Vistas próprias por direção** (v2.1): `back` (norte) e três quartos
  (`diag_front` SE/SW, `diag_back` NE/NW). Com a silhueta de perfil puro, os
  frames da diagonal SE saíam de perfil; com a passada em profundidade
  (componente lateral a 55% + perna da frente mais longa/curta) saem a 3/4 com
  passada real (SE 9.6%, NE 9.0%). De costas o pé que avança **sobe** no
  ecrã; continua a ser a vista mais fraca (3.0%).
- **Recorte: flood fill da borda em vez do rembg** (v2.1): um cavalo branco
  em fundo branco saía do rembg com buracos no corpo (o modelo desenhava-o bem
  — o recorte é que falhava). Testado fundo magenta (cor-chave clássica): o
  recorte fica exato, **mas o modelo sombreia com a complementar** — um
  esqueleto saiu verde. Ficou: fundo branco + flood fill a partir da borda
  (tolerância 12; a 24 vaza pelo contorno), que o contorno escuro do pixel art
  trava. 19/19 animações recortadas limpas; ~40 ms/célula; rembg só se a borda
  não for lisa. Armadilha Pillow 12: uma imagem de `Image.fromarray` partilha o
  buffer read-only do numpy e `ImageDraw.floodfill` não faz nada, sem erro —
  `.copy()` antes.
- **Pixel branco residual** (v2.3): o flood fill deixava (a) bolsas de fundo
  fechadas — entre braço e tronco, entre pernas — e (b) um halo claro de 1–2 px
  (anti-aliasing do modelo) que o downscale virava contorno branco. Filtros:
  bolsas = componentes da cor do fundo ≥12 px e ≤4% da figura **rodeadas de
  escuro/saturado** (anel com distância mediana > 60 — um orbe mágico é
  rodeado de claro e fica); desligado sozinho quando a figura tem >5% de pixels
  cor-de-fundo (cavalo branco 31% vs. ≤1.3% nos restantes). Franja = descascar
  2 px de borda com distância ao fundo < 72; o contorno escuro trava. Borda
  clara dos sprites na bateria v2.2: corrida 7.0→0%, oeste 5.6→0%, NE 4.3→0.4%,
  cavalo/lobo/cão intactos.
- **v2.4 — 23 animações, tempos por fase, `anim.json`, `animate-set`.** Novas
  bípedes `block`, `roll`, `shoot`, `kick`, `punch`, `victory`, `sneak`;
  quadrúpedes `quad-idle`, `quad-trot`, `quad-attack`, `quad-jump`,
  `quad-hurt` (silhueta com cabeça/corpo inclináveis, patas com flexão e
  assente no chão como os bípedes). Bateria RTX 4050 (variação média):
  rolar 23.9%, pontapé 12.4%, tiro 11.4%, socos 11.1%, morte 11.4%, bloqueio
  9.1%, furtivo 7.9%, vitória 4.4% (2 frames iguais de propósito); lobo
  ataque 14.2%, salto 19.4%, idle 7.5%, trote 6.8%, dano 7.0%, passo 3.2%.
  Lições:
  - **O modelo completa adereços pelo texto**: "bow" no prompt + braço
    esticado na silhueta → arco e corda desenhados sem os pôr na silhueta.
  - **O vão do arco é uma bolsa grande** (11–13% da figura): o limite de 4%
    e a proteção "figura >5% clara" deixavam-no branco. Proteção nova: maior
    peça cor-de-fundo > 20% (corpo do cavalo branco = 25%) ou > 40% clara.
  - **Morte do esqueleto torta** vinha da 1.ª fase (`hurt`, atirado para
    trás antes de ajoelhar); `death_hit` (cambalear para a frente) encadeia.
  - **Quadrúpede de cabeça muito erguida vira lama/veado**: o modelo copia o
    pescoço vertical da silhueta. Dano/salto usam inclinação do corpo, não da
    cabeça.
  - **Passada de frente pela diferença das coxas**, não pela coxa near: em
    poses agachadas as duas coxas vão para a frente e a v2.2 lia isso como
    passada larga (agachar/furtivo de frente).
  - **Identidade por vista não serve**: gerar a identidade de `south`/`north`
    com a de `east` como referência devolve **cópias** da referência (3×85 s
    perdidos). A folha de silhuetas é que impõe a vista — uma identidade só
    deu perfil, frente e costas corretos. O `animate-set` usa uma identidade.
  - **Lobo**: passada visivelmente maior (26→32°), mas a métrica fica baixa
    porque o modelo põe passada em todas as fases (a alternância é pouca).
- **Saltos**: o recorte assenta os pés no chão e perdia a altura; cada pose
  tem uma elevação (`pose_lift`) que é reposta no sprite depois de alinhar.
- **Quadrúpedes** (`text2d.quadruped`): só perfil. Patas de duas secções
  (coxa + canela) — com uma linha reta por pata o modelo ignorava-as. Cão a
  passo 6.7%, cavalo a galope 6.3%.
- **Silhuetas articuladas** (v2.2): membros com joelho e cotovelo, pé a
  apontar a direção, figura assente no chão automaticamente e rodada por
  `tilt` (dano/queda). Com pernas retas o agachamento era "pernas curtas" e o
  modelo desenhava a personagem de pé; com joelhos dobrados o `jump` agacha de
  facto e o `death` ajoelha, cai e deita. Membros da frente com contorno preto
  (o braço branco sobre o tronco branco desaparecia).
- **Bug de semântica no walk 4** (v2.2): o 4.º frame (`walk_down`) passava a
  mesma perna que o 2.º — o loop arrastava uma perna. Agora
  `walk_passing_back` passa a outra. De costas a passagem levanta o pé certo e
  a vista norte subiu de 2.9% para 3.7%.
- **Grelha 2x4** (v2.2): `walk8` em 8 frames numa só geração (1024x640, ~90 s
  com `--ref` na 4050), variação 8.1% entre frames consecutivos e personagem
  igual nos 8.
- **Auto-retry** (v2.2): abaixo do limiar da animação a folha é regenerada com
  outra seed (`--retries`, default 1) e fica a de maior variação. Na bateria
  de 19 animações não disparou; existe para os casos de seed azarada (v2.1:
  norte 2.9%).
- Bateria v2.2 (19 animações, RTX 4050, vramd): walk 9.8%, walk8 8.1%, run
  9.2%, jump 8.6%, attack 10.4%, hurt 5.8%, death 11.4%, cast 7.9%, SE 6.2%,
  NE 6.5%, S 5.3%, N 3.7%, cão 6.7%, cavalo 6.4%, lobo 3.8%; idle 0.9%
  (respiração, sem limiar).
- `--boxes` **só funciona com `--upsample`**: no pipeline distilled só o bloco
  `prompt_upsampler` consome `boxes` (sem ele o diffusers avisa "Unexpected
  input" e ignora). O CLI recusa `--boxes` sem `--upsample`; o generator
  descarta-as com aviso.

### Kit de jogo 2D: tileset, parallax, vfx, items (2026-10-27)

Regra comum: o modelo **só desenha**; encaixe, recorte, paleta e metadados são
código. Medido na RTX 4050 6 GB via vramd:

| Ferramenta | Gerações | Tempo (modelo quente) | Resultado |
|------------|----------|-----------------------|-----------|
| `tileset` relva/terra, areia/água | 2 × 512² | ~13 s cada | 16 transições encaixam; textura sai fina → `boost_detail` repõe contraste após o downscale 12x |
| `parallax` floresta assombrada | 4 × 1280x512 | ~24 s/camada | magenta recorta limpo (bolsas de céu entre ramos incluídas); céu com a cena por extenso desenhava árvores → a cena entra só como "mood" |
| `vfx` explosion / fire / magic | 1 × 1024x512 + guia | ~60 s | progressão segue a guia (flash → bola de fogo → fumo; orbe → anel → faíscas); fogo em loop |
| `items` 8 itens RPG | 1 × 1024x512 | ~58 s | estilo, contorno e luz iguais nos 8; 32 px perde pormenores pequenos (gargalo verde das poções) |

Lições:

- **Fundo magenta** para camadas que tocam a borda: o conteúdo ocupa >50% da
  borda e a mediana deixa de ser fundo → `chroma_key(key=…)` com a cor medida
  na linha de cima, tolerância 48 e despill.
- **Alpha pela luz** (`a = max(rgb)` sobre preto) serve para luz e fumo; objetos
  opacos no efeito (lenha da fogueira) ficam semitransparentes — pedir o efeito
  sem suporte ou usar `blend: alpha`.
- **Itens por centróide**: recortar a folha inteira e atribuir cada peça à
  célula do seu centro evita cortar ao meio itens que saem da célula.
- **vramd despejava o próprio worker** entre jobs a 1280x512 (livre < pico por
  causa da memória que o allocator do torch guardou) → recarga de ~80 s por
  camada. `worker_serve` passa a chamar `torch.cuda.empty_cache()` após cada
  job (só se o CUDA já foi iniciado): parallax 4 camadas 273 s → 135 s.

## Tiers de qualidade retunados

Distilled 4-16 steps (o antigo icon a 2 steps deixou de existir):

| tier | resolução | steps |
|---|---|---|
| fast | 512² | 4 |
| low | 768² | 8 |
| medium | 1024² | 12 |
| high | 1024² | 16 |
| highest | 1536² | 16 |
| icon (categoria) | 512² | 4 |

`guidance` foi removida das secções `text2d` (o YAML do Shared e o
`generation_profiles.py` do GameAssets espelham a tabela).

## Footprints e vramd

- Footprint novo: `flux3-klein` = `ModelFootprint(18.5, 2.0, 11.2)` GiB
  (9.7B params fp16 ≈ 18.5; maior módulo transformer ≈ 11.2). **Estimativa** do
  model card — recalibrar com `vramd calibrate text2d`.
- `activation_gib=2.0` é reserva do planner GO (pico real medido no FLUX.2 a
  1024² era ~5 GiB com GO+streams); valores altos (4.5) mandavam 6 GB para int2
  sem necessidade.
- A entrada medida em `calibrated/backends-6g.yaml` era do FLUX.2 e foi
  **removida** — até recalibrar, o admit cai nas estimativas + hw-auto.
- **`upsample` é decisão de LOAD e de SHAPE** (o bloco é popado no load; mudar
  exige reload). O backend `text2d` no `backends.yaml` passou a declarar
  `load_keys:`/`shape_keys:` (mecanismo por-backend do vramd) porque a allowlist
  global do supervisor não conhece a chave.

## CLI nova

```bash
text2d generate "prompt" -o out.png                       # t2i
text2d generate "the same character running" --ref hero.png -o run.png
text2d generate "two balloons" --boxes '[{"id":"b1","desc":"a red balloon","bbox":[0.1,0.1,0.5,0.4]}]'
text2d generate "hero pose" --ref-silhouette hero.png -o pose.png
text2d generate "hero" -n 3 -o hero.png                   # hero.png, hero_1.png, hero_2.png
text2d generate "epic scene" --upsample                   # pup 9B (lento)
text2d silhouette hero.png -o hero_sil.png                # extração standalone
```

Com `--ref` e sem `-W/-H` explícitos, a saída fica no tamanho da 1.ª referência
(comportamento do modelo); `-W/-H` ganham sempre.
