---
name: gameassets
description: Orquestra batches de prompts e assets 2D/3D/áudio com game.yaml, manifest YAML e presets. Use quando o utilizador falar em GameAssets, manifest, game.yaml, batch, gameassets dream, presets locais, TEXT2D_BIN, TEXTURE2D_BIN, TEXT2SOUND_BIN, TEXT3D_BIN, MATERIALIZE_BIN, RIGGING3D_BIN, ANIMATOR3D_BIN, image_source text2d/texture2d/skymap2d, generate_audio, generate_rig, generate_animate, generate_parts, --no-rig, --no-animate, Rigging3D, Animator3D, campo image_source no manifest, path_layout flat, ou integração Text2D/Texture2D/Text2Sound/Text3D/Materialize.
---

# GameAssets — batch de prompts e assets

## Quando usar

- Gerar **várias** imagens e/ou GLBs a partir de um **manifest YAML** alinhado ao estilo do jogo.
- Trabalhar com **`game.yaml`** + **`manifest.yaml`** + **`presets.yaml`** (e opcionalmente **`presets-local.yaml`**).
- Integrar **Text2D** ou **Texture2D** (2D), opcional **Text2Sound** (áudio por linha), e **Text3D** (3D) sem escrever comandos à mão para cada linha.

## O que é

CLI que combina **perfil** (`game.yaml`), **manifest** (`manifest.yaml`) e **presets** de estilo, e chama **`text2d`** ou **`texture2d generate`** (conforme `image_source` no YAML ou campo **`image_source`** por linha no manifest); se **`generate_audio=true`** na linha, **`text2sound generate`** após a imagem (Fase 1b, antes do 3D); e, se pedires, `text3d` em subprocess. Com **`texture2d.materialize: true`**, corre também o **Materialize** CLI sobre o PNG difuso (mapas PBR em pasta). O batch constrói prompts com `prompt_builder` e aplica opções do perfil.

## Pré-requisitos

| Componente | Notas |
|------------|--------|
| Python | 3.10+ |
| `text2d` | No `PATH` ou `TEXT2D_BIN` quando há linhas com fonte 2D **text2d** |
| `texture2d` | No `PATH` ou `TEXTURE2D_BIN` quando há linhas com fonte **texture2d** |
| `text3d` | Quando 3D está activo (auto-detectado): no `PATH` ou `TEXT3D_BIN` |
| `text2sound` | Com `generate_audio` no manifest (e sem `--skip-audio`): no `PATH` ou `TEXT2SOUND_BIN` |
| Materialize (opcional) | Só **`texture2d.materialize`** (PBR a partir da difusa): `PATH` ou `MATERIALIZE_BIN` / `texture2d.materialize_bin`. O GLB 3D fica PBR via **Paint 2.1** (`paint3d texture`), sem Materialize no mesh. |

## Pipeline mental

```text
game.yaml + manifest.yaml + presets [+ presets-local.yaml]
        → por linha: text2d generate OU texture2d generate (image_source global ou campo do manifest)
              → [se texture2d.materialize] materialize <difusa> -o … (mapas PBR)
        → [se generate_audio e não --skip-audio] text2sound generate …
        → [se generate_3d] text3d generate --from-image … (shape) → paint3d texture … (GLB PBR)
        → [se rig elegível e rigging3d no perfil] rigging3d pipeline (GLB após Text3D → GLB rigado)
        → [se animate elegível] animator3d game-pack (GLB rigado → GLB com clips)
```

**Nota:** O custo de API do Texture2D (Hugging Face Inference) não é calculado pelo GameAssets. O registo **`--log`** inclui **`timings_sec`** (segundos por fase: `image_text2d` / `image_texture2d`, `materialize_diffuse`, `text2sound`, `text3d` ou `text3d_shape` / `paint3d_texture` com `phased_batch` e `text3d.texture`), **`audio_path` / `audio_error`** quando aplicável, e **`texture2d_api`: true** nas linhas geradas via Texture2D.

## Comandos principais

| Comando | Função |
|---------|--------|
| `gameassets init [--path DIR]` | Cria `game.yaml` e `manifest.yaml` de exemplo |
| `gameassets prompts [--profile …] [--manifest …]` | Pré-visualiza prompts (sem GPU); `-o ficheiro.jsonl` grava JSONL |
| `gameassets batch [--profile …] [--manifest …]` | Gera imagens; 3D/rig/animate/parts auto-detectados do manifest + perfil; `--no-rig`/`--no-animate`/`--no-parts` para opt-out; `--dry-run --dry-run-json plan.json` grava plano |
| `gameassets handoff --public-dir …/public` | Copia/symlink GLB/áudio do `output_dir` para `public/assets` e grava `assets/gameassets_handoff.json` |
| `gameassets dream "descrição" [--dry-run]` | Da ideia ao jogo: LLM planeia assets+cena, batch gera, scaffold projecto Vite. `--dry-run` emite ficheiros sem GPU. Providers: `--llm-provider openai\|huggingface\|stdin` |
| `gameassets skill install` | Instala esta skill em `.cursor/skills/gameassets/` do projeto alvo |

**Flags úteis em `batch`:** `--dry-run`, `--fail-fast`, `--skip-audio`, `--no-3d`, `--no-rig`, `--no-animate`, `--no-parts`, `--log run.jsonl` (**`timings_sec`**).

## Preset só no teu ficheiro (`presets-local.yaml`)

Se `style_preset` (ex.: `galaxy_orbital`) **não** existir em `data/presets.yaml` e estiver apenas no teu YAML local, **é obrigatório**:

```bash
gameassets batch --profile game.yaml --manifest manifest.yaml \
  --presets-local presets-local.yaml --log run.jsonl
```

Sem `--presets-local`, o comando falha com **preset desconhecido**.

## Perfil (`game.yaml`) — resumo

- **`style_preset`**, **`output_dir`**, **`path_layout`**: `split` (pastas `images/` e `meshes/`) ou **`flat`** (PNG e GLB na mesma árvore; usa `id` com barra, ex. `Props/caixa_01`).
- **`image_source`**: `text2d` (defeito), `texture2d` ou `skymap2d` — pode ser sobreposto **por linha** no manifest (campo `image_source`).
- **`text2d`**: `low_vram`, `cpu`, `width`, `height` (resolução 2D).
- **`texture2d`**: opções do CLI seamless + **`materialize`** para PBR a partir da difusa (mapas em `materialize_maps_subdir`).
- **`text2sound`** (opcional): `duration`, `steps`, `cfg_scale`, `audio_format`, etc. — ver Text2Sound; `audio_subdir` no perfil para destino relativo a `output_dir`.
- **`text3d`**: `preset` (`fast` \| `balanced` \| `hq`), `low_vram`, `texture` (omitido = **`true`**), ou **Hunyuan explícito** (`steps`, `octree_resolution`, `num_chunks` — nesse caso não se passa `--preset` ao CLI), `mc_level`, `phased_batch`, GPU (`allow_shared_gpu`, `full_gpu`, …). PBR no GLB: **Paint 2.1** — ver `Text3D/docs/PBR_MATERIALIZE.md`.
  - **Paint3D tuning:** `paint_max_views`, `paint_view_resolution`, `paint_render_size`, `paint_texture_size`, `paint_bake_exp` (default 6 — costuras mais nítidas).
- **`rigging3d`** (opcional): `output_suffix`, `root`, `python` — presença activa rig para personagens; combina com `generate_rig` no manifest.
- **`animator3d`** (opcional): `preset` (`humanoid`, …) — presença activa animação automática após rig.

**Atenção:** `text3d.low_vram: true` em GPU faz o Hunyuan “shape” cair para CPU e **costuma degradar a forma**; preferir reduzir resolução 2D ou fechar outras apps que consumam VRAM.

## Manifest (`manifest.yaml`)

Campos incluem **`id`**, **`idea`**, **`generate_3d`**, opcionalmente **`generate_audio`**, **`generate_rig`**, **`generate_animate`**, **`generate_parts`**, **`image_source`** (`text2d` \| `texture2d` \| `skymap2d`), etc. (ver `manifest.py`). Com `path_layout: flat`, `id` pode ser `Categoria/nome` para espelhar pastas no Godot.

## Variáveis de ambiente

| Variável | Função |
|----------|--------|
| `TEXT2D_BIN` | Caminho ao executável `text2d` se não estiver no `PATH` |
| `TEXTURE2D_BIN` | Caminho ao executável `texture2d` se não estiver no `PATH` |
| `TEXT3D_BIN` | Idem para `text3d` |
| `TEXT2SOUND_BIN` | Idem para `text2sound` quando há `generate_audio` |
| `MATERIALIZE_BIN` | Idem para `materialize` (só fluxo Texture2D + `texture2d.materialize`) |
| `RIGGING3D_BIN` | Idem para `rigging3d` (ou `python -m rigging3d`) quando rig activo |
| `ANIMATOR3D_BIN` | Idem para `animator3d` quando animate activo |
| `PART3D_BIN` | Idem para `part3d` quando parts activo |

## Prompt — palavras a evitar para 3D limpo

Quando `generate_3d=true`, a imagem 2D alimenta o Hunyuan3D. Sombras e iluminação direcional na imagem viram **placas/discos** no mesh 3D. O `prompt_builder` já injeta iluminação flat e negativos, mas a **`idea`** no manifest também importa.

### Na coluna `idea` do manifest, **EVITAR**:

| Categoria | Termos tóxicos |
|-----------|---------------|
| **Posição/chão** | "on the ground", "on the floor", "on a pedestal", "standing on", "sitting on" |
| **Sombras** | "contact shadow", "drop shadow", "ground shadow" |
| **Iluminação** | "dramatic lighting", "harsh lighting", "rim light", "spotlight", "volumetric light", "backlit" |
| **Flutuação** | "floating" (trigger de sombra de flutuação) |

### Na coluna `idea`, **PREFERIR**:

- Descrever **o quê**, não **como iluminar**: "medieval sword with runes" em vez de "medieval sword with dramatic lighting"
- Cores e materiais explícitos: "red dragon with golden wings" em vez de "dragon"
- Evitar referências a superfícies: "robot warrior" em vez de "robot warrior standing on a platform"

O sistema de prompt enhancement (v2) envolve automaticamente o prompt com enquadramento de render flat (iluminação uniforme, fundo branco infinito, sem sombras).

## Armadilhas frequentes

| Sintoma | O que verificar |
|---------|------------------|
| `Preset desconhecido` | Passar `--presets-local` com o YAML onde o preset está definido. |
| GLB não gerado | `generate_3d` falso na linha, ou `--no-3d` activo. |
| Rig não aplicado | Sem bloco `rigging3d` no perfil e sem `generate_rig` na linha; ou `--no-rig` activo. Precisa de GLB do Text3D primeiro. |
| Sem animação após rig | Sem bloco `animator3d` no perfil e sem `generate_animate` na linha; ou `--no-animate` activo; ou `animator3d` ausente (`ANIMATOR3D_BIN` / `PATH`). |
| OOM no Text2D | Reduzir resolução em `text2d` no perfil; fechar **Godot** ou outros processos na GPU (`nvidia-smi`). |
| Qualidade 3D pior que no Text3D “isolado” | Mesmo `game.yaml` e mesmas flags; comparar preset/steps e VRAM livre. |

## Ferramentas relacionadas (monorepo GameDev)

| Ferramenta | Papel |
|------------|--------|
| **Text2D** | Imagens gerais (FLUX) por prompt; referência para 3D. |
| **Texture2D** | Texturas seamless (HF API); opcional + Materialize para mapas PBR em disco. |
| **Text2Sound** | Text-to-audio (Stable Audio Open); clipes por linha com `generate_audio`. |
| **Text3D** | Imagem → mesh (shape); com GameAssets + `text3d.texture` segue-se `paint3d texture` (GLB PBR). |
| **Materialize** | Mapas PBR a partir do diffuse (CLI; após Texture2D com `texture2d.materialize`). |
| **Rigging3D** | Rig automático do GLB (após Text3D); auto-activado para personagens quando bloco `rigging3d` no perfil. |

## Referências no repositório

- `src/gameassets/cli.py` — CLI e epílogo de exemplos
- `src/gameassets/prompt_builder.py` — composição de prompts
- `src/gameassets/data/presets.yaml` — presets base
- `README.md` — documentação de utilizador
