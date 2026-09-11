# Text2D — Group Offload + Streams + int4 com folga (2026-09-11)

**Problema:** o modelo 2D (FLUX.2 Klein) falhava nos pipelines em GPUs 6–12 GB.
A escada do planner escolhia "full-GPU + sdnq-int4" sempre que o pico estimado
*cabia* no orçamento — em GPUs 8 GB (4B: 5.98/7.2 GiB = 83%) e 12 GB (9B:
9.82/10.8 GiB = 91%) isso é "caber por pouco": zero folga para o VAE decode,
CUDA context, fragmentação — OOM no runtime. Agravado pelo admit do vramd com
calibração stale (`admit_peak_mib: 6144` = GPU inteira numa 4050 6 GB — medição
do path antigo model_cpu, com "ativação" medida de 5.18 GiB).

**Solução** (port do padrão Paint3D `b01b8872` + degraus finios):

1. **Group offload + CUDA streams por defeito** — novo gate
   `full_gpu_budget_fraction=0.70` no `plan_offload` (Text2D): full-GPU só com
   pico ≤ 70% do orçamento; acima disso o planner força `group_stream`
   (pico ≈ ativação). Kill-switch: `--no-group-offload` /
   `TEXT2D_GROUP_OFFLOAD=0` / `AIGAMEKIT_GROUP_OFFLOAD=0` (honrado no path do
   planner, gap anterior: só `_try_group_offload` da base o lia).
2. **4 bits por defeito** — o quant de offload base é `sdnq-int4` (era o mais
   agressivo sempre; agora é o piso): defaults honestos vramd/`needed_mib` do
   text2d passaram de uint8→int4.
3. **int3/int2 só "se for o caso"** — presets novos `sdnq-int3`/`sdnq-int2`
   (Hadamard + SVD, lib sdnq ≥0.2.1), **exclusivos dos modos de offload**
   (`_OFFLOAD_ONLY_QUANT`): engajam quando o headroom do GO
   (orçamento − ativação) fica < 2.5 / < 1.5 GiB. Nunca competem no full-GPU
   (int4+GO tem pico menor que int3/int2 full — degradar bits sem offload é
   sempre pior).
4. **Chunks menores** — VAE tiling (tile 256) + VAE slicing + attention slicing
   ligam em todos os modos de offload e em full-GPU a ≥1024 px
   (`target_resolution=1024` no placement do Text2D); GO com streams força
   `num_blocks_per_group=1` (chunks de 1 bloco).
5. **`record_stream=False`** no `plan_group_offload` (era True): com record
   medimos OOM-spin no Paint3D dual-UNet — frees determinísticos valem o
   ligeiro overhead.
6. **Alloc conf por modo** (lição Paint3D): `PYTORCH_CUDA_ALLOC_CONF` =
   `expandable_segments:True` (só) quando GO vai correr — `max_split_size_mb`
   sob churn de onloads pequenos + blocos grandes de ativação explode o
   reserved por fragmentação. Sem GO: `max_split_size_mb:128,gc:0.6` (clássico).
   Aplicado cedo no CLI (`setdefault` — override do user ganha) e por-request
   no worker vramd (replace se torch ainda não acordou).
7. **`allow_group_offload` viaja no request vramd** (payload → worker → ctor
   `group_offload`): o env do supervisor não reflete o pedido do utilizador.

## Escada resultante (planner puro, `plan_offload`)

| GPU | Modelo | Plano | Pico est. |
|-----|--------|-------|-----------|
| 3 GB | 4B | GO leaf + streams + **int2** | ~1.5 GiB |
| 4 GB | 4B | GO + streams + **int3** | ~1.5 GiB |
| 6 GB | 4B | GO + streams + int4 | ~1.5 GiB |
| 8 GB | 4B | GO + streams + int4 (era full int4 a 83%) | ~1.5 GiB |
| 12 GB | 9B | GO + streams + int4 (era full int4 a 91%) | ~1.5 GiB |
| 16 GB | 9B | full-GPU int4 (~68% do orçamento) | 9.8 GiB |
| 24 GB | 9B | full-GPU int4 (~45%) | 9.8 GiB |
| kill-switch | qualquer | comportamento clássico (full/model_cpu) | — |

Fatores de peso novos (`QUANT_WEIGHT_FACTOR`): `sdnq-int3` 0.28, `sdnq-int2`
0.25 (int4 medido 0.32 = bits/16 + escalas/SVD ~0.07).

## E2E — RTX 4050 Laptop 6 GB (2026-09-11)

- **Plano confirmado (worker vramd, GPU limpa):** `cuda | quant=sdnq-int4 |
  group_stream | group-offload(block_level | stream) | vae-tiling |
  attn-slice`. Com a GPU ocupada (~2.1 GiB livres) o placement desceu
  sozinho para `leaf_level` (grupos mínimos) — a adaptação à VRAM livre real
  funciona.
- **1024²·4 steps via vramd (fluxo pipeline):** ✓ imagem gerada; load+quant
  23 s, cliente total 37.4 s. Admit do supervisor: `peak=3558 MiB
  (pesos=1638+act=1536+safety)` — o footprint GO, não os 6117 antigos.
- **512² (GPU partilhada, ~2.4 GiB livres):** ✓ gerada; inferência 14 s;
  pico do processo ~2.6 GiB (um warning de allocator OOM-recover no fim).
- **Calibração do path GO** (`vramd calibrate text2d --load-kwarg
  allow_group_offload=true`, catálogo `backends-6g.yaml`): **peak 5568 MiB /
  admit 5952** a 1024². Lição: com GO os chunks de ativação (attention +
  VAE decode) **usam a VRAM livre pós-offload** — numa 6 GB o pico sobe até
  ~5.5 GiB (o report atribui ~2.1 GiB a cache/fragmentação do allocator); o
  job completa as 3 repetições com o allocator a recuperar de OOMs
  transitórios (o watcher 0.3.6+ tolera-os). A "folga" real do GO materializa-
  se: pesos residentes ~0.2 GiB (convive com outros processos até ao teto),
  512² a ~2.6 GiB, e o admit honesto por medição — o vramd dedica a GPU ao
  job 1024² em vez de OOM silencioso.
- **`needed_mib` do fallback in-process com GO:** 3000 MiB (≤640 px) /
  5400 MiB (>640 px) (`group_offload_needed_mib`), alinhado com a medição.

## Replicação nas tools 2D irmãs + código comum (2026-09-11, parte 2)

O método virou **default absoluto das tools 2D**, com a maquinaria fatorada no
Shared (cada tool só "liga"):

- **`aigamekit_shared.group_offload`** (novo bloco comum):
  `ALLOC_CONF_DEFAULT`/`ALLOC_CONF_GROUP_OFFLOAD` + `cuda_alloc_conf_for()` +
  `apply_alloc_conf_early()` (setdefault; max_split fora sob churn de GO),
  `group_offload_will_engage(footprint, full_gpu_budget_fraction, …)` — réplica
  pura do gate com **specs de VRAM livre** (`cuda_gpu_free_specs`, o mesmo sinal
  do placement real: numa GPU parcialmente ocupada o CLI gate concorda com o
  planner) — e `group_offload_needed_mib(footprint)` (act + margem 1.2 GiB).
- **`DiffusionGeneratorBase`**: `group_offload=True` default + class attr
  `GROUP_OFFLOAD_ENV` (kill-switch por tool) + `_go_planner_kwargs()` —
  devolve `allow_group_offload`/`full_gpu_budget_fraction` coerentes com
  flag+env, prontos a despejar no `_place_with_planner`.
- **Text2D**: refactorado para consumir o comum (sem duplicação).

| Tool | Footprint | Placement | GO modules | Notas |
|------|-----------|-----------|------------|-------|
| Text2D | flux-klein-4b/9b | gate 0.70 + preset aplicado | transformer + text_encoders | referência |
| Texture2D | **sd15-base** (2.4/1.2/1.8, novo) | planner adotado (era `pipe.to` direto), `allow_quant=("none",)` (SD1.5 sempre fp16 — sem quant runtime) | `("unet","text_encoder")` | VAE fora do GO (tiling); offload_mode no perfil |
| Skymap2D | flux-dev-uint4 (pré-quant) | gate 0.70 + **mantém `force_group_offload=memory_efficient`** (histórico: footprint só cobre o transformer) | default (transformer + encoders) | |
| Text2Icon | sana-sprint-600m | gate 0.70 + `target_resolution=1024` | default (transformer + encoder; sem text_encoder_2) | quantização própria no ctor → `allow_quant=("none",)` já existente |

- `allow_quant=("none",)` nas irmãs também protege o gate do CLI: sem isto o
  planner "inventa" fp8-layerwise sobre checkpoints que a tool não quantiza em
  runtime (SD1.5) ou já vêm uint4 (skymap) — bug apanhado no E2E do Texture2D.
- CLIs de todas: `--group-offload/--no-group-offload` default ON, alloc conf
  early, `allow_group_offload` no payload vramd (worker → ctor), `needed_mib`
  GO-aware no fallback in-process; `calibrate_load_kwargs` GO no backends.yaml.
- **E2E Texture2D (RTX 4050, GPU partilhada ~3.6 GiB livres com 3 processos
  alheios):** `group_stream + block_level + streams` em `unet`/`text_encoder`,
  textura 512² gerada ao lado dos outros processos ✓. GPU limpa 8 GB+:
  full-GPU clássico com folga (3.6/7.2 = 50% ≤ 70%) ✓.
- Suites: Texture2D 269 ✓ (era 263), Skymap2D 255 ✓ (+8), Text2Icon 183 ✓
  (+12), Text2D 171 ✓, Shared 1291 ✓, GameAssets 766 ✓. E2E GO do Skymap2D
  (2048²) e Text2Icon ficou pendente de janela de GPU livre (a 4050 esteve
  ocupada por trabalhos paralelos); os gates/recusas honrados foram
  verificados (needed 2764 MiB recusado corretamente com 1989 livres). E2E
  Text2Icon GO a 512² confirmado depois (GPU ~5.7 GiB livres):
  `group_stream + block + streams` em transformer/text_encoder, ícone ✓;
  uma tentativa a 1024² OOMou com processos paralelos a crescer na mesma
  janela (VAE Sana fp32 é guloso — recalibrar `sana-sprint-600m` quando
  houver janela estável).

## vramd 0.3.7 (upstream `~/GitClones/vramd`, release via tag → Actions)

**Causa raiz do "não roda nos pipelines":** o admit usava a calibração do
caminho clássico (`activação` medida 5.18 GiB incluía o warmup não-GO) para
requests GO → `peak=6117 MiB` > livres numa 6 GB limpa → recusa sempre.
Fix: `_measured_parts_mib` valida **quantização E modo de colocação**
(`peak_profile.load_kwargs.allow_group_offload`); request GO sem medição GO
cai no footprint (largest+act = 3558). O early-return de `vram.peak_mib` em
`peak_vram_mib` recebe o mesmo gate (o inverso também: medição GO não admite
request clássico). Testes: `TestText2dGroupOffloadAdmit` (vramd upstream).
Adoção monorepo: `vramd>=0.3.7` (pin `aigamekit-vramd` + venv canónico).

## Ficheiros

- `Shared/src/aigamekit_shared/lowvram.py` — gate `full_gpu_budget_fraction`,
  `_offload_quant_mode` (int4 base, int3/int2 por headroom), ladder +
  fatores, bits finos exclusivos de offload.
- `Shared/src/aigamekit_shared/sdnq.py` — presets `sdnq-int3`/`sdnq-int2`
  (use_hadamard), `_COMPRESSION_FACTORS`, `suggest_preset_for_vram`.
- `Shared/src/aigamekit_shared/group_offload.py` — `record_stream=False`.
- `Shared/src/aigamekit_shared/cli_helpers.py` — defaults honestos/needed_mib
  text2d → int4.
- `Text2D/src/text2d/generator.py` — ctor `group_offload=True`,
  `FULL_GPU_BUDGET_FRACTION=0.70`, placement com `allow_quant` fixado ao preset
  aplicado + `target_resolution=1024`.
- `Text2D/src/text2d/hardware.py` — perfil com `offload_mode`,
  `cuda_alloc_conf_for`/`apply_alloc_conf_early`, `group_offload_will_engage`,
  `GROUP_OFFLOAD_NEEDED_MIB`.
- `Text2D/src/text2d/cli.py` — flag `--group-offload` (default ON), alloc conf
  cedo, needed_mib GO, payload `allow_group_offload`.
- `Text2D/src/text2d/vramd_load.py` / `vramd_payload.py` /
  `worker_serve_adapter.py` — request ↔ ctor.

## Testes

- Shared: `TestFullGpuBudgetFraction`, `TestOffloadFineBits` (lowvram),
  `TestFineBitPresets` (sdnq), record_stream, default peak int4.
- Text2D: perfis 6/8/12/16 GB + int3/int2 + kill-switch + alloc conf + flags
  CLI + map load `group_offload` + quantização real int3/int2 (Hadamard, CPU).
