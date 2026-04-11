# Do zero ao jogo com IA — playbook (resumo PT)

Documento de apoio ao fluxo **conteúdo gerado + orquestração + agentes de código**. Versão completa em inglês: [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md).

## Três camadas de IA no monorepo

1. **Modelos generativos** — Text2D, Texture2D, Text3D, Paint3D, Text2Sound, Skymap2D, etc.: transformam prompts em ficheiros (PNG, GLB, áudio).
2. **Orquestração** — **GameAssets** (`gameassets batch`): perfil YAML + CSV + presets, chamadas determinísticas a CLIs (`*_BIN`).
3. **Agentes / IDE** — Convenções em [AGENTS.md](../AGENTS.md), contexto do motor em [VibeGame/llms.txt](../VibeGame/llms.txt), skill do GameAssets em `GameAssets/src/gameassets/cursor_skill/SKILL.md`.

## Fluxo recomendado

1. Instalar ferramentas ([INSTALLING_PT.md](INSTALLING_PT.md)).
2. Definir `game.yaml` + `manifest.csv` + presets; opcionalmente `gameassets prompts` antes do batch.
3. Correr `gameassets batch` com as flags necessárias (`--with-3d`, `--with-rig`, **`--with-animate`** para pipeline completo com Animator3D, áudio, etc.).
4. Opcional: validar GLBs com GameDevLab.
5. Copiar outputs para `public/assets/…` (ou `gameassets handoff --public-dir …`) e usar `loadGltfToScene`, `<GLTFLoader url="…">`, ou os exemplos [simple-rpg](../VibeGame/examples/simple-rpg/) (completo) / [hello-world](../VibeGame/examples/hello-world/) (mínimo).
6. Iterar **código e XML** com o assistente, usando `llms.txt` para VibeGame e AGENTS.md para o resto do repositório.

## Pipeline completo de animação

Fluxo de ponta a ponta para uma personagem texturizada, rigada e animada no browser:

**Texto → Imagem → Modelo 3D → Textura → Rig → Animação → Handoff → VibeGame**

### Animator3D após o rig

Depois do **Rigging3D** produzir um GLB rigado, o **Animator3D** grava animações de jogo procedimentais no asset com `game-pack`:

```bash
animator3d game-pack rigged.glb animated.glb --preset humanoid
```

O preset `humanoid` cria cinco clips: **BreatheIdle**, **Walk**, **Run**, **Jump** e **Fall** (no GLB com o prefixo `Animator3D_`; ver abaixo). Outros presets (`creature`, `flying`, …) geram conjuntos diferentes; ver [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md).

### Integração GameAssets

- **`gameassets batch --with-3d --with-rig --with-animate`** corre o pipeline completo, incluindo **Animator3D** game-pack quando o manifesto pede animação (após rig).
- **`gameassets dream`** anima personagens automaticamente: o batch emitido inclui **`--with-animate`** juntamente com `--with-3d` e `--with-rig`.

### VibeGame: personagem animada declarativa

Em vez de código à mão para cada projeto, usar o elemento **`PlayerGLTF`** no XML da cena. Carrega o GLB, reproduz **idle / walk / run** (e estados relacionados quando existem clips) e substitui a caixa por defeito:

```html
<PlayerGLTF pos="0 0 0" model-url="/assets/models/hero.glb"></PlayerGLTF>
```

### Convenção de origem (pés no chão)

- O **Text3D** usa **`origin=feet`** por defeito: base em **Y = 0**, **XZ** centrado na malha.
- O **Paint3D** preserva isso com **`--preserve-origin`** ao pintar para manter o alinhamento.
- O **Rigging3D** valida a origem após o merge para o rig e as animações ficarem assentes no chão.

### Nomes dos clips de animação (game-pack `humanoid`)

No GLB exportado:

- `Animator3D_BreatheIdle`
- `Animator3D_Walk`
- `Animator3D_Run`
- `Animator3D_Jump`
- `Animator3D_Fall`

O runtime (`PlayerGLTF` e sistemas relacionados) mapeia o movimento a estes nomes (ou aliases compatíveis).

## Handoff técnico

Ver [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md) (pastas, URLs, `loadGltfToScene`, `loadGltfAnimated`, `<PlayerGLTF>`). **Animator3D** pós-rig: [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md). Céu equirect: `applyEquirectSkyEnvironment` no VibeGame.

## Entregas recentes (resumo)

| Tema | Onde |
|------|------|
| Animator3D pós-rig | [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md) |
| PMREM / céu | `applyEquirectSkyEnvironment` (`vibegame`) |
| Pack para web | `gameassets handoff` |
| Plano JSON (batch dry-run) | `gameassets batch --dry-run --dry-run-json ficheiro.json` |
| GLB no XML | `<GLTFLoader url="…">` |
| Jogador animado (XML) | `<PlayerGLTF model-url="…">` — idle/walk/run pelo input; ver [Pipeline completo de animação](#pipeline-completo-de-animação) |

## `gameassets dream` — da ideia ao jogo

Novo comando que recebe uma **descrição em linguagem natural**, chama um **LLM** para planear assets e cena, e gera um **projecto Vite jogável** com VibeGame.

```bash
gameassets dream "idle clicker de fazenda, estilo pixel art" --dry-run
```

Fases: Plan (LLM) → Emit (yaml/csv/xml/ts) → Batch (`--with-3d --with-rig --with-animate`) → Sky → Handoff → Scaffold. `--dry-run` gera ficheiros sem GPU. Providers: `openai`, `huggingface`, `stdin`.

Código: `GameAssets/src/gameassets/dream/`.

## Backlog opcional

| Prioridade | Tema |
|------------|------|
| Média | Zip CI de `public/assets` |
| Baixa | `resume --dry-run-json` alinhado ao `batch` |
| Baixa | Refinamento multi-turn do plano no `dream` |
