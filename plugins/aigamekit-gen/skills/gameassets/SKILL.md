---
name: gameassets
description: Orquestração de batch de assets com o CLI gameassets do AiGameKit — batch/resume por manifest (game.yaml + manifest.yaml), dream (ideia → jogo com plano LLM), handoff para ViteGame, validate, doctor e mesh reorigin-feet. Usar quando o pedido for gerar um lote de assets (imagens + meshes + rig + animação) ou um jogo completo a partir de uma descrição, em vez de chamar as tools uma a uma.
---

# GameAssets — batch de assets + dream (ideia → jogo)

Orquestrador: lê `game.yaml` (perfil) + `manifest.yaml` (uma linha por asset) e encadeia as tools (Text2D/Text3D/Paint3D/Rigging3D/Animator3D/…) via subprocessos com waves geridas pelo vramd. **Não contém código de mesh** — toda a operação de mesh é delegada ao `text3d`.

## Resolver o binário

```bash
GA=$(command -v gameassets || echo "$HOME/GitClones/AiGameKit/GameAssets/.venv/bin/gameassets")
```

## Fluxo batch (lote de assets)

```bash
gameassets init                                   # cria game.yaml + manifest.yaml de exemplo
gameassets batch --profile game.yaml --manifest manifest.yaml --plain
gameassets validate --profile game.yaml --manifest manifest.yaml
gameassets handoff --public-dir public/           # GLB/áudio → public/assets + handoff.json
```

Para agentes/headless: `--plain` (batch) e `--no-dashboard` (resume) trocam a TUI por texto simples.

| Flag | Efeito |
|------|--------|
| `--dry-run --dry-run-json plan.json` | Plano (fases + argv por linha) sem GPU — ideal para agentes/CI |
| `--no-3d` / `--no-rig` / `--no-animate` / `--no-lod` / `--no-collision` | Opt-out por stage (as stages são auto-detectadas do manifest + game.yaml) |
| `--no-terrain` / `--no-skymap` / `--no-icons` / `--skip-text2d` / `--skip-audio` | Opt-out por feature |
| `--force` | Regenerar mesmo com outputs existentes |
| `--fail-fast` | Parar no primeiro erro (defeito: continuar) |
| `--gpu-ids 0,1` | Multi-GPU (auto-deteta via NVML quando omitido) |
| `--vramd-stream` / `--no-vramd` | Eventos vramd / bypass do supervisor |

### resume (idempotente)

```bash
gameassets resume --profile game.yaml --manifest manifest.yaml --no-dashboard
```

Analisa o estado de cada asset e corre **só as fases pendentes** (work-dir `.gameassets_work/`). `--redo-split` invalida artefactos de split (stump/top/LOD/collision) e refaz a partir do painted. Preferir a `batch --force` sempre que houver trabalho parcial.

## dream — ideia → jogo completo

```bash
gameassets dream create "um RPG de fazenda com goblins" --output-dir meu-jogo --llm-provider ollama --dry-run
```

Plano LLM → `game.yaml`/`manifest.yaml`/`world.xml`/projeto Vite → batch + terreno + céu + ícones + handoff. `--dry-run` gera os ficheiros sem GPU. Providers: `openai|huggingface|ollama|stdin` (ollama = local, zero-key). `--seed N` determinístico; planos são cacheados por descrição+flags (`--replan` força). Iterar/auditar:

```bash
gameassets dream refine plan.json "add a dragon"   # LLM edita o plano (.bak, seeds preservados)
gameassets dream explain plan.json [--json]        # exit 1 em lint errors — CI-ready
```

## Utilitários

```bash
gameassets doctor                                  # primeiro uso: tools, vramd/GPU, compressão GLB, LLM, disco
gameassets info                                    # binários resolvidos + VRAM livre (NVML)
gameassets prompts --profile game.yaml --manifest manifest.yaml   # prompts finais sem GPU
gameassets mesh reorigin-feet asset.glb            # origem nos pés (base Y=0, centro XZ)
gameassets debug …                                 # atalho para o aigamekit-lab (screenshots/inspect/compare)
```

## Manifest (linhas do manifest.yaml)

Caminho feliz = `category` + `size_m` (sem `octree_resolution`): faces ≈ 8·10⁴·char_m². Guia completo: `docs/MANIFEST_AUTHORING.md` no monorepo.

## Regras

- **LOD0 é o entregável**: animated > rigged > painted (o batch auto-detecta o estágio terminal). LOD0 sem rig/animação quando o asset os tem = regressão.
- Regressão visual → `aigamekit-lab debug compare` com screenshots antes/depois.
- VRAM: waves geridas pelo vramd; nunca kill/pkill GPU com o supervisor ocupado (`vramd status`/`queue` primeiro).
- Logs JSONL: `--log batch.jsonl` (batch) e `~/.cache/aigamekit/logs/gameassets-AAAA-MM-DD.log`.
