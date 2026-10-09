# Changelog

Notáveis por versão do monorepo AiGameKit. Formato [Keep a Changelog](https://keepachangelog.com/);
as tags `v<N>` são snapshots do monorepo — cada package mantém a sua própria versão
(`pyproject.toml` / `Cargo.toml` / `package.json`). Pacotes individuais com changelog
próprio: [`Materialize/CHANGELOG.md`](Materialize/CHANGELOG.md).

## [v0.6.0] — 2026-10-08

Foco: **zero friction** — instalação num comando, primeiro uso verificado, docs de
entrada a bater certo com a CLI e release por tag.

### Added — instalação e primeiro uso sem atrito

- **Pre-flight de pré-requisitos** (`scripts/preflight.py`, stdlib, Linux+Windows):
  valida Python, Node ≥ 20.12, Bun, cargo, GPU, disco e 7-Zip **antes** de instalar;
  quando falta algo obrigatório, pára uma única vez com todos os comandos prontos a
  copiar (apt/nodesource/rustup/bun no Linux, winget no Windows). `--json` para
  agentes; bypass `AIGAMEKIT_PREFLIGHT=0`.
- **`./install.sh` sem argumentos instala o perfil core** (10 tools, zero-a-jogo).
  Perfis `core|examples|--all` mantidos; `--list` mostra perfis e ferramentas;
  perfis **continuam depois de falhas** com resumo OK/FAILED + retry; no fim correm
  o doctor e imprimem os próximos passos. `install.ps1` com o mesmo fluxo.
- **`gameassets doctor`** — check único de primeiro uso: tools do perfil, extras do
  dream, vramd+GPU, compressão GLB (agrega `text3d doctor`), Node/Bun, provider LLM
  (OPENAI_API_KEY/ollama), HF token e disco; veredicto `READY` com o próximo comando;
  `--json`, exit 1 em falha.
- **Dream com pré-checks**: `dream create/refine` avisam no arranque quando o plano
  vai sair em fallback (sem chave LLM/ollama) ou quando sky/terreno/áudio vão ser
  saltados (com o `./install.sh <tool>` e a flag `--no-*` certa em cada linha).
- **Aviso de primeiro download**: o full-run do dream avisa antes de começar que a
  primeira vez descarrega ~30 GB de pesos (20–40 min, GPU a 0% durante o download).
- **Erros acionáveis**: `batch`/`prompts` sem `game.yaml` apontam `gameassets init`;
  tools em falta a meio do fluxo dizem `./install.sh <tool>`; fixes do doctor e do
  dream pre-flight são cross-platform (`./install.sh` vs `.\install.ps1`).
- **Release por tag**: `release.yml` dispara em `push: tags v*` e cria a GitHub
  Release com notes (npm publish fica opt-in até `NPM_TOKEN` estar configurado).
- Perfis de instalação `core|examples|--all` (estudo de uso real:
  ~42 GB recuperáveis nos 3 extras não usados pelos exemplos) e
  `make dedupe-venvs` (hardlinks por blake2b; 126 → 28 GB no clone de /media).

### Changed — Viber (track nativa)

- **Bevy 0.20.0 oficial (crates.io)**: os 22 patches git do rc.2 foram removidos e
  toda a árvore resolve ao registry; forks do ecossistema (bevy_rapier3d,
  rerecast/landmass) continuam vendored até os upstreams publicarem versões
  bevy-0.20. Clippy `-D warnings` limpo (202 avisos do port resolvidos).
- **Port para Bevy 0.20.0-rc.2**: WESL substitui o naga_oil; crates do ecossistema
  ficam vendored.
- **simple-rpg completo no Viber** (10 loops de port): combate, quests, economia,
  menus, travel, save/load, skills, mundo vivo, loot em ouro/XP de chefe/caçadas,
  rain-scale no deserto, física Rapier declarativa, HUD AAA (Cinzel, menu Q,
  profiler), sandbox Luau com orçamento de CPU/heap e API `viber.*`/`viber.ui.*`,
  terrain declarativo (Terrain/Lake/River/RoadNetwork + pads), debug bridge BRP
  (screenshot/input/tree/logs), `<Composition>`/`<Prototype>`/`<Use>` e consumidores
  `EngineConfig` (`NavMesh`, `SpawnGate`, `ProjectileTemplate`,
  `AdaptiveQuality`, `PostFxDebugToggle`).

### Changed — pipeline de assets

- **Paint3D**: PBR-enrich ON por default — normais + AO via Materialize sobre o
  albedo, presets por material e export GLB com maps (fix do MR descartado);
  upscale unificado no Real-ESRGAN_x4plus único; group offload + streams no
  envelope mem-eff 1024/2048.
- **Text2D/Texture2D/Skymap2D**: group offload + CUDA streams por default em GPUs
  apertadas (gate de folga 0.70), modo ícone `--category icon` (512²/2 steps) com
  `--transparent` (rembg); resolução por default 1024.
- **Texture2D seamless 2.0**: late-circular + noise rolling, decode integral,
  hires 512+refine, score/auto-heal.
- **Animator3D**: clips de quadrúpede (galope transverso, death/hit/attack),
  two-bone IK própria com limites YAML (`ik-limits`), retarget Quaternius estável.
- **vramd**: admissão por modo + `peak_mib` medido, `vramd zero`/`respawn`/`calibrate`,
  catálogo calibrado por capacidade de VRAM (backends-6g/16g/…).

### Removed — consolidação de modelos (~77 GB no total)

- Modelos sem uso: Sana, Stable Audio Open, Disty0/FLUX.2-klein SDNQ alt.,
  T2MGPT, Hunyuan3D-2.1-DIT, Motion full; wave Motion3D morta do batch;
  extras Part3D/Motion3D/Intrinsic fora do disco (reinstaláveis com
  `./install.sh <tool>`).

### Fixed

- Docs de entrada a bater certo com a CLI: flags do `dream` (`--no-audio/--no-sky/
  --no-terrain` — as `--no-3d/--no-rig/--no-animate` são do `batch`), providers com
  `ollama`, `manifest.yaml` (não `.csv`), `modelserver`→`vramd`, pool de assets dos
  exemplos no caminho certo (`Viber/examples/shared-assets`), README_PT completa
  (pipeline/dream/VibeGame/quality, env vars, manual, Makefile).
- `installer/unified.py` ressincronizado com a API do clified 0.9 (o
  `text2d_venv_only` rebentava com TypeError); wrappers `scripts/installer.py`
  consolidados num template único; dead code removido (`tool_script.py`, ramo
  `requirements_file`).
- CI verde: ruff/mypy/clippy pré-existentes limpos (Viber tools, Shared,
  Materialize `approx_constant`); código vendored excluído do ruff.

## [v0.5.0] — 2026-08-30

Tag de bump do VibeGame (0.5.0) criada pelo workflow Release; a publicação npm
falhou por falta de `NPM_TOKEN` (o pacote `aigamekit-vibegame` nunca chegou ao
npm). Sem GitHub Release anexada — a partir da v0.6.0 as tags criam a release
automaticamente.
