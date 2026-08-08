# Calibração vramd — RTX 4050 Laptop 6 GB (2026-08-07)

Primeira medição real de footprint de **todos** os backends com
`vramd calibrate`. Números do driver (NVML por processo, ~20 Hz), não estimativas.

- **Hardware:** NVIDIA GeForce RTX 4050 Laptop, 6141 MiB, driver 595.84 (≈5.64 GiB
  utilizáveis para CUDA).
- **Descriptor emitido:** [`Vramd/calibrated/backends.rtx4050-6g.yaml`](../../Vramd/calibrated/backends.rtx4050-6g.yaml)
  (v2, carrega no loader actual: `Registry(yaml_path=…)`).
- **Kwargs de load:** os que o `hw-auto` de cada tool escolhe nesta GPU
  (perfil `cuda-1x6g`), para medir o caminho que a produção usa.

## Resultado

Tudo em MiB. `admit` = pesos + activação + safety, como no
`vram_planner`. `Δ` = declarado − medido (negativo = **o vramd acredita que cabe
mais do que cabe**).

| backend | ctx | pesos | activação | pico | admit | declarado | Δ | conf | sinais |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| motion3d | 126 | 4070 | 1414 | 5610 | 5994 | 2840 | **−3154** | high | |
| texture2d | 134 | 2056 | 1164 | 3354 | 3738 | 2884 | **−854** | high | |
| terrain3d | 0 | 0 | 3714 | 3714 | 4098 | 3584 | −514 | high | lazy load |
| text3d | 264 | 1064 | 4370 | 5698 | 6082 | 5708 | −374 | low | staged, warmup +134 |
| skymap2d | 196 | 78 | 4350 | 4624 | 5008 | 4684 | −324 | low | staged (group offload) |
| part3d | 128 | 0 | 3688 | 3688 | 4072 | 4595 | +523 | high | lazy load, warmup +1150 |
| text2sound | 172 | 2594 | 1654 | 4420 | 4804 | 5504 | +700 | high | fuga 0/run (weight_norm fuse) |
| paint3d | 242 | 1494 | 3974 | 5710 | 6094 | 8576 | +2482 | high | staged |
| text2icon | 0 | 4764 | 862 | 5626 | 6010 | 9395 | +3385 | medium | **unload não liberta** |
| text2d | 226 | 188 | 5298 | 5712 | 6096 | 28544 | +22448 | low | staged |

**Actualização (após correção das tools):** `skymap2d` passou a carregar,
`terrain3d` desceu de 5720 para 3714 MiB sem fuga, `text2sound` de 42 → 34 →
**0** MiB/run (fuse do `weight_norm` no VAE). Ver *Correções aplicadas às tools*.

### Leitura

**Quatro backends subdimensionados** — o vramd admite jobs que não cabem:

- **motion3d (−3154)** é o pior. O descriptor aponta `footprint_key:
  hy-motion-lite` (1.2 GiB), mas o hw-auto desta GPU escolhe `model=full`, e os
  pesos residentes medidos são **4070 MiB**. Nem `hy-motion-full` (2.5 GiB)
  cobriria. Durante um job motion3d o vramd julga ter ~3 GB livres que não existem.
- **terrain3d (−514)**, depois de corrigida a fuga. Na primeira medição crescia
  651 MiB por geração (4660 → 5578 → 5720) e o `admit` chegava a exceder o total
  da placa — ver *Correções aplicadas às tools*.
- **texture2d (−854)**: activação real 1164 MiB contra 512 declarados.
- **text3d (−374)** está dentro da tolerância de 10% — o único cujo footprint
  quantizado está bem calibrado.

**Cinco sobredimensionados** — recusas e evicções desnecessárias. O caso extremo
é **text2d**: o footprint declara 26 GB de pesos que **nunca estão na GPU**
(offload CPU, só 188 MiB residentes; os blocos entram por streaming durante a
inferência). O `vram_mib` estático de 4500, esse, subestima o pico real (5712).

**O `vram_mib` estático e o `footprint_key` discordam sistematicamente.** Hoje
qual dos dois vale depende de o backend ter `footprint_key` — é a ambiguidade
que a calibração elimina.

### Sinais estruturais

- **`unload` do text2icon não devolve VRAM**: 82 MiB de 4764. Evictá-lo não
  liberta nada, portanto o plano do `VRAMPlanner` para este backend é ficção.
  Emitido no descriptor como `peak_profile.unload_frees_vram: false`.
- **Lazy load** (`terrain3d`, `part3d`): o `load` do adapter não põe nada na GPU
  (o terrain3d só constrói `TerrainConfig`); o modelo entra dentro do
  `generate`. Nada residente para evictar, e o pico está todo na inferência.
- **Fugas por repetição**: terrain3d 651 MiB (**corrigida** → 0), text2sound 42 → 34 → **0** (weight_norm fuse).
- **Warmup**: part3d gasta +1150 MiB na 1.ª inferência (estado estável 2538,
  primeira 3688). Calibrar com `--repeats 1` inflaciona.
- **Pico no load**: nenhum backend acabou load-bound com estes kwargs, mas o
  text2icon chegou a 5626 MiB durante a quantização, com OOM recuperável do
  caching allocator.

### Confiança `low` em text2d e text3d

Nos dois, o maior intervalo entre amostras subiu para 0.23 s e 0.34 s (alvo
0.05 s): durante o streaming de blocos CPU→GPU o worker satura a CPU e esfomeia
a thread do amostrador. **Os picos reportados para estes dois são um piso, não
um valor final.** O calibrador baixa a confiança em vez de fingir precisão.

## Reproduzir

```bash
vramd calibrate motion3d --repeats 3 --wait-free 120 \
  --load-kwarg model=full --load-kwarg memory_efficient=true \
  --load-kwarg offload_text_encoder=true \
  --out /tmp/motion3d.yaml --report /tmp/motion3d.json
```

Os kwargs de load **têm de vir do hw-auto da tool** — os adapters não o aplicam
sozinhos (`memory_efficient` só existe se vier no request; em produção é o
`with_vramd_peak_opts` do CLI que o injecta). Perfil de cada tool:

```bash
<Tool>/.venv/bin/python -c "from <tool>.hardware import detect_hardware_profile as f; print(f())"
```

## Defeitos do calibrador que esta corrida expôs

Todos corrigidos, com teste de regressão nomeado pelo caso que os revelou:

| Defeito | Sintoma real | Correção |
|---|---|---|
| Amostras anteriores à 1.ª alocação CUDA contadas como cegueira do driver | texture2d marcava 19% "sem dados" e caía para `low` | só conta cegueira depois da 1.ª vez que o worker aparece no NVML |
| Cache do allocator somada à margem de segurança | texture2d: margem 384 → 1216 MiB (activação contada duas vezes) | margem = dispersão entre repetições estáveis + piso |
| `unload` que não liberta produzia `pesos≈0` + falso "staged load" | text2icon: pesos 82 de 4764 residentes | detecta e reporta `unload_frees_vram: false`; atribuição conservadora |
| `weights == 0` escapava ao teste de staged load | terrain3d/part3d não eram marcados | flag explícita de *lazy load* |
| Residual pós-unload tratado como contexto sem nada residente | terrain3d punha 4.88 GiB de lixo em `context_gib` | contexto = 0 + aviso |
| Backends encadeados sem esperar a VRAM drenar | paint3d recebia "3 MiB livres" logo após o text3d | `--wait-free` (default 120 s) no comando |

## Pôr em vigor

As fases F1/F2 já existem — o descriptor entra por sobreposição, sem tocar no
package:

```bash
export UMS_BACKENDS_FILE=<repo>/Vramd/calibrated/backends.rtx4050-6g.yaml
vramd doctor
```

O bloco `vram:` passa a alimentar o admit (o `footprint_key` fica como fallback),
e o `peak_profile.unload_frees_vram: false` do text2icon faz a evicção escalar
para terminar o worker em vez de um `unload` que não devolve nada.

## Correções aplicadas às tools (2026-08-07)

Três bugs que a calibração expôs, corrigidos e **re-medidos**:

### terrain3d — pipeline nunca largava a GPU

`WorldPipeline.close()` só fecha o tile store e o ficheiro temporário; nunca
toca nos modelos. Como o pipeline é reconstruído a cada `generate_terrain`, os
pesos do ciclo anterior ficavam presos no allocator.

`generator._release_pipeline()`: `close()` → `to("cpu")` → largar as referências
dos submodelos → `gc.collect()` → `empty_cache()`. A ordem importa: mover para
CPU antes de perder as referências, senão os blocos só voltam quando o GC decidir.

| | antes | depois |
|---|---|---|
| pico (4 repetições) | 5720 MiB (a crescer) | **3714 MiB** (estável) |
| fuga por repetição | 651 MiB | **0** |
| admit | 6680 (acima do total da placa) | 4098 |
| confiança | medium | **high** |

### skymap2d — `memory_efficient` aceite e ignorado

O `SkymapGenerator` guardava `memory_efficient` no base mas **nunca o consultava**
na colocação. O planner decidia só pela pegada declarada (`flux-dev-uint4`:
2.2 GiB, que descreve o transformer e ignora text encoders + LoRA fundida),
concluía "4.2 de 5.1 GiB, cabe", escolhia full-GPU, e o load rebentava com
5.31 GiB alocados.

Agora `force_group_offload=bool(self.memory_efficient)` na chamada ao planner.
**Era o único dos 10 que não carregava nesta placa**; passa a gerar com pico
medido de 4624 MiB (pesos residentes 78 MiB — o group offload streama-os).

### text2sound — clear de VRAM ineficaz, `cuda:N`, e churn do `weight_norm`

Três defeitos:

1. o `empty_cache` do `_generation_context` corria com o tensor do job ainda
   vivo, portanto não tinha blocos para devolver. O áudio passa a ir para CPU e
   o tensor CUDA é largado **dentro** do contexto (todos os consumidores só
   gravam, e o `save_audio` já fazia `.cpu()`);
2. o guard usava igualdade exata com `"cuda"`, enquanto o resto do ficheiro usa
   `startswith` — com `device="cuda:0"` a limpeza de VRAM ficava
   **silenciosamente desligada**. Corrigido em todos os guards do ficheiro;
3. o VAE do `stable-audio-tools` aplica `torch.nn.utils.weight_norm` em ~74
   `Conv1d`/`ConvTranspose1d`. Cada decode reescreve `module.weight` com um
   Tensor novo (`weight_v`×`weight_g`); o caching allocator com
   `expandable_segments` **não devolvia** esses blocos — NVML +34 MiB/run com
   `memory_allocated` estável. O salto +592 MiB na 1.ª geração é o conditioner
   (T5/CLAP) a subir para a GPU no primeiro forward, não fuga.

Correcção residual: `AudioGenerator._fuse_weight_norm` no `load` (fundir hooks
com `remove_weight_norm`). Medido in-process: reserved/NVML **flat** em 5
corridas após a 1.ª. Fuga: 42 → 34 → **0 MiB/run**.
