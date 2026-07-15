# Kernel opts bench — Text2D / Text3D / Paint3D

Medição cold vs hot (mesmo processo: load 1×, generate 2×).

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
| 16 | part3d | `pt3d-no-cl` | 17.4 | FAIL | FAIL | q=fast | mem-eff | vd=flashvdm | no-channels-last | FAIL:OutOfMemoryError:CUDA out of memory. Tried to allocate 1.25 GiB. GPU 0 has a total capacity of 5.64 GiB of which 401.06 MiB is free. Process 474180 has 25.97 MiB memory in use. Including non-PyTorch memory, this process has 5.13 GiB memory in use. Of the allocated memory 2.09 GiB is allocated by PyTorch, and 2.91 GiB is reserved by PyTorch but unallocated. If reserved but unallocated memory is large try setting PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True to avoid fragmentation.  See documentation for Memory Management  (https://docs.pytorch.org/docs/stable/notes/cuda.html#optimizing-memory-usage-with-pytorch-cuda-alloc-conf) |
| 17 | part3d | `pt3d-compile` | 16.6 | FAIL | FAIL | q=fast | mem-eff | vd=flashvdm | compile=default | channels_last | FAIL:TorchRuntimeError:RuntimeError when making fake tensor call
  Explanation: Dynamo failed to run FX node with fake tensors: call_function torch_cluster.fps(*(FakeTensor(..., device='cuda:0', size=(81920, 3)), FakeTensor(..., device='cuda:0', size=(2,), dtype=torch.int64), FakeTensor(..., device='cuda:0', size=()), True), **{}): got RuntimeError("The tensor has a non-zero number of elements, but its data is not allocated yet.\nIf you're using torch.compile/export/fx, it is likely that we are erroneously tracing into a custom kernel. To fix this, please wrap the custom kernel into an opaque custom op. Please see the following for details: https://pytorch.org/tutorials/advanced/custom_ops_landing_page.html\nIf you're using Caffe2, Caffe2 uses a lazy allocation, so you will need to call mutable_data() or raw_mutable_data() to actually allocate memory.")
  Hint: Your code may result in an error when running in eager. Please double check that your code doesn't contain a similar error when actually running eager/uncompiled. You can do this by removing the `torch.compile` call, or by using `torch.compiler.set_stance("force_eager")`. 

  Developer debug context: 

 For more details about this graph break, please visit: https://meta-pytorch.github.io/compile-graph-break-site/gb/gb4315.html

from user code:
   File "/media/maikeu/b1e73891-ddde-49a0-9382-903accb68b49/GitClones/GameDev/Part3D/.venv/lib/python3.13/site-packages/torch_cluster/fps.py", line 107, in torch_dynamo_resume_in_fps_at_97
    return torch.ops.torch_cluster.fps(src, ptr_vec, r, random_start)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Set TORCHDYNAMO_VERBOSE=1 for the internal stack trace (please do this especially if you're reporting a bug to PyTorch). For even more developer context, set TORCH_LOGS="+dynamo"
 |

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

## Conclusões (2026-07-15, mesmo prompt/seed)

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

**Replicar já:** hw-auto Text3D usa `flashvdm` se VRAM&lt;7.5 GiB (senão `hierarchical`); Text2D `--compile` em server mode.

**Skip:** `t3d-group-offload` (Hunyuan custom); `t3d-fp8` (dtype mismatch Half/BF16); `t3d-compile` vanilla one-shot; Paint3D `--compile` com mem-eff/SDNQ.
