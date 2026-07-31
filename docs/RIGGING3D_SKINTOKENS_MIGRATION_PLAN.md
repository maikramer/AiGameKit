# Rigging3D — Plano de Migração UniRig → SkinTokens

Data: 2026-07-04. Status: **Migração concluída — corte limpo, sem UniRig.**
Fases 0–3 executadas na mesma sessão (por pedido explícito do utilizador: "sem
nada de unirig", sem coexistência de backend). `Rigging3D/src/rigging3d/unirig/`
removido por completo; CLI (`cli.py`) e `hardware.py` reescritos para o novo
backend; testes atualizados (66/66 passam); `pyproject.toml`/README/THIRD_PARTY.md
alinhados. Ver §0.75 e §8 para o detalhe de cada fase.

## 0.5. Resultado da Fase 1 (vendorização) — 2026-07-04

- Código-fonte do SkinTokens (`src/`, `configs/`) copiado para
  `Rigging3D/src/rigging3d/skintokens/`, já com os 3 fixes SDPA da Fase 0 aplicados
  (`model/tokenrig.py`, `model/skin_vae_model.py`, `server/spec.py`). `demo.py`,
  `download.py`, `bpy_server.py` mantidos como referência (`demo_reference.py`,
  `bpy_server_reference.py`) — não fazem parte do caminho de import de produção,
  a Fase 2 substitui esse fluxo por um adapter in-process.
- `pyproject.toml`: `transformers` subiu de `==4.51.3` para `>=4.57.0` (resolveu para
  `5.13.0`, junto com `huggingface-hub` `0.36.2→1.22.0`); `diffusers`/`scipy`
  adicionados formalmente (já estavam presentes transitivamente no venv). **Não**
  adicionados `gradio`/`bottle`/`tornado`/`open3d` — só suportam o servidor HTTP e o
  postprocess voxel do SkinTokens original, nenhum dos dois usado no caminho de
  produção planeado (Fase 2). `skintokens/**/*` incluído em `package-data` e excluído
  do ruff, mesmo padrão de `unirig/`.
- **UniRig legado sob o transformers novo**: smoke-testado (não é um teste de
  comportamento completo) — `src.model.unirig_ar` e `src.model.unirig_skin` (as
  classes que de facto envolvem `AutoModel`/config HF) importam sem erro sob
  `transformers==5.13.0`. Suficiente para não bloquear, mas **não substitui** correr
  o pipeline UniRig completo com checkpoints reais antes de confiar no
  `RIGGING3D_BACKEND=unirig` como fallback de produção — ver §7 (decisão em aberto
  atualizada).
- Suite de testes existente: `192 passed, 1 failed` — a falha
  (`test_vectorized_is_faster_on_long_sequences`) é uma asserção de velocidade
  relativa (`fast` deve ser ≥5x mais rápido que `legacy`) em `tokenizer_part.py`,
  arquivo não tocado por esta migração; reproduz isolada, é sensível a ruído de CPU
  (medição de microssegundos), não relacionada a nenhuma mudança desta fase.

## 0. Resultado da Fase 0 (spikes) — 2026-07-04

Ambiente: RTX 4050 Laptop **6GB VRAM** (única GPU do projeto). Venv de spike em
`/home/maikeu/GitClones/SkinTokens/.venv` (na prática resolve para a partição grande,
`GitClones` está montado lá — sem problema de espaço apesar do path `/home`).

| Risco (§3) | Resultado |
|---|---|
| #1 VRAM 14GB | **Não é bloqueante.** Rodando `demo.py` fim-a-fim (settings default, `num_beams=10`, sem quantização) em CLI single-item: `giraffe.glb` (exemplo simples) pico **3.3GB**; `goblin_clean.glb` (asset real do `simple-rpg`, denso, multi-parte, saída de 53MB) pico **3.9GB**, 27.5s. O "14GB" do README provavelmente reflete o servidor Gradio interativo com múltiplos componentes residentes, não o caso de uso CLI que nos interessa. **Nenhuma quantização foi necessária** — qualidade e velocidade default preservadas. |
| #2 Python 3.11 vs 3.13 / bpy | **Resolvido: unificar tudo em Python 3.13 + `bpy==5.1.2`** (mesma convenção do resto do Rigging3D/Animator3D). Confirmado por wheel-tag: `bpy<=5.0.x` só tem `cp311`, `bpy>=5.1.0` só tem `cp313` — sem overlap, mas o requisito real do SkinTokens é só `bpy>=4.2`, então subir para 5.1.2 funciona sem mudanças de código. Única baixa: `open3d` (usado só em `Asset.voxel()`/`--use_postprocess`, opcional e off-by-default) não tem wheel `cp313` — **removido da instalação**, feature de postprocess voxel adiada (não usada pelo demo default). |
| #3 flash-attn obrigatório | **Eliminado por completo, sem build nativo.** Rastreamento mostrou que só 2 dos 5 pontos de uso realmente forçam a dependência: `tokenrig.py` (`attn_implementation="flash_attention_2"` no `AutoModelForCausalLM.from_config`) e `skin_vae_model.py` (`Perceiver.forward`). Os outros 3 já são seguros na nossa GPU: `attention_processor.py` já tem fallback SDPA próprio (com `repeat_interleave` para GQA); `transformer_blocks.py`/`miche_transformer_blocks.py` só tentam importar flash-attn quando `"H100" in device_name` (guard `FLASH3.available`) — nunca dispara numa 4050, cai direto no branch SDPA que os próprios autores já deixaram pronto (comentado/`else`). Fix aplicado nos 2 arquivos reais + `server/spec.py` (`_attn_implementation`, usado só no caminho opcional `hf_path`): trocado para `"sdpa"` / shim `scaled_dot_product_attention` equivalente. |
| #4 `bpy_server.py` subprocesso HTTP | Confirmado funcional como está (usado no spike). Decisão do plano mantida: produção usa adapter **in-process** (Fase 2), não replica o servidor HTTP. |
| #5 nomeação de bones (`mixamo.yaml`) | **Risco real, confirmado — e mais preciso agora.** `configs/skeleton/*.yaml` (`Order`) **é** consultado em tempo de inferência real (`Transform.parse` → `Order.parse` → `OmegaConf.load` de cada yaml em `skeleton_path`, resolvido por caminho relativo ao cwd — não é só config de treino). Mas só reescreve nomes para os `cls` explicitamente mapeados nesse dict (ex. `vroid`, `mixamo`); o checkpoint usado (`articulation_xl_quantization_256_token_4`) tagueia os nossos assets com `cls="articulation"`, que **não está** nesse mapa — `Order.arrange_names` devolve os nomes inalterados (`if cls not in self.parts_order: return names, {0: None}`). Confirmado empiricamente na Fase 2 (adapter in-process): mesmo com `configs/` corretamente acessível, `goblin_inprocess.glb` saiu com `bone_0..bone_33` genéricos. **Decisão mantida: reaproveitar `_rename_generic_bones`** (heurística topológica já existente no Rigging3D) — continua sendo o único caminho viável para nomes semânticos com este checkpoint. |
| #6 checkpoints | `grpo_1400.ckpt` 1.13GB + `last.ckpt` 487MB + config Qwen3 (~16MB, sem pesos — vêm do ckpt). ~1.6GB total, download rápido. Não é um problema de storage como temido. |
| Achado extra (falso alarme) | Um objeto "Icosphere" (42 verts) apareceu numa inspeção via `bpy` — investigado a fundo e confirmado como **contaminação do próprio script de inspeção** (novo processo `bpy` com cena default Camera/Cube/Light, não limpa antes do import), não do pipeline do SkinTokens. Parse direto do JSON do GLB confirma saída limpa: só bones + `mesh_0` + `Armature`. |

Edições já aplicadas no clone de spike (`/home/maikeu/GitClones/SkinTokens/src/`,
fora do monorepo — replicar ao vendorizar na Fase 1): `model/tokenrig.py` (removido
import morto de `flash_attn_func`, `attn_implementation` → `"sdpa"`),
`model/skin_vae_model.py` (fallback SDPA com suporte a GQA no lugar do import de
`flash_attn`), `server/spec.py` (`_attn_implementation` → `"sdpa"`).

Fonte: [`VAST-AI-Research/SkinTokens`](https://github.com/VAST-AI-Research/SkinTokens.git)
(clonado localmente em `/home/maikeu/GitClones/SkinTokens`), modelo em
[`huggingface.co/VAST-AI/SkinTokens`](https://huggingface.co/VAST-AI/SkinTokens).
SkinTokens é o sucessor declarado do [UniRig](https://github.com/VAST-AI-Research/UniRig)
(mesmo grupo, VAST-AI-Research / Tripo), reivindicando **98–133%** de melhoria em
acurácia de skinning e **17–22%** em predição de bones sobre baselines SOTA.

## 0.75. Resultado da Fase 2 (adapter in-process) — 2026-07-04

`rigging3d/skintokens_runner.py` criado: `run_rig_inprocess()` substitui
`oneshot.py`. Confirmado, contra o venv de produção real (torch 2.11,
transformers 5.13, bpy 5.1.2), rodando `goblin_clean.glb` fim-a-fim sem
nenhum subprocesso/servidor HTTP — bpy e torch/CUDA convivem no mesmo
processo sem conflito, como já era prática no resto do Rigging3D
(`transfer_weights.py`). Achado operacional: os checkpoints (baixados via
`hf_hub_download`, fora do git) precisam de um `configs/` irmão no mesmo
diretório-raiz — o `.ckpt` referencia `configs/skeleton/*.yaml` por caminho
relativo ao cwd no momento do load (ver §0 risco #5). `ensure_checkpoints()`
agora symlinka `configs/` vendorizado do pacote para dentro da raiz de
checkpoints automaticamente. Decisão de corte limpo (pedido do utilizador):
**sem coexistência de backend** — UniRig removido por completo em vez de
mantido atrás de `RIGGING3D_BACKEND` (a estratégia de rollback do §6 foi
descartada a favor de reverter por git se necessário).

## 1. Contexto e motivação

O `Rigging3D` atual (`Rigging3D/src/rigging3d/unirig/`) empacota uma árvore vendored
do UniRig e expõe via CLI (`rigging3d skeleton|skin|merge|pipeline|transfer-weights`).
Arquitetura de 2 modelos separados:

1. **Skeleton** — modelo autoregressivo (task
   `quick_inference_skeleton_articulationxl_ar_256.yaml`) prediz hierarquia de ossos.
2. **Skin** — modelo esparso separado (sparse conv, task
   `quick_inference_unirig_skin.yaml`) prediz pesos de skinning dado o skeleton.
3. **Merge** — módulo próprio (`src.inference.merge`, chamado via `python -m`)
   recombina skin+mesh original num GLB rigado.

SkinTokens substitui (1)+(2) por **um único modelo autoregressivo unificado**
(Qwen3-0.6B backbone) que gera skeleton + "SkinTokens" (pesos de skinning
discretizados via FSQ-CVAE) como uma sequência de tokens interleaved. Isso colapsa
duas fases de inferência em uma, e — segundo o paper — corrige o principal problema
qualitativo do UniRig/Puppeteer: "bleeding" de influência entre partes de mesh
desconectadas (relevante para os assets Text3D atuais, que costumam ter múltiplas
partes/ilhas de geometria).

## 2. Comparação arquitetural

| Aspecto | UniRig (atual) | SkinTokens (alvo) |
|---|---|---|
| Estágios de modelo | 2 (skeleton AR, skin sparse-conv) | 1 (AR unificado skeleton+skin) |
| Backbone | custom (ver `unirig/src`) | Qwen3-0.6B (transformers `AutoModelForCausalLM`) |
| Merge com mesh original | módulo próprio `src.inference.merge` (bpy, subprocess `python -m`) | `use_transfer=True` já embutido no `run_rig` (chama bpy via HTTP a um `bpy_server.py` dedicado) |
| Skin-only (skeleton existente) | task YAML separado + comando `rigging3d skin` | flag `--use_skeleton` no mesmo `run_rig` |
| Pós-processo de skin | Laplacian smoothing (`merge_cmd --smooth-iterations`) | `--use_postprocess` (voxel-based, `voxel_skin`) |
| Nomeação de bones | heurística própria (`_rename_generic_bones`, classifica topologia da árvore) | `configs/skeleton/{mixamo,vroid}.yaml` — mapeamento declarativo nome↔parte via `Order` (`src/data/order.py`) |
| Execução Blender | in-process (`bpy` importado no processo Python principal) | processo `bpy_server.py` separado, comunicação HTTP + `torch.save`/`torch.load` como serialização |
| Python | 3.13 (`bpy>=5.1.0`) | 3.11 recomendado pelo README (`bpy>=4.2` no `requirements.txt`) |
| VRAM mínima documentada | ~6–8GB (aviso hw-auto abaixo de 6.5GB) | **14GB** (requisito explícito do README) |
| Attention | padrão PyTorch | `flash-attn` obrigatório (`flash_attn_interface` ou `flash_attn`, sem fallback puro-PyTorch) |
| Multi-GPU | `CUDA_VISIBLE_DEVICES` por subprocesso (`hardware.py`) | não modelado no repo — assume `cuda` single-device |
| Licença | mesma família (verificar `unirig` vendored) | MIT (confirmado em `LICENSE`) |
| Demo/serving | scripts bash (`launch/inference/*.sh`) | `demo.py` (CLI + Gradio), `download.py` (HF Hub) |

## 3. Riscos e bloqueios críticos (validar antes de qualquer rewrite)

Estes itens podem invalidar partes do plano — tratar como spikes de Fase 0, não
como detalhes de implementação.

1. **VRAM 14GB é um salto grande.** O hardware de referência atual do projeto
   inclui uma RTX 4050 6GB (Text3D/Paint3D já têm modos low-VRAM dedicados) e
   2×RTX 3060 12GB (24GB combinados, mas SkinTokens não faz sharding automático
   — teria de rodar inteiro numa única placa de 12GB, ainda abaixo dos 14GB
   pedidos). **Sem quantização ou offload, SkinTokens pode não caber em nenhuma
   GPU atualmente testada.** Isso é o maior risco do plano — precisa de spike
   dedicado (testar `device_map="auto"` do `transformers`, `bitsandbytes`
   int8/int4 no Qwen3-0.6B — modelo pequeno, deve quantizar bem — ou
   `--low_vram` custom reduzindo `num_beams`/`max_length`/batch).
2. **Python 3.11 vs 3.13.** O resto do Rigging3D/Animator3D está fixado em
   Python 3.13 por causa de `bpy==5.1.0` (não existe wheel PyPI de `bpy` 5.1
   para 3.11). SkinTokens declara `bpy>=4.2` e o README manda criar venv 3.11.
   Duas saídas: (a) testar se o código de `bpy_server.py`/`rig_package/parser/bpy.py`
   funciona sem alterações sob `bpy==5.1.0` + Python 3.13 (provável, a API bpy
   relevante — import/export glTF, vertex groups, armature — é estável entre
   4.2 e 5.1); ou (b) manter um venv 3.11 **separado só para SkinTokens**,
   análogo ao que já existe para outros pacotes com stacks divergentes, e
   falar com o Rigging3D principal via subprocess/CLI. (a) é preferível —
   evita duplicar a complexidade de dois Pythons dentro do mesmo pacote.
3. **`flash-attn` é dependência obrigatória, não opcional.** `tokenrig.py`
   tenta `flash_attn_interface` (FA3) e cai para `flash_attn` (FA2) — mas
   **sempre** precisa de um dos dois instalado; não há caminho puro-PyTorch.
   Compilação de `flash-attn` é notoriamente pesada (native build, precisa
   casar exatamente com a versão CUDA/torch do venv) — mesmo risco de
   fragilidade já visto com `sage-attn` no Paint3D (ver memória
   `paint3d-optimization`). Validar build em cada GPU-alvo antes de prosseguir.
4. **`bpy_server.py` é um subprocesso HTTP persistente, não uma chamada in-process.**
   O `Rigging3D` atual investiu especificamente em eliminar subprocessos
   (`oneshot.py`: "Eliminates 4 subprocess launches... saving ~30-50s of
   import overhead"). O padrão do SkinTokens é o oposto: sobe um servidor bpy
   em processo separado e troca payloads serializados via `torch.save`/`load`
   sobre HTTP (`object_to_bytes`/`bytes_to_object` em `src/server/spec.py`).
   **Não copiar esse padrão.** Ver §5 — a adaptação correta é reimplementar
   o equivalente de `run_rig`'s ramo `use_transfer`/`export` como chamada
   `bpy` in-process, no mesmo estilo de `transfer_weights.py` já existente
   (import GLB, aplicar skin, parent armature, export GLB — tudo dentro do
   processo principal, sem servidor HTTP).
5. **Nomeação de bones — provavelmente uma melhoria real, mas não verificada
   ponta-a-ponta.** `configs/skeleton/mixamo.yaml` define `parts`/`parts_order`
   com nomes `mixamorig:*` completos (corpo + mãos com dedos), consumido por
   `Order.make_names`/`arrange_names` em `src/data/order.py`. Isso é
   estritamente melhor que a heurística atual de `_rename_generic_bones`
   (classifica topologia da árvore às cegas, sem garantia de nomes corretos
   em esqueletos não-humanoides). **Mas** `demo.py`/`run_cli` não expõe
   nenhum argumento `--skeleton_config` ou `--cls` — não está claro no CLI de
   demonstração como/quando esse YAML é efetivamente aplicado no fluxo de
   `run_rig`. Precisa de spike: rastrear onde `Order`/`cls` entram no
   `predict_step` do `TokenRig` (`src/model/tokenrig.py`) e confirmar que dá
   para forçar `cls="mixamo"` (ou equivalente) na nossa chamada programática.
6. **Checkpoints grandes, download HF direto (sem CDN interno).** `download.py`
   baixa `experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt`
   + `experiments/skin_vae_2_10_32768/last.ckpt` + config do Qwen3-0.6B via
   `hf_hub_download`/`snapshot_download`. Sem quantização, isso é peso extra
   de armazenamento e tempo de download por máquina de dev — considerar cache
   compartilhado (mesma lição do `python-venv-upgrade-traps`: `HF_HOME`
   partilhado entre pacotes já é prática no repo).

## 4. Compatibilidade de CLI (superfície pública do `rigging3d`)

Objetivo: manter os comandos existentes (`rigging3d skeleton`, `skin`, `merge`,
`pipeline`, `transfer-weights`) estáveis para não quebrar `GameAssets`
(`master_pipeline`, stage 8 `rigging3d transfer-weights`) nem scripts externos.
Mapeamento proposto:

| Comando atual | Comportamento hoje | Comportamento com SkinTokens |
|---|---|---|
| `rigging3d skeleton -i mesh.glb -o skel.glb` | roda só o estágio skeleton (bash script) | chama `run_rig(..., use_skeleton=False)` e descarta o skin gerado (ou: reformular como "gera skeleton+skin mas só grava o skeleton" — **decisão de design em aberto**, ver §7) |
| `rigging3d skin -i skel.glb -o skin.glb` | roda só o estágio skin dado skeleton existente | chama `run_rig(..., use_skeleton=True)` — mapeamento direto, é literalmente o mesmo flag |
| `rigging3d merge -s skin.glb -t mesh.glb -o out.glb` | módulo bpy próprio, Laplacian smoothing | equivalente a `use_transfer=True`; reimplementar in-process (não via `bpy_server` HTTP, ver risco #4) reaproveitando `Asset.vertices_with_pose`/exportação, ou simplesmente adaptar a lógica de transferência de `demo.py::run_rig` para chamar `bpy` diretamente |
| `rigging3d pipeline -i mesh.glb -o out.glb` | encadeia skeleton→skin→merge (3 chamadas) | colapsa para **uma única chamada** `run_rig(..., use_skeleton=False, use_transfer=True)` — pipeline fica mais simples e mais rápido (elimina 2 dos 3 estágios) |
| `rigging3d transfer-weights` (stage 8, LOD0→LOD1/2) | `bpy.ops.object.data_transfer` puro, sem depender do backend de geração | **sem alteração** — já é independente do UniRig/SkinTokens, continua igual |
| `--gpu-ids`, `--hw-auto` | pina GPU por `CUDA_VISIBLE_DEVICES`; hw-auto liga perfil low-memory | `--gpu-ids`/`--hw-auto` continuam válidos (mesmo princípio, `CUDA_VISIBLE_DEVICES` por subprocesso ou por `.to(device)` in-process). **Nota:** `--low-vram` foi removido de todo o monorepo — Rigging3D passa a usar hw-auto + `--gpu-ids` e o advisory `low_memory_warning` (reduzir `--num-beams`); não há `num_train_vertex` no SkinTokens (ver risco #1) |

## 5. Plano de fases

### Fase 0 — Spikes de validação (bloqueantes, antes de tocar código de produção)
- Rodar `demo.py --input examples/giraffe.glb --output /tmp/giraffe_rigged.glb`
  isolado no clone `/home/maikeu/GitClones/SkinTokens` (venv própria, conforme
  README) na GPU mais forte disponível. Objetivo: confirmar que o pipeline
  finge-de-referência funciona antes de qualquer port.
- Medir VRAM de pico real (não só o README) com `nvidia-smi` durante a
  inferência acima. Decide se risco #1 é bloqueante ou gerenciável.
- Testar `bpy==5.1.0` + Python 3.13 contra o `rig_package/parser/bpy.py` e
  `bpy_server.py` do SkinTokens (import/skin/export de um GLB simples) — sem
  precisar do venv 3.11 completo do SkinTokens, só a parte bpy.
- Tentar compilar `flash-attn` no(s) ambiente(s)-alvo do projeto; documentar
  tempo de build e falhas.
- Rastrear como `cls`/`Order` (mixamo.yaml) se conecta ao `predict_step` —
  confirmar se dá para forçar saída com nomes Mixamo em vez de `bone_N`.

Só prosseguir para as fases seguintes se VRAM e Python/bpy não forem
bloqueantes (ou se houver mitigação clara: quantização, venv separada, etc).

### Fase 1 — Vendorização e empacotamento
- Trazer o código-fonte do SkinTokens (não os checkpoints) para dentro de
  `Rigging3D/src/rigging3d/skintokens/`, no mesmo espírito de como
  `unirig/` é empacotado hoje (`tool.setuptools.package-data`, excluído do
  ruff/ver `[tool.ruff] exclude`).
  - Remover artefatos de app (`bpy_server.py` como processo standalone,
    `demo.py` Gradio, `download.py` CLI) do caminho de import principal —
    manter como referência, mas a integração real vive num adapter novo
    (ver Fase 2).
- Atualizar `pyproject.toml`: adicionar `diffusers`, subir `transformers` para
  a faixa exigida pelo SkinTokens (`>=4.57.0` — checar se quebra compat com
  outras deps já fixadas em `4.51.3` hoje; pode exigir isolar num extra
  opcional ou aceitar o bump global), `open3d`, `gradio`/`bottle`/`tornado`
  **só se mantivermos o server HTTP** (evitar se optarmos por in-process,
  risco #4).
- Decidir sobre `flash-attn` como dependência hard do `pyproject.toml` do
  Rigging3D (vs. opcional com erro claro se ausente).

### Fase 2 — Camada de adaptação in-process (substitui `oneshot.py`)
- Criar `rigging3d/skintokens_runner.py` (nome análogo a `oneshot.py`) com uma
  função tipo `run_rig_inprocess(mesh_path, output_path, *, use_skeleton, use_transfer, seed, ...)`
  que:
  1. Carrega o `TokenRig` (equivalente a `load_model` em `demo.py`) uma vez
     por processo (cache global, como já é feito para `RIGGING3D_HW_AUTO`).
  2. Monta o `DatasetConfig`/`RigDatasetModule` para um único arquivo (sem
     Gradio, sem servidor HTTP).
  3. Chama `model.predict_step(...)` e obtém o `Asset`.
  4. Para exportação: reimplementar o ramo `use_transfer`/`export` do
     `run_rig` original chamando `bpy` **diretamente no processo** (import
     GLB alvo, aplicar `asset.skin`/`asset.joints` como vertex groups +
     armature, exportar) — reaproveitar padrões já testados em
     `transfer_weights.py` (import/parent/export via `bpy.ops`) em vez de
     replicar o protocolo HTTP de `bpy_server.py`.
- Isso elimina a necessidade de subir/derrubar um subprocesso `bpy_server.py`
  por render, ficando alinhado com a filosofia de performance que motivou
  `oneshot.py` originalmente.

### Fase 3 — Rewire do CLI (`cli.py`)
- `skeleton_cmd`, `skin_cmd`, `merge_cmd`, `pipeline_cmd` passam a chamar
  `skintokens_runner` em vez de `_run_bash(...)`/`_run_module(...)`.
- `pipeline_cmd` simplifica: uma única chamada in-process substitui a
  sequência skeleton→skin→merge; os flags `--smooth-iterations`,
  `--groups-per-vertex`, `--draco` continuam existindo mas mapeiam para o
  pós-processamento de export (Draco/smoothing aplicados depois do `Asset`
  vir do modelo, não mais como argumentos de um módulo `merge` separado).
- `_rename_generic_bones` e `_validate_and_fix_origin` — reavaliar necessidade:
  se `mixamo.yaml`/`Order` já produz nomes corretos (confirmado na Fase 0),
  `_rename_generic_bones` pode ser removido; `_validate_and_fix_origin`
  (checagem de convenção "feet" do Text3D) continua relevante independente
  do backend.
- `hardware.py`: redesenhar `Rigging3DHardwareProfile` — o
  conceito de "reduzir `num_train_vertex`" não existe mais; `--low-vram`
  foi removido de todo o monorepo (hw-auto é agora o único mecanismo de
  afinação por VRAM, e o advisory `low_memory_warning` sugere reduzir
  `--num-beams` — depende do resultado da Fase 0, risco #1).
- `transfer-weights` (stage 8) **não muda**.

### Fase 4 — Testes
- `tests/test_cli.py::TestSkeleton/TestSkin/TestMerge/TestPipeline` mockam
  hoje `_run_bash`/`_run_module` (subprocessos); precisam de reescrita para
  mockar `skintokens_runner.run_rig_inprocess` (padrão mais simples — uma
  função, não dois scripts bash + um módulo).
  - `TestRenameGenericBones` — remover ou manter como teste do fallback,
    dependendo da decisão da Fase 3.
- `tests/test_low_vram_skin_config.py` (testa que o YAML low-vram vive em
  `configs/model/`) — obsoleto, o mecanismo muda inteiro; substituir por
  teste equivalente para o novo mecanismo low-vram.
- Novo teste de integração (gated por GPU/checkpoint disponível, skip
  automático sem CUDA — mesmo padrão dos outros pacotes GPU-dependentes):
  gerar rig para `examples/giraffe.glb` (já existe no clone do SkinTokens,
  pode ser copiado como fixture) e validar via `aigamekit-lab check glb` /
  `aigamekit-lab debug compare` contra baseline UniRig.

### Fase 5 — Limpeza e documentação
- Remover `Rigging3D/src/rigging3d/unirig/` (árvore vendored antiga) só
  depois de SkinTokens validado ponta-a-ponta em pelo menos um hardware real
  do projeto (ideal: os dois perfis de referência, 4050 6GB e 2×3060 12GB).
- Atualizar `AGENTS.md`/`CLAUDE.md`: trocar a frase "No PyPI, `bpy==5.1.0`
  exige Python 3.13. Rigging3D e Animator3D usam stack 3.13 + `bpy==5.1.0`"
  se a Fase 0 confirmar que dá para manter 3.13 (provável) — só documentar
  se algo mudou; caso contrário, registrar a decisão sobre venv separada.
  Atualizar também a nota de "Rigging3D/optimization" na memória do agente
  (`rigging3d-optimization.md`) — ela descreve otimizações específicas do
  UniRig (voxel_skin Dijkstra) que deixam de se aplicar.
- Atualizar `docs/MONOREPO_GAME_PIPELINE.md` se ele referenciar
  especificamente UniRig/estágios skeleton+skin separados.

## 6. Estratégia de rollback

Manter os dois backends coexistindo durante a transição via env var, análogo
a outros flags já existentes no repo (`RIGGING3D_HW_AUTO`, `PAINT3D_MULTI_GPU`
legado):

```
RIGGING3D_BACKEND=unirig|skintokens   # default: unirig até a Fase 3 completar; depois: skintokens
```

Só remover o branch `unirig` (Fase 5) quando `skintokens` tiver rodado com
sucesso no `gameassets batch` master pipeline completo (stage rig →
transfer-weights → validate) em pelo menos um asset humanoide e um
não-humanoide (o ganho de "sem bleeding entre partes" do paper é mais
relevante em meshes com múltiplas ilhas de geometria, que é o caso comum de
assets Text3D pós-`topology-fix`).

## 7. Decisões em aberto (precisam de resposta antes da Fase 3)

**Resolvidas na Fase 3 (corte limpo, ver §8):**

1. **`rigging3d skeleton` isolado ainda faz sentido?** Sem dois modelos
   separados, "gerar só o skeleton" vira "gerar skeleton+skin e descartar o
   skin" — desperdiça compute. Alternativa: manter o comando por
   compatibilidade de CLI mas documentar que internamente já roda o skin
   também (sem custo extra real de manter a interface).
2. **`merge` continua como comando standalone?** Com `use_transfer` embutido
   no fluxo principal, pode não haver mais um "skin.glb intermediário sem
   mesh" para dar como `--source` a um `merge` separado. Se o novo pipeline
   sempre produz o GLB final direto, `merge_cmd` vira legado (manter só para
   quem já tem um `skin.glb` gerado por UniRig, durante a transição).
3. **Quantização do Qwen3-0.6B** — a Fase 0 mostrou que **não é necessária**
   (pico real de 3.9GB numa 4050 6GB com settings default, ver §0). Manter a
   ideia arquivada caso surja um asset excepcionalmente denso que estoure o
   orçamento; não priorizar enquanto não houver um caso real que precise.
4. ~~`RIGGING3D_BACKEND=unirig` como fallback de produção~~ — **moot**: por
   pedido explícito do utilizador, a Fase 3 removeu `unirig/` por completo em
   vez de manter os dois backends coexistindo atrás de uma flag (§6
   descontinuado). Rollback, se necessário, é via `git revert`/checkout do
   commit anterior a esta migração, não uma flag em runtime.

## 8. Resultado da Fase 3 (corte limpo — sem UniRig) — 2026-07-04

Executado na mesma sessão que as Fases 0–2, por pedido explícito: "sem nada de
unirig" (sem coexistência de backend) + "transformers 5 otimizado".

- **Removido por completo**: `Rigging3D/src/rigging3d/unirig/` (109 ficheiros
  git-tracked + `logs/`/`lightning_logs/`/`tmp/` gitignored) e `oneshot.py`.
- **`cli.py` reescrito**: `pipeline` (única forma de gerar rig — colapsa
  skeleton+skin+merge numa chamada a `skintokens_runner.run_rig_inprocess`) e
  `transfer-weights` (inalterado, já era backend-agnóstico). Comandos
  `skeleton`/`skin`/`merge` e flags `--root`/`--python` **removidos** (decisão
  #1/#2 do §7: sem economia real de compute em manter `skeleton`/`skin`
  separados quando o modelo já gera os dois juntos; `--use-existing-skeleton` e
  `--no-transfer` no `pipeline` cobrem os mesmos casos de uso). Novos flags
  expostos directamente do gerador: `--num-beams`, `--top-k`, `--top-p`,
  `--temperature`, `--repetition-penalty`, `--postprocess` — controlo que o
  UniRig não oferecia.
- **`hardware.py` simplificado**: removido o conceito de `low_vram`/
  `num_train_vertex` (não existe equivalente no SkinTokens; Fase 0 já mostrou
  que não é necessário — 3.9GB pico numa 4050 6GB). Limiar de aviso ajustado de
  6.5GB para 4GB, refletindo o resultado medido em vez de uma estimativa.
  `Shared/src/aigamekit_shared/data/quality-profiles.yaml`: removidos
  `smooth_iterations`/`low_vram` dos 5 tiers de `rigging3d` (mantido só
  `groups_per_vertex`, que tem equivalente directo — `group_per_vertex` no
  `BpyParser.export`/`transfer_rigging`).
- **`transformers` 5 "otimizado"**: além do bump de versão (Fase 1), aplicados
  patches para eliminar deprecations reais observadas nos logs de execução:
  `torch_dtype=` → `dtype=` (kwarg renomeado no `transformers` 5.x, 2 sítios:
  `tokenrig.py`, `server/spec.py`), acesso a `self.cond_channels` (deprecated
  pelo `diffusers.ConfigMixin`) trocado pelo parâmetro local já disponível em
  `skin_fsq_cvae_model.py`, e `torch.backends.cuda.sdp_kernel` (deprecated)
  trocado por `torch.nn.attention.sdpa_kernel` nos dois módulos Michelangelo
  que ainda o usavam (efeito nulo na 4050 — só disparam no branch `is_use`
  gated por `"H100" in device_name`, nunca verdadeiro aqui — mas zero
  deprecations no output agora).
- **Testes**: `test_extract_paths.py`, `test_stray_mesh_bbox.py`,
  `test_low_vram_skin_config.py`, `test_runner.py`, `test_logits_processor.py`
  removidos (testavam exclusivamente módulos/mecanismos que deixaram de
  existir — extração UniRig, low-vram YAML patch, resolução de path/bash,
  optimização vectorizada do `unirig_ar.py`). `test_cli.py`/
  `test_cli_helpers.py`/`test_rigging_extended.py` reescritos, mantendo o que
  sobrevive (`_rename_generic_bones`, help/version). Novo
  `test_skintokens_runner.py` (checkpoints home, symlink de `configs/`).
  `test_hardware.py` ajustado ao novo limiar (6GB deixa de gerar aviso,
  confirmado pela Fase 0). **66/66 passam**, ruff limpo.
- **Docs**: `pyproject.toml` (deps `timm`/`pyrender`/`wandb` removidas — eram
  UniRig-only, não usadas pelo SkinTokens; `transformers>=4.57.0`/`diffusers`/
  `scipy` formalizados), `README.md` e `THIRD_PARTY.md` reescritos para o novo
  CLI/backend, `AGENTS.md`/`CLAUDE.md` (tabela de packages + nota de
  vendored-code-excluído-do-lint) atualizados. Versão do pacote `0.5.0→0.6.0`.
