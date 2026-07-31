# Kernel opts — guia operacional (6 GB)

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Tabela cold/hot + configs: [`../KERNEL_OPTS_BENCH.md`](../KERNEL_OPTS_BENCH.md).  
Script: [`../scripts/bench_kernel_opts.py`](../scripts/bench_kernel_opts.py).

Hardware de referência: **RTX 4050 Laptop 6 GB** (2026-07).

---

## Flags CLI comuns

| Flag | Efeito | Env |
|------|--------|-----|
| `--compile` / `--no-compile` | `torch.compile` no transformer/UNet/DiT | `AIGAMEKIT_TORCH_COMPILE=1` |
| `--compile-mode` | `default` \| `reduce-overhead` \| `max-autotune` | — |
| `--channels-last` | Memory format NHWC (Ampere+ conv) | — |
| `--step-cache` | FirstBlock/TaylorSeer (só full-GPU) | `AIGAMEKIT_STEP_CACHE` |

**Regras Inductor (Shared):**

- `reduce-overhead` / `max-autotune` → só `offload=none` (CUDA graphs).
- Com `group_stream` → força `mode=default`.
- Com `model_cpu` / `sequential_cpu` → **skip** compile (ping-pong de device).
- Step-cache só com `offload=none`.

Helpers: `aigamekit_shared.quantization.apply_torch_compile` /
`apply_channels_last` / `resolve_torch_compile_mode`;
`DiffusionGeneratorBase._maybe_compile_transformer` (também `unet` SD1.5).

---

## Quando usar (resumo)

| Tool | One-shot `generate` | Batch / UMS |
|------|---------------------|-------------|
| **Text2D** | opt-in `--compile --channels-last` | **default ON** (batch + UMS) |
| **Text2Icon** | opt-in `--channels-last` | **CL ON**; compile **OFF** |
| **Skymap2D** | compile off (cold ~6 min) | **compile ON** (batch + UMS) |
| **Text3D** | `--volume-decoder flashvdm` (hw-auto &lt;7.5 GiB) | idem; compile one-shot não vale |
| **Paint3D** | mem-eff/SDNQ | **nunca** `--compile` c/ SDNQ (`QConv2d`) |
| **Part3D** | flashvdm+CL; autotune anti-OOM | DiT compile **skip** em ≤8 GB+offload |
| **Texture2D / Text2Sound** | flags wired | sem ganho útil nestes benches |
| **Terrain3D** | `torch.compile` já ON (Linux+CUDA, vendor) | — |

---

## Defaults aplicados no código

### UMS (`ModelServer/adapters/`)

| Backend | Defaults no `load()` | Override |
|---------|----------------------|----------|
| `text2d` | `torch_compile=True`, `channels_last=True` | preload / kwargs |
| `skymap2d` | `torch_compile=True` | idem |
| `text2icon` | `channels_last=True`, `torch_compile=False` | idem |

Preload aceita: `torch_compile`, `torch_compile_mode`, `channels_last`
(ver `modelserver/server.py`).

### CLI batch

| Comando | Defaults |
|---------|----------|
| `text2d generate-batch` | `--compile` / `--channels-last` **ON** |
| `skymap2d batch` | `--compile` **ON** |
| `text2icon batch` | `--channels-last` **ON** |

One-shot `generate` continua default **OFF** (cold do compile não compensa).

---

## Números-chave (hot, mesmo processo)

| Tool / config | Hot (s) | Nota |
|---------------|--------:|------|
| Text3D flashvdm vs vanilla | 43 vs 74 | **−42%** — maior win |
| Text2D compile+CL | ~4.3 vs 4.8 | ~−10%; batch/UMS |
| Text2Icon channels_last | 1.3 vs 1.5 | ~−13%; batch/UMS |
| Skymap2D compile | 30.4 vs 37.7 | −19%; cold ~359 s |
| Text2Icon / Texture2D / Sound compile | ≈baseline ou pior | skip em 6 GB |
| Paint3D compile mem-eff | FAIL | SDNQ `QConv2d.weight` |
| Part3D compile mem-eff | 77 vs 71 | DiT skip; sem ganho |

---

## Part3D autotune (anti-OOM)

Em `Part3D/.../autotune.py` (VRAM livre via `mem_get_info`):

| Condição | Acção |
|----------|--------|
| ≤7.5 GB free | `cond_batch_size=1` |
| ≤6.5 GB free | `max_parts` DiT = 1 |
| mem-eff / offload low VRAM | DiT compile **off** (VAE only) |
| Conditioner OOM | retry part-by-part |

Conditioner **nunca** compilado (`torch_cluster.fps` × Dynamo).  
Input preferido: mesh **shape** Text3D — paint GLB pode hangar em `fix_mesh`.

---

## Bench — caveats

1. **Subprocesso por config** (Text2Icon / Skymap / Text2Sound): group_offload deixa
   VRAM presa no mesmo PID → OOM falso entre configs.
2. **Não matar** jobs GPU / UMS mid-queue sem olhar `ums queue` / `ums debug`.
3. Comparar só **hot** para compile (cold inclui Inductor).
4. Replicar: `Tool/.venv/bin/python docs/scripts/bench_kernel_opts.py --tool <name> --append`.

---

## Checklist agente (6 GB)

```
[ ] Text3D: flashvdm (não vanilla) em VRAM <7.5 GiB
[ ] Text2D batch/UMS: compile+CL (já default)
[ ] Text2Icon batch/UMS: channels_last; sem compile
[ ] Skymap batch/UMS: compile; sem depender de CL
[ ] Paint: sem --compile com SDNQ/mem-eff
[ ] Part3D: shape mesh; autotune; sem Conditioner compile
[ ] One-shot: compile off salvo necessidade batch local
[ ] Ao mudar defaults: actualizar esta doc + MODEL_FINDINGS §5 + KERNEL_OPTS “Aplicado”
```

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-24 | Extraído da campanha bench; defaults UMS/batch Text2D/Skymap/Icon |
