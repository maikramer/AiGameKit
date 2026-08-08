# UMS como produto — estudo de generalização

**Data:** 2026-08-06 · **Estado:** estudo (nenhum código alterado)
**Pergunta:** o Unified Model Server pode deixar de ser específico do AiGameKit e
tornar-se um supervisor de GPU configurável para quaisquer modelos/pipelines?

**Resposta curta:** sim. O núcleo já é agnóstico. O acoplamento ao monorepo está
concentrado em **5 pontos**, todos pequenos e substituíveis por configuração
declarativa. O trabalho real não é reescrever — é **extrair packages**, **mover
constantes hardcoded para YAML** e **automatizar a calibração de footprint**.

---

## 1. O que é o UMS hoje (mapa em camadas)

```
cliente (CLI/tool)  ──unix socket JSONL──▶  supervisor UMS  ──stdin/stdout JSONL──▶  worker (venv próprio)
   delegate_to_ums                          JobQueue                                  run_worker_loop
   submit/poll/wait                         AffinityScheduler                         Adapter.load/generate/unload
                                            BackendManager (admit/evict)              modelo na GPU
                                            VRAMPlanner (peso+LRU)
                                            Registry (backends.yaml)
```

Três contratos, já bem separados:

| Contrato | Onde vive | Genérico? |
|---|---|---|
| **Protocolo cliente↔supervisor** | `ModelServer/src/modelserver/protocol.py` | ✅ 100% — comandos, estados, códigos de erro, prioridades. Zero nomes de tools. |
| **Protocolo supervisor↔worker** | `Shared/src/aigamekit_shared/worker_protocol.py` + `worker_serve.py` | ✅ 100% — `load/generate/unload/abort/ping/shutdown` ↔ `ready/progress/done/error/unloaded/pong`. |
| **Contrato do adapter** | `worker_serve_adapter_base.py::WorkerAdapter` (3 métodos abstratos) | ✅ 100% — `load(**kwargs) → model`, `generate(model, request) → dict`, `unload(model)`. |

Módulos do supervisor **já 100% agnósticos** (não conhecem nenhuma tool):
`protocol.py`, `job_queue.py` (728 L), `scheduler.py`, `vram_planner.py`,
`idle_evictor.py`, `runtime_budget.py`, `stats.py`, `worker.py`,
`process_guard.py`, `registry.py`. São ~2.500 linhas de scheduler/admissão/WAL
reutilizáveis tal como estão.

---

## 2. O que o UMS resolve que a concorrência não resolve

Isto é o pitch — vale a pena escrevê-lo antes de decidir refatorações.

1. **Admissão por *pico*, não por pesos.** `can_admit(free, weights + activation + safety)`
   (`vram_planner.py:41-56`). Servers de LLM assumem "o modelo cabe"; aqui a
   pergunta é "cabe o pico da inferência". É a diferença entre OOM a 80% do job
   e recusar em 0.2 s.
2. **Isolamento por processo/venv.** Cada backend corre no seu interpretador
   (`subprocess_pool.py:467`). Modelos com deps incompatíveis (torch 2.x vs 2.y,
   diffusers vs bpy, CUDA wheels diferentes) coexistem. Ray Serve/Triton assumem
   ambiente homogéneo; isto é o problema #1 de quem junta 5 modelos generativos.
3. **Afinidade VRAM na fila.** Se a cabeça precisa de um backend frio e há mais
   atrás um job cujo backend já está quente, salta a cabeça (≤3 cuts). Numa GPU
   onde um load custa 30–90 s, isto é a diferença entre 10 min e 40 min de batch.
4. **Evicção footprint-aware (peso ÷ prioridade + LRU)** — `vram_planner.py:84-128`.
5. **Abort cooperativo + progresso por fase.** Jobs de 5 s a 10 min não são
   token-streams: o progresso é `shape → paint → save`, e o cancel acontece
   **entre fases**, sem matar kernels CUDA a meio.
6. **Ops sem racing:** WAL de jobs, `respawn <backend>` (recarrega código da tool
   sem parar o supervisor), `zero` (liberta toda a VRAM idle sem parar o
   supervisor), `reap` de órfãos, watchdog de PPID nos workers.

**Nicho:** inferência generativa **multi-segundos**, **um-job-de-cada-vez**, em
**GPU de consumo**. vLLM/TGI/Triton/Ray optimizam throughput de tokens em
hardware de datacenter — não competem aqui, e não têm resposta para o ponto 2.

---

## 3. Onde *não* está a magia (honestidade necessária)

Correr um modelo de 26 GB em 6 GB **não é feito pelo UMS**. É feito por três
coisas fora do supervisor:

| Mecanismo | Onde | Exemplo |
|---|---|---|
| **Staged load** (a GPU nunca segura tudo) | na *tool* | Motion3D: text encoder Qwen3-8B em CPU, DiT na GPU — nunca ambos (`Motion3D/src/motion3d/generator.py`) |
| **Quantização** SDNQ int8/int4 | `aigamekit_shared.sdnq` + `lowvram.QUANT_WEIGHT_FACTOR` | text3d fp16 ~8 GiB → int4 ~3 GiB |
| **Offload** (group-stream / sequential / model) | `lowvram.OffloadPlan` | FLUX 9B em 6 GB |

O UMS contribui **duas** peças: (a) declarar a pegada residente real
(`hy-motion-lite` = 1.2 GiB, não 24 — `lowvram.py:116`) e (b) garantir que
ninguém mais toca na GPU enquanto esse job corre.

**Consequência para o produto:** são **dois produtos**, e devem ser separados.

- **`ums-core`** — supervisor, fila, admissão, evicção, workers. Vende-se sozinho.
- **`vram-fit`** (opcional) — planner de quant/offload + registry de footprints
  + auto-calibração. É a camada que faz "modelo grande em GPU pequena", mas
  **precisa de suporte da tool** (uma tool que carrega tudo de uma vez em fp16
  não cabe, independentemente do supervisor).

---

## 4. Inventário de acoplamento (o que impede um backend de terceiros hoje)

| # | Ponto | Ficheiro:linha | Problema | Custo de correção |
|---|---|---|---|---|
| A1 | Comando do worker hardcoded | `subprocess_pool.py:467-468` | `cmd = [python, "-m", tool, "serve", "--ums-worker"]` e o python vem de `discover_monorepo_tool_python` (`env.py:188`, tabela `TOOL_LAYOUT` do monorepo). Um backend externo não tem como declarar o seu comando. Também **não há `env=` nem `cwd=`** no spawn — o worker herda o ambiente do UMS (sem `HF_HOME`, `CUDA_VISIBLE_DEVICES` ou `PATH` próprios). | **Baixo** — `runtime:` no YAML |
| A2 | Heurísticas de pico por nome | `backend_manager.py:519-527` | `mem = name in ("paint3d","text3d","text2d","motion3d") and quant.startswith("sdnq")`; idem `group_offload` e `streams_on_load`. Backend novo cai no caso genérico e o pico fica errado. | **Baixo** — `peak_profile:` no YAML |
| A3 | Allowlists de kwargs | `backend_manager.py:55-118` (`_LOAD_KWARG_KEYS`, `_SHAPE_LOAD_KEYS`) | Contêm `max_num_view`, `bake_exp`, `volume_decoder`, `octree_resolution`… Um backend novo não consegue declarar que `beam_size` muda o shape do load → reuso indevido de modelo carregado. | **Baixo** — `load_keys:`/`shape_keys:` por backend |
| A4 | Registry de footprints em código | `lowvram.py:102-122` (`FOOTPRINTS` dict Python) | Adicionar um modelo obriga a editar `aigamekit-shared`. | **Baixo** — YAML + merge de ficheiros externos |
| A5 | Dependências do supervisor | `ModelServer/pyproject.toml` → `aigamekit-shared` → **torch, bpy, transformers, bitsandbytes, xformers, Pillow** | O supervisor *deliberadamente não cria contexto CUDA*, mas arrasta ~5 GB de wheels para arrancar. Usa apenas 7 módulos do Shared: `gpu`, `logging`, `worker_protocol`, `lowvram`, `vram_budget`, `env`, `model_server`. | **Médio** — extrair packages |
| A6 | Paths/socket fixos | `protocol.py:46-48`, `model_server.py:49` | `~/.cache/aigamekit/model-server.sock`, `ums-jobs.jsonl`. | **Trivial** — env/config |
| A7 | Mapa de bench | `cli.py:1286-1294` | Dict `{"text3d": "text3d.generator", …}` para `ums bench`. | **Trivial** — vem do YAML |
| A8 | POSIX-only | `subprocess_pool.py` (`fcntl`, `select`, `O_NONBLOCK`) | Sem suporte Windows na leitura de pipes. | **Médio** — camada de IO |

**Nada disto é arquitetural.** Não há um único ponto onde a lógica de fila,
admissão ou evicção conheça uma tool.

---

## 5. Proposta: contrato de plugin declarativo

### 5.1 `backends.yaml` v2 (retrocompatível com o v1)

```yaml
version: 2
backends:
  # Backend de terceiros — nada no código do UMS precisa de mudar.
  - name: whisper-large-v3
    runtime:
      command: ["${env:WHISPER_PYTHON}", "-m", "my_whisper.ums_worker"]
      cwd: "~/models/whisper"
      env:
        HF_HOME: "~/hf-cache"
        TOKENIZERS_PARALLELISM: "false"
      load_timeout_sec: 180
      event_timeout_sec: 300
    vram:
      weights_gib: 3.1          # fp16 residente
      activation_gib: 1.2       # pico de inferência acima dos pesos
      quantizable: [int8, int4] # combina com QUANT_WEIGHT_FACTOR
    peak_profile:
      memory_efficient_when: "quant:int4|int8"   # substitui A2
      activation_factor_memory_efficient: 0.65
    priority: 20
    load_keys:  [device, compute_type, beam_size]  # substitui A3
    shape_keys: [device, compute_type]

  # Backend do monorepo — açúcar sintático mantém o YAML atual válido.
  - name: text3d
    runtime: { monorepo_tool: text3d }
    footprint_key: hunyuan3d-omni
    priority: 40
```

Resolvers no `command`: `${env:VAR}`, `${monorepo:tool}`, `${self:cwd}`.
Merge de ficheiros: `data/backends.yaml` (built-in) + `$UMS_BACKENDS_FILE` +
`~/.config/ums/backends.d/*.yaml`. Um utilizador acrescenta um modelo **sem
tocar no package**.

### 5.2 SDK do worker (`ums-worker-sdk`)

Extração literal de `worker_serve.py` + `worker_protocol.py` +
`worker_serve_adapter_base.py`. Integrar um modelo qualquer =

```python
from ums_worker import WorkerAdapter, run_worker_loop

class Adapter(WorkerAdapter):
    name = "whisper-large-v3"

    def load(self, **kw):
        import faster_whisper
        return faster_whisper.WhisperModel("large-v3", device=kw.get("device", "cuda"),
                                           compute_type=kw.get("compute_type", "float16"))

    def generate(self, model, request):
        if self.should_abort(request):
            return self.cancelled_response()
        self.report_progress(request, 0.0, "transcribing")
        segments, _ = model.transcribe(request["audio"], beam_size=request.get("beam_size", 5))
        return {"status": "ok", "output": " ".join(s.text for s in segments)}

    def unload(self, model):
        del model

if __name__ == "__main__":
    run_worker_loop(Adapter, backend_name="whisper-large-v3")
```

~20 linhas. É o teto de esforço que um produto pode pedir.

### 5.3 Cliente (`ums-client`)

`delegate_to_ums` / `submit_to_ums` / `wait_ums_job` / `ensure_ums_running`
extraídos de `model_server.py` (987 L), sem torch nas dependências.

---

## 6. Roadmap (fases entregáveis, cada uma testável isoladamente)

| Fase | Entrega | Fecha | Esforço |
|---|---|---|---|
| **F0** ✅ | `load_keys`/`shape_keys` por backend no YAML; allowlists atuais viram defaults | A3 | feito |
| **F1** ✅ | `runtime:` (`command`/`cwd`/`env`/timeouts) + merge por chave de YAML externo (`$UMS_BACKENDS_FILE`, `backends.d/`) | A1 | feito |
| **F2** ✅ | `vram:` medido vence o footprint no admit; `peak_profile:` substitui as heurísticas por nome | A2 | feito |
| **F3** ✅ | Supervisor sem torch por **extras** (`gpu`/`image`/`mesh`) em vez de split físico: os módulos já eram lazy, só a declaração de deps pesava. Venv 5 GB → 8.7 MB | A5 | feito |
| **F4** ✅ | **`ums calibrate <backend>`** — corre um job real, mede pico com NVML, escreve o descriptor v2 (`modelserver/calibrate/`, 162 casos) | atrito #1 de config | feito |
| **F5** | Produto: `ums init`, `ums add`, docs EN, exemplos (whisper / SDXL / llama.cpp / TTS), gateway HTTP opcional | — | 2 semanas |
| **F6** *(opcional)* | Camada de IO portável (asyncio) → Windows/macOS | A8 | 1 semana |
| **M1** ✅ | `ums calibrate --hw-auto` — kwargs de load vindos do perfil da própria tool | calibrar o caminho real | feito |
| **M2** ✅ | `unload_frees_vram: false` respeitado: planner desprioriza, evicção escala para matar o worker | evicção que não libertava | feito |

**F4 é o diferenciador.** A pergunta que mata a adoção é "como é que eu sei o
`weights_gib`/`activation_gib` do *meu* modelo?". O UMS já mede tudo o que
precisa (`process_vram_mib`, `torch_reserved_mib`, `query_gpu_free_mib` em
`aigamekit_shared.gpu`); falta fechar o ciclo: correr → medir pico → persistir →
admitir com números reais. Sem isto, configurar um backend novo é adivinhação;
com isto, é um comando.

---

## 6-bis. O produto pode assumir o load/offload dos pesos? (escada de invasividade)

Pergunta concreta: *tive de modificar o vendor para usar CUDA streams e carregar
em chunks — isso é declarável em YAML e auto-tunável?*

Resposta: **depende da classe da modificação**. Há quatro, e só as duas primeiras
cabem em configuração. É importante não prometer as outras duas.

### T0 — Zero-touch: hooks sobre `nn.Module` ✅ **já é YAML-ável hoje**

Tudo o que se aplica *por reflexão* sobre um módulo carregado, sem tocar no
código do modelo:

| Otimização | Implementação existente | Mecanismo |
|---|---|---|
| Group offload leaf/block + **CUDA streams** + `record_stream` | `group_offload.py:160` `try_group_offloading` | hooks do `diffusers.hooks.apply_group_offloading` |
| Escolha automática do modo (leaf vs block vs nenhum) | `group_offload.py:80` `plan_group_offload` | fórmula pura VRAM×footprint |
| Quantização SDNQ int8/int4/fp8 | `sdnq.py:326` `quantize_model`, `pre_quantize_model` | substituição de `nn.Linear` |
| Layerwise casting | `group_offload.py:282` `try_layerwise_casting` | hooks |
| CPU offload sequential/model | `lowvram.OffloadPlan` | API do diffusers |
| Attention slicing / sage | `attention.py` | flags do pipeline |

`try_group_offloading` já aceita `modules=("model", "conditioner", …)` — não
assume nomes de pipeline diffusers. **É literalmente o produto pedido**, só falta
a fachada declarativa:

```yaml
- name: my-diffusion-model
  fit:
    quant: auto            # auto | none | int8 | int4 | fp8
    offload: auto          # auto | none | group-leaf | group-block | model | sequential
    streams: true          # use_stream + record_stream nos grupos
    module_names: [transformer, text_encoder]   # onde aplicar (default: heurística)
    vae_tiling: auto
```

`auto` = `plan_group_offload(usable_vram, footprint, quant)`. O worker SDK aplica
isto **depois** do `adapter.load()`, sobre o objeto devolvido. Nenhuma linha de
vendor tocada. Cobre a maioria dos modelos baseados em `nn.Module`.

### T1 — Um gancho: patch declarativo de método ⚠️ **YAML-ável com cuidado**

O caso do chunked prefetch é este. A modificação real está em
`Text3D/src/text3d/hy3dshape/models/autoencoders/inference_utils/extract_geometry_base.py:46`
(`iter_query_chunks`): double-buffer CPU→GPU em `torch.cuda.Stream` dedicada,
`pin_memory()` + `non_blocking=True`, `wait_event` só do chunk atual. Não é
offload de pesos — é **pipelining do loop de inferência** (dados/ativação).

Um YAML não descobre onde está o loop. Mas o loop foi isolado numa função com
assinatura estável — e isso torna-o substituível declarativamente:

```yaml
  fit:
    patches:
      - target: "hy3dshape.models.autoencoders.inference_utils.extract_geometry_base.BaseGeometryExtractor.iter_query_chunks"
        impl: "umsfit.primitives.chunked_prefetch"
        params: { prefetch_depth: 2, pin_memory: true, chunk_elems: auto }
        guard: { sha256: "…"  }    # hash do ficheiro vendor; não bate → não aplica
```

O SDK traz as **primitivas** genéricas (`chunked_prefetch`, `staged_execution`,
`tiled_forward`, `stream_stage`), o YAML escolhe qual e com que parâmetros, o
vendor cede **um** ponto de entrada. Custo para quem integra: extrair o loop
para um método (o que já é boa prática) em vez de espalhar streams pelo código.

**Risco real:** monkeypatch por dotted-path parte quando o upstream muda. Mitigação
obrigatória — `guard.sha256`/versão, e falha *silenciosa com aviso* (corre sem
otimização) em vez de crash.

### T2 — Ordem de execução: `stages` declaradas 🟡 **meio-caminho**

O truque do Motion3D (26 GB→6 GB) não é offload: é **reordenar** — encode do texto
com o Qwen3-8B, libertar, só depois carregar o DiT. A GPU nunca segura os dois.
Nenhum hook adivinha isto, porque é semântica do pipeline.

O que *é* declarável: se o adapter expuser as fases e os submódulos por nome,
o SDK executa o plano.

```python
class Adapter(WorkerAdapter):
    def describe_stages(self):
        return [
            Stage("text_encode", modules=["text_encoder"], output="cond"),
            Stage("denoise",     modules=["dit"],          inputs=["cond"]),
            Stage("decode",      modules=["vae"]),
        ]
```
```yaml
  fit:
    stage_plan: auto        # auto = SDK aloca devices/free por VRAM disponível
    # ou explícito:
    # stages: { text_encode: {device: cpu}, denoise: {device: cuda, quant: int4, free_after: [text_encoder]} }
```

Fronteira: o SDK **não pode inventar** as stages; a tool declara-as uma vez
(~10 linhas) e a partir daí o plano é config e auto-tunável.

### T3 — Reescrita algorítmica ❌ **fork mesmo**

Marching cubes por blocos, atenção por tiles, decoders que materializam um tensor
gigante de uma vez. Não há YAML que resolva. O produto deve **detetar e reportar**
("pico dominado por ativação, não por pesos — nenhum plano de offload ajuda"),
não fingir que resolve.

### O auto-tune: medir, não adivinhar

Aqui está o produto de verdade. Hoje o plano vem de uma fórmula
(`plan_group_offload`) — boa para cold start, cega quanto ao custo real. A versão
produto:

```
ums calibrate <backend> --job sample.json
  → varre o espaço {quant} × {offload} × {chunk/prefetch} com jobs reais
  → mede pico NVML + latência por combinação (já existe process_vram_mib / torch_reserved_mib)
  → escolhe Pareto (cabe na VRAM ∧ menor latência)
  → escreve ~/.config/ums/plans/<modelo>@<gpu>.yaml
```

O ficheiro de plano por `(modelo, GPU, shape do job)` é o **artefacto de valor**:
resolve o atrito nº1 (ninguém sabe que valores meter no YAML), é partilhável
entre utilizadores (registry comunitário de planos: "HY-Motion Full @ RTX 3060
6 GB → int4 + group-leaf + text-encoder CPU → pico 5.1 GiB, 42 s") e transforma
config manual em `um comando`.

### Resumo da resposta

| Camada | YAML chega? | Trabalho no vendor |
|---|---|---|
| Quant + offload de **pesos** + streams de grupo | ✅ sim, hoje | zero |
| Chunking / prefetch do **loop de inferência** | ⚠️ sim, via patch declarativo + primitiva do SDK | isolar o loop num método |
| Ordem de execução (staged load) | 🟡 sim, se o adapter declarar `stages` | ~10 linhas, uma vez |
| Reescrita algorítmica | ❌ não | fork |
| **Escolha dos parâmetros** de tudo acima | ✅ `ums calibrate` (medido) | zero |

Impacto no roadmap: acrescentar **F4-bis — `umsfit`** (T0 declarativo + primitivas
T1 + contrato `stages`), ~1–2 semanas, depende de F3. É o que justifica vender
*dois* produtos em vez de um scheduler.

---

## 7. Riscos e limites conhecidos

- **`MAX_INFLIGHT=1` é uma escolha de GPU pequena.** Numa A100 subutiliza. Já há
  suporte a >1 com verificação de VRAM livre, mas falta *packing* real
  (escolher o conjunto de jobs que maximiza ocupação). Fora do nicho inicial.
- **Multi-GPU não tem placement central.** `gpu_ids` é passado ao worker; o
  supervisor não decide colocação nem faz contabilidade por-device. Numa
  máquina com 2+ GPUs heterogéneas isto é uma lacuna visível.
- **Sem auth/multi-tenant.** Socket unix local com permissões de utilizador. Um
  gateway HTTP (F5) exige repensar isto antes de expor.
- **A camada `vram-fit` não é universal.** Faz milagres com pipelines que
  suportam staged load / offload; não faz nada por um modelo monolítico que
  carrega tudo em fp16. O marketing tem de dizer isto.
- **Nome/marca.** "Unified Model Server" colide com terminologia de LLM serving
  e vende mal o diferenciador. Algo à volta de *VRAM broker* / *GPU admission
  control* posiciona melhor.

---

## 8. Prova de conceito mínima recomendada

Antes de qualquer refactor grande: **adicionar um backend não-monorepo usando
só YAML externo**. Candidato: `faster-whisper` num venv próprio (dep conflitante
com o monorepo, load rápido, pico mensurável).

Critério de sucesso: `ums submit whisper-large-v3 …` funciona **sem uma única
linha alterada** em `ModelServer/src/`, incluindo evicção correta quando o
text3d pede a GPU. Isso valida F0+F1+F2 de uma vez e prova o produto.

Se falhar, o ponto onde falha é exatamente o próximo item do roadmap.

---

## Referências

- Arquitetura atual: [`ModelServer/README.md`](../ModelServer/README.md)
- Migração para subprocessos: [`docs/UMS_SUBPROCESS_PLAN.md`](UMS_SUBPROCESS_PLAN.md)
- Ops VRAM: [`docs/findings/UMS_VRAM_FINDINGS.md`](findings/UMS_VRAM_FINDINGS.md), [`docs/MODEL_FINDINGS.md`](MODEL_FINDINGS.md)
- Waves de batch: [`docs/GAMEASSETS_UMS_BATCH.md`](GAMEASSETS_UMS_BATCH.md)
