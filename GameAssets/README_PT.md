# GameAssets

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI para **batches de prompts e assets** alinhados ao estilo e à ideia do teu jogo. Combina um perfil YAML (`game.yaml`), um manifest CSV e presets de estilo, e orquestra **`text2d`** ou **`texture2d`** (texturas seamless via API), opcionalmente **`text2sound`** (áudio por linha), **`text3d`** (só geometria), **`paint3d`** (Hunyuan3D-Paint 2.1 — textura + PBR no GLB com `text3d.texture`) e **Materialize** só para **mapas PBR a partir da imagem difusa** no fluxo Texture2D (`texture2d.materialize`).

## Requisitos

- Python 3.10+
- Comandos no `PATH` conforme o fluxo (instala os pacotes nos respetivos ambientes) ou variáveis de ambiente:
  - `TEXT2D_BIN` — executável `text2d` ([Text2D](../Text2D)) quando usas geração 2D **FLUX** (`image_source: text2d` ou coluna `image_source` por linha)
  - `TEXTURE2D_BIN` — executável `texture2d` ([Texture2D](../Texture2D)) quando usas **texturas seamless** (`image_source: texture2d` ou por linha no CSV)
  - `TEXT3D_BIN` — executável `text3d` ([Text3D](../Text3D)) com `--with-3d` (gera só o shape)
  - `PAINT3D_BIN` — executável `paint3d` ([Paint3D](../Paint3D)) quando no `game.yaml` tiveres **`text3d.texture: true`** (o batch chama `paint3d texture` após o shape; o GLB já sai PBR do Paint 2.1)
  - `TEXT2SOUND_BIN` — executável `text2sound` ([Text2Sound](../Text2Sound)) quando há linhas com **`generate_audio=true`** no CSV (e não usas `--skip-audio`)
  - `MATERIALIZE_BIN` — opcional; **mapas PBR a partir da difusa** com Texture2D + `texture2d.materialize` (ver [Materialize](../Materialize) e [Text3D/docs/PBR_MATERIALIZE.md](../Text3D/docs/PBR_MATERIALIZE.md))
  - `PART3D_BIN` — executável `part3d` ([Part3D](../Part3D)) com **`--with-parts`** e coluna **`generate_parts=true`** no CSV (decomposição semântica após o GLB do Text3D)
  - `ANIMATOR3D_BIN` — executável `animator3d` ([Animator3D](../Animator3D)) com **`--with-animate`** após rig com sucesso (`animator3d game-pack`; coluna opcional **`generate_animate`** no CSV — ver secção do batch abaixo)

## Debug / laboratório

Debug visual de GLB (screenshots, inspect, compare, bundle) está em **[GameDevLab](../GameDevLab)** (`gamedev-lab debug …`), não no `gameassets`.

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório GameDev:

```bash
cd /caminho/para/GameDev
./install.sh gameassets
```

Guia geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento

O projeto mantém um **venv** local em `GameAssets/.venv`, como Text2D e Text3D.

```bash
cd GameDev/GameAssets
chmod +x scripts/setup.sh activate.sh
./scripts/setup.sh
source .venv/bin/activate
gameassets --help
```

Opções do script:

| Opção | Efeito |
|--------|--------|
| *(default)* | Cria `.venv` se não existir; `pip install -e .` |
| `--recreate` | Apaga e recria o `.venv` |
| `--dev` | Instala também extras de desenvolvimento (`pytest` via `pip install -e ".[dev]"`) |

**Ativar o ambiente** em cada terminal:

```bash
source /caminho/para/GameDev/GameAssets/.venv/bin/activate
```

O ficheiro `activate.sh` segue o padrão do Text2D: corre um comando já com o venv ativo, por exemplo:

```bash
./activate.sh gameassets prompts --profile game.yaml --manifest manifest.csv
```

**Dependências:** listadas em [`config/requirements.txt`](config/requirements.txt) (instaladas automaticamente pelo `setup.py` ao fazer `pip install -e .`). Desenvolvimento: [`config/requirements-dev.txt`](config/requirements-dev.txt) ou `./scripts/setup.sh --dev`.

## Fluxo em 3 passos

| Subcomando | Descrição |
|-----------|-----------|
| `gameassets init` | Cria `game.yaml` e `manifest.csv` numa pasta |
| `gameassets prompts` | Pré-visualiza prompts sem gerar imagens |
| `gameassets batch` | Gera imagens (e opcionalmente 3D/áudio) em batch |
| `gameassets handoff` | Copia/symlink do `output_dir` para `public/assets` e grava `assets/gameassets_handoff.json` |
| `gameassets dream` | Da ideia ao jogo com IA: LLM planeia assets+cena, batch gera, scaffold projecto Vite |
| `gameassets info` | Mostra configuração, binários detetados, e ambiente |
| `gameassets skill install` | Instala Agent Skill Cursor no projeto |

### 1. Inicializar

```bash
gameassets init --path ./meu_jogo
cd meu_jogo
```

Isto cria `game.yaml` (perfil) e `manifest.csv` (lista de assets).

### 2. Rever prompts (sem GPU)

```bash
gameassets prompts --profile game.yaml --manifest manifest.csv
```

Ou gravar JSONL:

```bash
gameassets prompts -o prompts.jsonl --profile game.yaml --manifest manifest.csv
```

### 3. Gerar imagens (e opcionalmente 3D)

Só **2D**:

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

**2D + 3D** onde `generate_3d=true` no CSV:

```bash
gameassets batch --profile game.yaml --manifest manifest.csv --with-3d
```

**Preset personalizado** (chave só no teu `presets-local.yaml`, não em `data/presets.yaml`): tens de passar **`--presets-local caminho.yaml`**, caso contrário o comando falha com `Preset desconhecido`.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv --with-3d \
  --presets-local presets-local.yaml --log run.jsonl
```

```bash
# Multi-GPU batch: auto-detetar GPUs e dividir entre elas
gameassets batch --profile game.yaml --manifest manifest.csv --gpu-ids 0,1
```

- `--with-3d`/`--no-3d` é um flag tri-state (auto-deteta da coluna `generate_3d` por defeito). O mesmo para `--with-rig`/`--no-rig`, `--with-parts`/`--no-parts`, `--with-animate`/`--no-animate` — auto-deteção do manifest quando não especificado. Omite o flag para auto-detetar, ou usa `--no-*` para desativar explicitamente.
- Com **`--with-3d`** e **`--with-rig`**, linhas com **`generate_rig=true`** (e GLB do Text3D gerado com sucesso) chamam o **Rigging3D**; o GLB rigado aparece no log em **`rig_mesh_path`** (sufixo configurável em `rigging3d.output_suffix` no `game.yaml`). Requer **`RIGGING3D_BIN`** ou `rigging3d` no `PATH`.
- Com **`--with-3d`**, **`--with-rig`** e **`--with-animate`**, após rig com sucesso o batch corre **`animator3d game-pack`** quando a linha pede animação: **`generate_animate=true`**, ou **`generate_rig=true`** com **`--with-rig`** (perfil Animator3D no bloco opcional **`animator3d`** no `game.yaml`, preset por defeito `humanoid`). Requer **`ANIMATOR3D_BIN`** ou `animator3d` no `PATH`. Ver [Animator3D após rig](../docs/ANIMATOR3D_AFTER_RIG.md).
- Com **`--with-3d`** e **`--with-parts`**, linhas com **`generate_parts=true`** chamam o **Part3D** (`part3d decompose`) sobre o GLB do Text3D **antes** do rig: saídas **`parts_mesh_path`** (cena multi-parte) e **`segmented_mesh_path`** (malha com cores por parte), junto ao GLB principal; opções em **`part3d`** no `game.yaml`. Requer **`PART3D_BIN`** ou `part3d` no `PATH`.
- `--dry-run` mostra os comandos sem executar.
- `--fail-fast` para no primeiro erro (defeito: continua).
- `--log batch-log.jsonl` acrescenta um JSON por linha processada, incluindo **`timings_sec`** (segundos, tempos de parede por subprocesso quando aplicável), por exemplo: `image_text2d` ou `image_texture2d`, `materialize_diffuse`, `text2sound` (quando `generate_audio`), `text3d` (passo único), ou `text3d_shape` / `paint3d_texture` (com `phased_batch` e `text3d.texture`). Registos incluem **`audio_path`** / **`audio_error`** quando aplicável. Linhas **Texture2D** incluem **`texture2d_api`: true** (custo da API Hugging Face não é calculado pelo GameAssets).
- **Lock exclusivo:** na pasta do manifest é criado `.gameassets_batch.lock` (ficheiro `fcntl`) para **impedir dois batches na mesma pasta** — evita disputa de VRAM entre `text2d`/`text3d` em paralelo. Se o PID no lock já não existir, o lock é recuperado. `--skip-batch-lock` desliga (avançado).
- **VRAM:** antes da execução, se `nvidia-smi` existir e a VRAM livre for inferior a ~1,8 GiB, mostra-se um aviso. `--skip-gpu-preflight` desliga o aviso.
- **CUDA:** os subprocessos `text2d`/`text3d` recebem `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` se a variável ainda não estiver definida no ambiente (reduz falhas por fragmentação).
- **Multi-GPU:** `--gpu-ids 0,1` auto-deteta as GPUs disponíveis via `nvidia-smi` (ou aceita IDs explícitos separados por vírgula) e propaga `CUDA_VISIBLE_DEVICES` e `--gpu-ids` a todos os sub-tools (text2d, text3d, paint3d, part3d, rigging3d, animator3d).

### Text2Sound (`generate_audio`)

- No CSV, coluna opcional **`generate_audio`** (`true`/`false`): quando `true`, após a imagem 2D (Fase 1) corre **Text2Sound** (Fase 1b) antes do Text3D.
- Perfil: `audio_subdir` (defeito `audio`) e bloco opcional **`text2sound`** (duração, passos, formato `wav`/`flac`/`ogg`, etc.) — ver [Text2Sound](../Text2Sound).
- **`--skip-audio`:** ignora a coluna e não invoca `text2sound`.
- **`prompts`** inclui `prompt_audio` e `generate_audio` no JSONL / pré-visualização.

### Text2D vs Texture2D (`image_source`)

No **`game.yaml`**, **`image_source`** escolhe a ferramenta de imagem por defeito:

| Valor | Ferramenta | Notas |
|-------|--------------|--------|
| `text2d` (defeito) | FLUX Klein — imagens gerais, referência para 3D | Consome VRAM local; bloco `text2d` no YAML |
| `texture2d` | [Texture2D](../Texture2D) — texturas **seamless** (HF Inference API) | Pouca VRAM local; bloco `texture2d` no YAML (resolução, `materialize` para PBR em ficheiros separados, etc.) |

**Por linha no CSV:** coluna opcional **`image_source`** (`text2d` ou `texture2d`) sobrepõe o defeito do perfil para essa linha (útil para misturar *props* com FLUX e *tiles* com Texture2D no mesmo manifest).

Variável **`TEXTURE2D_BIN`** se `texture2d` não estiver no `PATH`.

### PBR no GLB (Paint3D 2.1)

Com **`text3d` no `game.yaml`** e **`texture: true`**, o batch corre **`paint3d texture`** sobre o GLB do shape (e a imagem de referência). O **Hunyuan3D-Paint 2.1** gera um **GLB PBR**; o **Materialize CLI não entra** no fluxo 3D.

Exemplo mínimo (com `--with-3d` e linhas `generate_3d=true`):

```yaml
text3d:
  preset: fast
  texture: true
```

Contexto e Texture2D + Materialize: [Text3D/docs/PBR_MATERIALIZE.md](../Text3D/docs/PBR_MATERIALIZE.md).

## Perfil (`game.yaml`)

Campos principais:

| Campo | Descrição |
|-------|-----------|
| `title`, `genre`, `tone` | Metadados do perfil; **o título não vai para o prompt de imagem** (evita texto/logótipo no PNG). Género e tom definem ambiente |
| `style_preset` | Chave em `src/gameassets/data/presets.yaml` (`lowpoly`, `pixel_art`, …) |
| `negative_keywords` | Lista extra de restrições (“Avoid: …”) |
| `output_dir` | Raiz de saída (defeito **`.`** → `./images/` e `./meshes/` sem pasta extra `outputs/`) |
| `path_layout` | `split` (defeito) ou `flat` — ver abaixo |
| `images_subdir` / `meshes_subdir` | Usados em `split`: subpastas para PNG/JPG e GLB |
| `image_ext` | `png` ou `jpg` |
| `seed_base` | Opcional; seeds derivados por `id` para reprodutibilidade |
| `image_source` | `text2d` (defeito), `texture2d` ou `skymap2d` — ferramenta de imagem por defeito (sobreponível por coluna no CSV) |
| `text2d` | Bloco opcional: `low_vram`, `cpu`, `width`, `height` |
| `texture2d` | Bloco opcional se usas Texture2D (global ou só com linhas CSV `texture2d`): resolução, `steps`, `guidance_scale`, `preset`, … e **PBR em difusa:** `materialize`, `materialize_maps_subdir`, `materialize_bin`, `materialize_format`, etc. |
| `text3d` | Bloco opcional: `preset`, `low_vram`, `texture` (omitido = **`true`**), `steps` / `octree_resolution` / `num_chunks` (alternativa mútua a `preset`), `no_mesh_repair`, `mesh_smooth`, `mc_level`, `phased_batch`, `allow_shared_gpu`, `gpu_kill_others`, `full_gpu`, `model_subfolder`. **Tuning Paint3D:** `paint_max_views`, `paint_view_resolution`, `paint_render_size`, `paint_texture_size`, `paint_bake_exp` (defeito 6 — costuras mais nítidas) |
| `rigging3d` | Bloco opcional (rig após Text3D): `output_suffix` (ex. `_rigged`), `root` (código do pacote Rigging3D), `python` (interprete). Usado com `batch --with-rig` e linhas `generate_rig=true` |
| `animator3d` | Bloco opcional (**Animator3D** após rig): `preset` (`humanoid` \| `creature` \| `flying`, …). Usado com `batch --with-rig --with-animate` e linhas elegíveis para animação (ver bullets do batch). Requer `animator3d` no `PATH` ou `ANIMATOR3D_BIN` |
| `part3d` | Bloco opcional (Part3D após Text3D, antes do rig): `steps`, `octree_resolution`, `num_chunks`, `segment_only`, `no_cpu_offload`, `verbose`, `parts_suffix`, `segmented_suffix`. Usado com `batch --with-parts` e linhas `generate_parts=true` |

Todos os sub-tools também aceitam `--gpu-ids` propagado pelo comando batch.

### Hunyuan3D e qualidade

Com `text3d.low_vram: true` e GPU CUDA, o **Text3D** envia o Hunyuan3D shape para **CPU** (evita OOM em ~6 GB), mas a **forma** costuma degradar muito (malhas blocosas). Para assets de jogo sérios, usa **`low_vram: false`** com `preset: balanced` ou `fast` na GPU e fecha outras aplicações que usem VRAM (ex.: editor Godot).

### Layout de pastas (`path_layout`)

- **`split`** — `output_dir/images_subdir/<id>.png` e `output_dir/meshes_subdir/<id>.glb`. O `id` pode incluir subpastas (ex. `Props/crate_01`).
- **`flat`** — `output_dir/<dir do id>/<nome>.png` e o mesmo diretório para `<nome>.glb`. Ex.: `id` = `Collectibles/core` → `output_dir/Collectibles/core.png` e `Collectibles/core.glb`. Adequado para **uma pasta por categoria** no Godot, sem ramos separados `images/` e `meshes/`.

Podes criar `presets.local.yaml` ao lado do perfil e passar `--presets-local presets.local.yaml` para fundir presets personalizados.

## Manifest (`manifest.csv`)

Cabeçalhos: **`id`**, **`idea`** (obrigatórios); opcionais: **`kind`** (`prop`, `character`, `environment`), **`generate_3d`**, **`generate_audio`**, **`generate_rig`** (`true`/`false`/… — rig do GLB após Text3D, com `batch --with-rig`), **`generate_animate`** (`true`/`false`/… — correr **Animator3D** após rig com `batch --with-animate`; se omitido, linhas rigadas ainda podem animar com **`--with-rig`** ativo), **`generate_parts`** (`true`/`false`/… — decomposição Part3D após Text3D, com `batch --with-parts`), **`image_source`** (`text2d` \| `texture2d` \| `skymap2d`) para sobrepor o `image_source` do `game.yaml` nessa linha. Com `path_layout: flat`, usa `id` com barra, por exemplo `Crystals/shard_blue`, para gravar ficheiros dentro de `Crystals/`.

## Estrutura

```
GameAssets/
├── src/gameassets/
│   ├── cli.py             # CLI Click (init, prompts, batch, handoff, dream, info, …)
│   ├── profile.py         # Parsing do game.yaml
│   ├── manifest.py        # Parsing do manifest.csv
│   ├── prompt_builder.py  # Construção de prompts com perfil + preset
│   ├── runner.py          # Execução de subprocessos (text2d, text3d, etc.)
│   ├── presets.py         # Carregamento de presets YAML
│   ├── templates.py       # Templates de prompt
│   ├── batch_guard.py     # Lock exclusivo + VRAM preflight
│   └── data/presets.yaml  # Presets embutidos
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   └── setup.sh           # Setup do venv + deps
└── tests/
```

## Variáveis de Ambiente

| Variável | Descrição |
|----------|-----------|
| `TEXT2D_BIN` | Caminho para o binário `text2d` (se não estiver no `PATH`) |
| `TEXTURE2D_BIN` | Caminho para o binário `texture2d` |
| `TEXT3D_BIN` | Caminho para o binário `text3d` |
| `PAINT3D_BIN` | Binário `paint3d` quando o perfil tem `text3d.texture` |
| `TEXT2SOUND_BIN` | Caminho para o binário `text2sound` |
| `MATERIALIZE_BIN` | Caminho para o binário `materialize` (só com Texture2D + `texture2d.materialize`) |
| `RIGGING3D_BIN` | Caminho para `rigging3d` (ou `python -m rigging3d`) quando usas `batch --with-rig` |
| `ANIMATOR3D_BIN` | Caminho para `animator3d` com `batch --with-rig --with-animate` |
| `PART3D_BIN` | Caminho para `part3d` (ou `python -m part3d`) quando usas `batch --with-parts` |
| `PYTORCH_CUDA_ALLOC_CONF` | Auto-definida como `expandable_segments:True` se vazia (reduz fragmentação CUDA) |

## Licença

- **Código:** MIT (alinhado ao resto do monorepo).
- **Modelos invocados** (`text2d`, `texture2d`, `skymap2d`, `text2sound`, `text3d`, `part3d`, `rigging3d`): cada ferramenta descarrega ou usa pesos com licenças próprias (FLUX, Tencent Hunyuan, Stability Audio, UniRig, etc.). **Não** confundir a MIT do `gameassets` com a licença dos checkpoints. Tabela e notas: [README do monorepo — Licenças](../README_PT.md).
