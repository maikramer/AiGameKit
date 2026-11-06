# Hi3D v1.0 — refinamento de malha no pipeline (2026-10-29)

Estudo de integração do [Hi3D v1.0 / SPARC3D](https://huggingface.co/hi3d-official/Hi3Dv1.0)
como pós-processamento do shape, **ativado por defeito** no `gameassets batch`.

## Resumo

* **Cabe numa GPU de 6 GB** (RTX 4050 Laptop, CC 8.9): 512³ com **residência por
  fase** → pico **3,74 GiB**, **~60 s** por asset a 25 passos (32 s com o worker
  do vramd já quente). O offload sequencial do upstream fazia ~137 s com 0,86 GiB
  de pico — pagava 92 s de transferências por camada com a GPU quase vazia.
* O stage corre entre o `topology-fix` e o `paint`; o `_clean` continua a ser o
  fallback e o `ensure_clean_for_paint()` devolve o `_refined.glb` quando existe.
* O venv é **Python 3.10** (único do monorepo) porque o upstream só mediu
  torch 2.5.0+cu124 / CUDA 12.4 / flash-attn 2.7.4.post1 / spconv-cu120 2.3.6.
* Pesos: 8 artefactos, **7,73 GB** (2× transformer 3,68 GB + VAE 377 MB).

## Medições

Máquina: RTX 4050 Laptop 6141 MiB, driver 615.71, CUDA toolkit do sistema 13.4
(irrelevante: os wheels trazem o runtime 12.4).

| Config | Passos | Tempo | Pico VRAM | Faces saída |
|---|---:|---:|---:|---:|
| **Residência por fase** (default), worker quente | 25 | **32,0 s** | 3,74 GiB | 31 978 |
| Residência por fase, processo novo (load incluído) | 25 | **60,3 s** | 3,74 GiB | 31 996 |
| Offload sequencial (upstream), processo novo | 25 | 137,0 s | 864 MiB | 31 990 |
| Offload sequencial, 5 passos | 5 | 97,7 s | — | 561 119 |

Fases medidas a 25 passos, GPU de 6 GB:

| Fase | Offload sequencial | Residência por fase |
|---|---:|---:|
| sampler (`_sample_latents`) | 91,8 s | **27,1 s** |
| decode (VAE) | 3,8 s | 3,9 s |
| extract (flexicubes) | 0,7 s | 0,5 s |
| postprocess | 2,8 s | 3,1 s |
| conditioner (DINOv2+CLIP) | 1,5 s | 1,3 s |
| load (construção do modelo) | 19,7 s | 19,7 s |

## Otimização de execução (kernel)

Antes de mexer no código, o pipeline foi instrumentado por fase. A conclusão foi
que o offload do upstream (`enable_sequential_cpu_offload`) move **cada
sub-módulo** a cada forward: 25 passos custam ~91 s de PCIe com um pico de 238
MiB — a GPU passa a maior parte do tempo à espera.

### Aceite: residência por fase (`hi3d/perf.py`)

Os quatro componentes (conditioner, transformer, vae) cabem **individualmente**
na GPU. Em vez de mover camada a camada, movemos o componente inteiro no início
da sua fase e devolvemos à CPU no fim:

| Técnica | Sampler | Total | Pico | Veredicto |
|---|---:|---:|---:|---|
| Offload sequencial (upstream) | 91,8 s | 137 s | 0,86 GiB | baseline |
| **Residência por fase** | **27,1 s** | **60 s** | 3,74 GiB | **default** |
| + `torch.autocast(fp16)` | 27,1 s | 60 s | 3,80 GiB | rejeitado (ruído) |
| + pesos fp16 (`model.half()`) | — | — | — | rejeitado (`polar_cuda` não suporta Half) |
| + `torch.compile(mode="default")` | 60 s (5 passos, cold) | — | 3,93 GiB | rejeitado (sem ganho hot) |
| + `torch.compile(reduce-overhead)` | — | — | — | rejeitado (CUDA graphs falham) |
| + `--fast-postprocess` | 27,1 s | 59 s | — | opt-in (~1 s) |

Notas:

* **fp16 sem ganho**: o modelo já é misto fp16/fp32 (a atenção faz `.half()` no
  qkv e o VAE corre em autocast); o gargalo é largura de banda/overhead, não
  matmul. `model.half()` rebenta no RoPE (`polar_cuda`).
* **compile sem ganho**: o cold (~50 s) não se paga num job de 30 s. O
  `reduce-overhead` falha com "input tensor deallocate during graph recording".
  Confirma a regra do [`KERNEL_OPTS_FINDINGS.md`](KERNEL_OPTS_FINDINGS.md) — aqui
  nem com `offload=none` funciona.
* **`fast_postprocess`** salta o filtro de componentes do upstream (2,8 s em
  540k faces); o nosso `strip_debris` pós-decimação já o cobre. Opt-in porque
  muda a ordem do filtro (antes vs depois da simplificação). Medido: ~1 s.
* **OOM** durante a fase staged degrada para o offload sequencial e repete o
  job uma vez (nunca falha o asset).
* **Transferências**: 3,67 GB in + out por job (~2,4 s). Com o worker do vramd
  quente, o load de 19,7 s é pago uma vez por processo — por isso o 2.º job cai
  para 32 s.

### Load: 19,7 s é construção, não cópia

`from_pretrained` = 19,7 s; `Sparc3DTransformer(cfg)` sozinho custa **17,6 s**
(init de 1,8B parâmetros em CPU). `load_state_dict` é 0,4 s e
`load_state_dict(assign=True)` 0,21 s — não compensa. A via meta-device
(`with torch.device("meta")`) reduz a construção para 3,7 s mas deixa **24
tensores meta fora do state_dict** e `.to("cpu")` falha com "Cannot copy out of
meta tensor" — não é seguro sem tocar no vendor.

### Knobs globais (free)

`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (se o utilizador não definiu),
TF32 em matmul/cudnn e `cudnn.benchmark=True` — aplicados em
`configure_torch()` antes do primeiro contexto CUDA.

## Decisões de desenho

### 1. Normalização do upstream é destrutiva

`sparc3d.sparcubes.postprocess.postprocess_mesh()` faz
`vertices -= vertices.mean(axis=0)` e `vertices /= max‖v‖`. Sem inverter isto, o
GLB refinado sairia com raio 1 na origem e o paint/LOD herdariam escala errada.
`hi3d/align.py` replica a fórmula exatamente (`frame_of`/`restore_frame`); o teste
`test_restore_frame_recovers_world_scale` fecha o ciclo.

### 2. Decimação depois do alinhamento + strip de lascas

`TARGET_FACE_COUNTS = (1M, 1M, 2M)` no upstream. Decimámos no frame de mundo
(erro medido em metros) e removemos ilhas abaixo de 0,5 % do maior componente —
o mesmo `COMPONENT_FACE_RATIO` que o upstream aplica **antes** de simplificar.
Sem o strip, um hero de 32k faces ficava com **214 ilhas de 1–7 faces** (238
faces, 0,7 %) porque a decimação fragmenta lascas novas.

### 3. `topology_clean` **não** corre por defeito no `text3d refine`

Primeira iteração corria `prepare_mesh_topology(morph_close=0, watertight=True)`
por analogia com o resto do DAG. Medição numa malha refinada de hero:

| Versão | Faces | Componentes | Arestas de fronteira | Non-manifold |
|---|---:|---:|---:|---:|
| crua do Hi3D (decimada para 106k) | 106 222 | **1** | **0** | 2 |
| após `topology_clean` (`watertight=True`) | 90 384 | 298 | 2 804 | 0 |
| após `topology_clean` (`--no-watertight`) | 90 384 | 298 | 2 804 | 0 |

O clean **abre** a superfície, fragmenta 298 micro-ilhas e perde 15 % das faces.
Como o sampler já entrega uma superfície de marching cubes fechada, o default
passou a `--no-clean`; `--clean` fica como opt-in. Com `--no-clean` o E2E do
`ensure_to_paint_for_paint` produziu 127 986 faces, 1 componente, 0 boundary,
0 non-manifold — o melhor input de paint dos três.

### 4. Worker vramd sem `aigamekit_shared`

O venv 3.10 não pode instalar a Shared (`requires-python >=3.13`). `hi3d/ums.py`
implementa o mesmo protocolo JSONL (comandos em stdin, eventos em stdout, logs
em stderr + `dup2` para não poluir o canal). `tests/test_ums.py` faz parsing por
`ast` de `Shared/src/aigamekit_shared/worker_protocol.py` e compara todas as
constantes — o teste falha se o contrato derivar.

### 5. Instalação: stack medido, não resolvido

`Hi3D/pyproject.toml` declara **zero dependências**; o hook
`aigamekit_shared.installer.hi3d_extras` instala torch cu124 do índice oficial,
`sconv-cu120`, o wheel pré-compilado do flash-attn (`cp310/cu12torch2.5/
cxx11abiFALSE`, sem nvcc) e `sparc3d` do git `--no-deps`. Se o uv visse `torch`
no pyproject, trocava o wheel cu124 e o flash-attn deixava de carregar.

Dependências não declaradas no upstream, descobertas em instalação limpa:

* `einops` — o `transformers==4.46.3` precisa dele para `modeling_clip`;
* `setuptools` — o `triton==3.1.0` importa `triton.runtime.build` (que importa
  `setuptools`) ao carregar os kernels.

### 6. Contrato do worker: `result["status"]` é o veredicto

O primeiro teste real da delegação vramd deu `GENERATE_FAILED` **depois** de o
GLB já estar escrito. Causa: `vramd.job_queue.Job.mark_finished()` decide
done/failed por `result.get("status")` (default `"error"` quando ausente), e o
adapter do Hi3D devolvia só `{"output": ..., "faces": ...}`. O resultado do
worker tem de trazer `{"status": "ok", ...}` — há um teste dedicado
(`TestVramdResultContract`) para não regredir. Qualquer adapter novo do monorepo
herda esta regra.

### 7. Política de resolução (heurística, não do card)

O card não publica mínimo de VRAM. `capability.py`:

| VRAM livre | Resolução | Offload |
|---|---|---|
| ≥ 40 GiB | 1536³ (3 passes) | não |
| ≥ 16 GiB | 1024³ (2 passes) | sim < 12 GiB |
| ≥ 4,8 GiB | 512³ (1 passe) | sim |
| < 4,8 GiB ou CC < 8.0 | indisponível (exit 3) | — |

`vramd calibrate hi3d` substitui as estimativas por medição real; o descriptor
em `backends.yaml` usa 2048 MiB (medido 864 + folga).

## Integração no GameAssets

`ensure_clean_for_paint()` (usado por `ensure_to_paint_for_paint` e por todos os
call sites de batch/resume/wave) passa a:

1. garantir o `_clean` (topology-fix, como antes);
2. chamar `text3d refine` → `_intermediate/<id>_refined.glb`;
3. devolver o refinado — ou o `_clean` se o refine estiver desligado, sem
   binário, sem pesos, sem GPU, sem imagem de referência, ou com exit 3.

A imagem de referência é descoberta por `_reference_image_for()`:
sidecars `<base>_text2d.png` / `_input.png` (o `text3d generate` do batch passa a
levar `--save-reference-image`) → `<output>/<images_subdir>/<base>.{png,jpg,jpeg}`
(layout split) → ao lado da mesh (layout flat).

O `_to_paint` (orçamento de atlas) é derivado do refinado, e o paint/LOD herdam
a geometria nova sem alterações no master pipeline.

## Configuração

```yaml
refine:
  enabled: true        # default ON
  resolution: auto
  steps: 25
  max_faces_scale: 4.0 # teto = 4 × faces alvo do LOD0
  seed: 0
```

`refine: false` ou `refine: {enabled: false}` desliga. `gameassets doctor` mostra
o estado (ausente / indisponível com razão / pronto com resolução e GB de pesos).

## Limitações e riscos

* Só geometria: o refine tem de ficar **antes** do paint.
* Erros estruturais graves do coarse persistem (o modelo refina, não substitui).
* 1024³/1536³ não foram medidos nesta máquina — os limiares são conservadores.
* Licença: Hi3D Open License 1.0 (branding "Powered by Hi3D 1.0" em integrações
  comerciais; notificação acima de 200k MAU ou 1M USD/mês). Outputs standalone
  do GLB não ficam sujeitos por si só. Ver `Hi3D/README.md` §Licença.
