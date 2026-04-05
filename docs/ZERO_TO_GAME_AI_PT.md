# Do zero ao jogo com IA — playbook (resumo PT)

Documento de apoio ao fluxo **conteúdo gerado + orquestração + agentes de código**. Versão completa em inglês: [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md).

## Três camadas de IA no monorepo

1. **Modelos generativos** — Text2D, Texture2D, Text3D, Paint3D, Text2Sound, Skymap2D, etc.: transformam prompts em ficheiros (PNG, GLB, áudio).
2. **Orquestração** — **GameAssets** (`gameassets batch`): perfil YAML + CSV + presets, chamadas determinísticas a CLIs (`*_BIN`).
3. **Agentes / IDE** — Convenções em [AGENTS.md](../AGENTS.md), contexto do motor em [VibeGame/llms.txt](../VibeGame/llms.txt), skill do GameAssets em `GameAssets/src/gameassets/cursor_skill/SKILL.md`.

## Fluxo recomendado

1. Instalar ferramentas ([INSTALLING_PT.md](INSTALLING_PT.md)).
2. Definir `game.yaml` + `manifest.csv` + presets; opcionalmente `gameassets prompts` antes do batch.
3. Correr `gameassets batch` com as flags necessárias (`--with-3d`, `--with-rig`, áudio, etc.).
4. Opcional: validar GLBs com GameDevLab.
5. Copiar outputs para `public/assets/…` (ou `gameassets handoff --public-dir …`) e usar `loadGltfToScene`, `<gltf-load url="…">`, ou o exemplo [monorepo-game](../VibeGame/examples/monorepo-game/).
6. Iterar **código e XML** com o assistente, usando `llms.txt` para VibeGame e AGENTS.md para o resto do repositório.

## Handoff técnico

Ver [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md) (pastas, URLs, `loadGltfToScene`). **Animator3D** pós-rig: [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md). Céu equirect: `applyEquirectSkyEnvironment` no VibeGame.

## Entregas recentes (resumo)

| Tema | Onde |
|------|------|
| Animator3D pós-rig | [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md) |
| PMREM / céu | `applyEquirectSkyEnvironment` (`vibegame`) |
| Pack para web | `gameassets handoff` |
| Plano JSON (batch dry-run) | `gameassets batch --dry-run --dry-run-json ficheiro.json` |
| GLB no XML | `<gltf-load url="…">` |

## `gameassets dream` — da ideia ao jogo

Novo comando que recebe uma **descrição em linguagem natural**, chama um **LLM** para planear assets e cena, e gera um **projecto Vite jogável** com VibeGame.

```bash
gameassets dream "idle clicker de fazenda, estilo pixel art" --dry-run
```

Fases: Plan (LLM) → Emit (yaml/csv/xml/ts) → Batch → Sky → Handoff → Scaffold. `--dry-run` gera ficheiros sem GPU. Providers: `openai`, `huggingface`, `stdin`.

Código: `GameAssets/src/gameassets/dream/`.

## Backlog opcional

| Prioridade | Tema |
|------------|------|
| Média | Zip CI de `public/assets` |
| Baixa | `resume --dry-run-json` alinhado ao `batch` |
| Baixa | Refinamento multi-turn do plano no `dream` |
