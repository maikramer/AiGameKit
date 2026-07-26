# Kernel opts bench — Text2D / Text2Icon / Text3D / Paint3D / Part3D

Medição cold vs hot (mesmo processo: load 1×, generate 2×).

**Guia operacional (flags, defaults UMS/batch, checklist):** [`findings/KERNEL_OPTS_FINDINGS.md`](findings/KERNEL_OPTS_FINDINGS.md).  
**Hub consolidado (VRAM admit, footprints, Omni, UMS):** [`MODEL_FINDINGS.md`](MODEL_FINDINGS.md).

## Hardware

| Campo | Valor |
|-------|-------|
| GPU | NVIDIA GeForce RTX 4050 Laptop GPU |
| VRAM | 6141 MiB |
| Data | 2026-07-15 |

## Prompt fixo

```
a red wooden crate, stylized game prop, white background, centered
```

## Parâmetros comuns

| Tool | quality | seed | Outros |
|------|---------|------|--------|
| Text2D | `fast` | 42 | width/height via quality; hw-auto |
| Text2Icon | `fast` | 42 | Sana Sprint; low_vram; prompt ícone (mesmo tema) |
| Texture2D | medium→bench 20 steps | 42 | SD1.5 + circular padding; 512² |
| Skymap2D | `fast` (1024×512, 14 steps) | 42 | FLUX.1-dev uint4 + LoRA equirect; mem-eff |
| Text2Sound | `fast` (12 steps, 2s) | 42 | stable-audio-open-small (effects) |
| Text3D | `fast` | 42 | `--from-image` após Text2D baseline; hw-auto |
| Paint3D | mem-eff | — | mesh=`t3d-flashvdm_hot.glb` + ref=`t2d-baseline_hot.png` |
| Part3D | `fast` | 42 | mesh=`t3d-flashvdm_hot.glb` (shape); mem-eff; evitar paint GLB |

**Cold** = 1ª `generate`/`paint` após load (inclui warmup `torch.compile` se activo).  
**Hot** = 2ª no mesmo processo (modelo já em memória).  
**Load** = tempo `_load_pipeline` / `_load_hunyuan` / `PaintBatchProcessor.__enter__`.

## Como correr

```bash
# Text2D baseline + opts (script escreve neste ficheiro)
python docs/scripts/bench_kernel_opts.py --tool text2d --append

# Text2Icon (Sana Sprint — kernel opts)
Text2Icon/.venv/bin/python docs/scripts/bench_kernel_opts.py --tool text2icon --append

# Texture2D (SD1.5 UNet)
Texture2D/.venv/bin/python docs/scripts/bench_kernel_opts.py --tool texture2d --append

# Skymap2D (FLUX equirect — subprocesso por config)
Skymap2D/.venv/bin/python docs/scripts/bench_kernel_opts.py --tool skymap2d --append

# Text2Sound (Stable Audio Open Small)
Text2Sound/.venv/bin/python docs/scripts/bench_kernel_opts.py --tool text2sound --append

# Text3D (usa PNG do bench Text2D ou gera)
python docs/scripts/bench_kernel_opts.py --tool text3d --append

# Paint3D (usa GLB flashvdm + PNG Text2D)
Paint3D/.venv/bin/python docs/scripts/bench_kernel_opts.py --tool paint3d --append

# Part3D (usa GLB paint3d ou text3d)
Part3D/.venv/bin/python docs/scripts/bench_kernel_opts.py --tool part3d --append
```

---

## Resultados

<!-- BENCH_TABLE_START -->

| # | Tool | Config | Load (s) | Cold (s) | Hot (s) | Notas |
|---|------|--------|----------|----------|---------|-------|
| 1 | text2d | `t2d-baseline` | 28.5 | 30.5 | 4.8 | cuda | quant=sdnq-int4 | group_stream | group-offload(block_level | stream | record) | vae-tiling | attn-slice | pico~1.5/5.1GiB |
| 2 | text2d | `t2d-channels-last` | 20.0 | 6.7 | 5.0 | cuda | quant=sdnq-int4 | group_stream | group-offload(block_level | stream | record) | vae-tiling | attn-slice | pico~1.5/5.1GiB |
| 3 | text2d | `t2d-compile` | 13.2 | 25.2 | 4.5 | cuda | quant=sdnq-int4 | group_stream | group-offload(block_level | stream | record) | vae-tiling | attn-slice | pico~1.5/5.1GiB |
| 4 | text2d | `t2d-compile-cl` | 13.4 | 4.3 | 4.3 | cuda | quant=sdnq-int4 | group_stream | group-offload(block_level | stream | record) | vae-tiling | attn-slice | pico~1.5/5.1GiB |
| 5 | text2d | `t2d-step-cache` | 13.2 | 4.5 | 4.5 | step-cache skip (group_stream); ≈baseline |
| 6 | text3d | `t3d-baseline` | ~18 | 76.5 | 74.4 | vanilla volume decode 2122 chunks; full-GPU pico~3.6GiB |
| 7 | text3d | `t3d-flashvdm` | ~16 | 43.3 | 43.3 | FlashVDM decode 64 chunks; **−42% hot** vs baseline |
| 8 | text3d | `t3d-channels-last` | 67.0 | 74.3 | 74.1 | ≈baseline; DiT sem ganho NHWC |
| 9 | text3d | `t3d-flashvdm-compile` | 65.6 | 86.1 | 44.5 | compile hot ≈ flashvdm; cold pior |
| 10 | text3d | `t3d-fp8` | — | FAIL | FAIL | `Half` vs `BFloat16` no linear (hooks) |
| 11 | paint3d | `p3d-baseline` | 13.0 | 351.7 | 296.2 | mem-eff |
| 12 | paint3d | `p3d-channels-last` | 13.1 | 311.2 | 292.4 | mem-eff | channels_last |
| 13 | paint3d | `p3d-compile` | 14.3 | FAIL | FAIL | mem-eff | compile=default | FAIL:RuntimeError:_apply(): Couldn't swap QConv2d.weight |
| 14 | paint3d | `p3d-compile-cl` | 10.5 | FAIL | FAIL | mem-eff | compile=default | channels_last | FAIL:RuntimeError:_apply(): Couldn't swap QConv2d.weight |
| 15 | part3d | `pt3d-baseline` | 24.8 | 119.1 | 120.2 | q=fast | mem-eff | vd=flashvdm | channels_last |
| 16 | part3d | `pt3d-no-cl` | 17.4 | FAIL | FAIL | OOM 6GB (frag / sem CL) |
| 17 | part3d | `pt3d-compile` | 16.6 | FAIL | FAIL | torch_cluster.fps × Dynamo (Conditioner) |
| 18 | part3d | `pt3d-no-cl` | 24.4 | 125.2 | 121.9 | q=fast | mem-eff | vd=flashvdm | no-channels-last |
| 19 | part3d | `pt3d-compile` | 17.2 | FAIL | FAIL | q=fast | mem-eff | vd=flashvdm | compile=default | channels_last | FAIL:OutOfMemoryError:CUDA out of memory. Tried to... |
| 20 | part3d | `pt3d-baseline` | 25.4 | 74.5 | 71.4 | q=fast | mem-eff | vd=flashvdm | channels_last |
| 21 | part3d | `pt3d-compile` | 17.7 | 83.6 | 77.1 | q=fast | mem-eff | vd=flashvdm | compile=default | channels_last |
| 22 | text2icon | `t2i-baseline` | 20.8 | 15.0 | 1.3 | ok |
| 23 | text2icon | `t2i-channels-last` | 11.6 | FAIL | FAIL | FAIL:OutOfMemoryError:CUDA out of memory. Tried to allocate 1.10 GiB. GPU 0 has a total capacity of 5.64 GiB of which... |
| 24 | text2icon | `t2i-compile` | 9.0 | FAIL | FAIL | FAIL:OutOfMemoryError:CUDA out of memory. Tried to allocate 1.10 GiB. GPU 0 has a total capacity of 5.64 GiB of which... |
| 25 | text2icon | `t2i-compile-cl` | 8.8 | FAIL | FAIL | FAIL:OutOfMemoryError:CUDA out of memory. Tried to allocate 1.10 GiB. GPU 0 has a total capacity of 5.64 GiB of which... |
| 26 | text2icon | `t2i-channels-last` | 12.6 | 3.4 | 1.3 | ok |
| 27 | text2icon | `t2i-compile` | 12.6 | 78.9 | 2.3 | ok |
| 28 | text2icon | `t2i-compile-cl` | 12.7 | 79.1 | 2.3 | ok |
| 29 | text2icon | `t2i-baseline` | 13.6 | 3.7 | 1.5 | ok |
| 30 | texture2d | `tex-baseline` | 7.5 | 3.2 | 2.6 | ok |
| 31 | texture2d | `tex-channels-last` | 1.3 | 2.6 | 2.9 | channels_last |
| 32 | texture2d | `tex-compile` | 1.3 | 14.6 | 2.7 | compile=default |
| 33 | texture2d | `tex-compile-cl` | 1.3 | 6.3 | 2.7 | compile=default | channels_last |
| 34 | skymap2d | `sky-baseline` | 31.7 | 48.5 | 37.7 | mem-eff |
| 35 | skymap2d | `sky-channels-last` | 18.8 | 36.0 | 37.1 | mem-eff | channels_last |
| 36 | skymap2d | `sky-compile` | 18.2 | 359.1 | 30.4 | mem-eff | compile=default |
| 37 | skymap2d | `sky-compile-cl` | 19.1 | 367.5 | 30.5 | mem-eff | compile=default | channels_last |
| 38 | text2sound | `snd-baseline` | 12.0 | 1.1 | 0.6 | effects/small | d=2.0s | steps=12 |
| 39 | text2sound | `snd-channels-last` | 7.8 | 0.9 | 0.6 | effects/small | d=2.0s | steps=12 | channels_last |
| 40 | text2sound | `snd-compile` | 8.4 | 27.5 | 0.6 | effects/small | d=2.0s | steps=12 | compile=default |
| 41 | text2sound | `snd-compile-cl` | 8.3 | 14.7 | 0.6 | effects/small | d=2.0s | steps=12 | compile=default | channels_last |

<!-- BENCH_TABLE_END -->

## Configs testadas

### Text2D

| id | Flags |
|----|-------|
| `t2d-baseline` | (nenhuma kernel opt) |
| `t2d-channels-last` | `--channels-last` |
| `t2d-compile` | `--compile --compile-mode default` |
| `t2d-compile-cl` | `--compile --channels-last` |
| `t2d-step-cache` | `--step-cache auto` (só full-GPU; skip se offload) |

### Text2Icon

| id | Flags |
|----|-------|
| `t2i-baseline` | (nenhuma kernel opt; low_vram) |
| `t2i-channels-last` | `--channels-last` |
| `t2i-compile` | `--compile --compile-mode default` |
| `t2i-compile-cl` | `--compile --channels-last` |

### Texture2D

| id | Flags |
|----|-------|
| `tex-baseline` | (nenhuma kernel opt) |
| `tex-channels-last` | `--channels-last` |
| `tex-compile` | `--compile --compile-mode default` |
| `tex-compile-cl` | `--compile --channels-last` |

### Skymap2D

| id | Flags |
|----|-------|
| `sky-baseline` | mem-eff (nenhuma kernel opt) |
| `sky-channels-last` | `--channels-last` |
| `sky-compile` | `--compile --compile-mode default` |
| `sky-compile-cl` | `--compile --channels-last` |

### Text2Sound

| id | Flags |
|----|-------|
| `snd-baseline` | effects/small (nenhuma kernel opt) |
| `snd-channels-last` | `--channels-last` |
| `snd-compile` | `--compile --compile-mode default` |
| `snd-compile-cl` | `--compile --channels-last` |

### Text3D

| id | Flags |
|----|-------|
| `t3d-baseline` | `volume_decoder=vanilla` |
| `t3d-flashvdm` | `--volume-decoder flashvdm` |
| `t3d-compile` | `--compile --compile-mode default` |
| `t3d-flashvdm-compile` | `--volume-decoder flashvdm --compile` |
| `t3d-fp8` | `--fp8-layerwise` |
| `t3d-channels-last` | `--channels-last` |
| `t3d-group-offload` | `--group-offload` (experimental) |

### Paint3D

| id | Flags |
|----|-------|
| `p3d-baseline` | mem-eff (SDNQ uint8); TORCHDYNAMO_DISABLE |
| `p3d-channels-last` | `--channels-last` |
| `p3d-compile` | `--compile --compile-mode default` |
| `p3d-compile-cl` | `--compile --channels-last` |

### Part3D

| id | Flags |
|----|-------|
| `pt3d-baseline` | mem-eff + `flashvdm` + channels_last (default) |
| `pt3d-no-cl` | `--no-channels-last` |
| `pt3d-hierarchical` | `--volume-decoder hierarchical` |
| `pt3d-compile` | `--compile --compile-mode default` |

## Notas de hardware (6 GiB)

- Em RTX 4050 6GB, Text2D tipicamente usa **4B + group_stream** → `reduce-overhead` / step-cache / cudagraphs **não activam** (ou caem para `default` / skip).
- `torch.compile` com `model_cpu`/`sequential_cpu` é **skip** (ping-pong de device).
- Com `group_stream`, compile usa só `mode=default` — **activa** e dá ganho hot pequeno.
- Text3D neste GPU: DiT cabe full-GPU com SDNQ-int4 (~3.6 GiB). Bottleneck = **volume decode vanilla** (2122 chunks).

## Conclusões (2026-07-15/16, mesmo prompt/seed)

| Vencedor | Ganho | Acção |
|----------|-------|-------|
| Text3D `--volume-decoder flashvdm` | **−42% hot** (74.4→43.3s) | reforçar como default hw-auto |
| Text2D `--compile --channels-last` | ~−10% hot (4.8→4.3s) | batch/server |
| Text2D `--compile` só | ~−6% hot (4.8→4.5s) | cold +20s na 1ª |
| Text3D `flashvdm+compile` | hot 44.5 ≈ flashvdm; cold 86s | compile **não vale** one-shot |
| Text2D/Text3D `channels_last` só | ~0 | Text3D DiT sem ganho |
| Text2D `--step-cache` | skip c/ group_stream | só full-GPU |
| Paint3D `channels_last` | hot 292≈296s (~−1%) | sem ganho; **não** hw-auto |
| Paint3D `--compile` (mem-eff) | FAIL | SDNQ `QConv2d.weight` — skip UNet compile |
| Part3D baseline flashvdm+CL | hot **71s** (pós-autotune; thin-skip) | default CL+flashvdm OK 6GB |
| Part3D `--no-channels-last` | hot ~122s (retry) | ≈baseline antigo; OOM era frag/batch |
| Part3D `--compile` mem-eff | hot **77s** (sem OOM) | DiT compile **skip**; só VAE — sem ganho vs baseline |
| Text2Icon baseline (full-GPU) | hot **1.5s** | CLI kernel opts wired |
| Text2Icon `--channels-last` | hot **1.3s** (~−13%) | ganho pequeno; OK batch |
| Text2Icon `--compile` / `+CL` | hot **2.3s** (pior); cold ~79s | **não** usar em 6GB Sana |
| Texture2D baseline SD1.5 | hot **2.6s** (20 steps) | CLI kernel opts wired |
| Texture2D CL / compile | hot 2.7–2.9s ≈baseline | sem ganho útil em 6GB |
| Skymap2D baseline mem-eff | hot **37.7s** (1024×512, 14 steps) | group_stream |
| Skymap2D `--channels-last` | hot 37.1s ≈baseline | ~0 |
| Skymap2D `--compile` | hot **30.4s** (−19%); cold 359s | **batch/server** sim; one-shot não |
| Skymap2D `compile+CL` | hot **30.5s** ≈compile | CL sem ganho extra; 1º run matado externo |
| Text2Sound baseline (small) | hot **0.6s** (2s/12 steps) | CLI kernel opts wired |
| Text2Sound CL / compile | hot 0.6s ≈baseline | sem ganho; compile cold 15–28s |

**Replicar já:** hw-auto Text3D `flashvdm` se VRAM&lt;7.5 GiB; Part3D autotune: `cond_batch=1` + `max_parts=1` em ≤6.5GB; DiT compile off c/ mem-eff.

**Aplicado (server/batch defaults):**
- UMS `text2d`: `torch_compile=True` + `channels_last=True` (override via preload kwargs)
- UMS `skymap2d`: `torch_compile=True`
- UMS `text2icon`: `channels_last=True` (compile off — hot pior)
- CLI `text2d generate-batch`: `--compile` / `--channels-last` default **ON**
- CLI `skymap2d batch`: `--compile` default **ON**
- CLI `text2icon batch`: `--channels-last` default **ON**
- One-shot `generate` continua default OFF (excepto defaults batch acima)

**Skip:** `t3d-group-offload`; `t3d-fp8`; Paint3D `--compile` mem-eff/SDNQ; Part3D Conditioner compile (`torch_cluster.fps`); Part3D DiT `--compile` em ≤8GB+offload (sem ganho); Text2Icon `--compile` em 6GB (hot pior); Texture2D/Text2Sound compile/CL (sem ganho nestes benches); Skymap2D CL só (~0).

**Nota Text2Icon/Skymap2D/Text2Sound:** bench multi-config no mesmo PID deixa VRAM presa → subprocesso por config.

**Terrain3D:** já tem `torch.compile` on por default (Linux+CUDA) no vendor pipeline — sem gap CLI desta campanha.
