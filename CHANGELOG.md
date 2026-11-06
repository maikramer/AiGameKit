# Changelog

Notáveis por versão do monorepo AiGameKit. Formato [Keep a Changelog](https://keepachangelog.com/);
as tags `v<N>` são snapshots do monorepo — cada package mantém a sua própria versão
(`pyproject.toml` / `Cargo.toml` / `package.json`). Pacotes individuais com changelog
próprio: [`Materialize/CHANGELOG.md`](Materialize/CHANGELOG.md).

## [v0.7.0] — 2026-11-06

Foco: **a malha volta a ser o produto** — o refinamento Hi3D entra como stage,
o shape recupera o sampling e a pose certa, e o paint/LOD deixam de custar
costuras.

### Added — Hi3D (refinamento image-conditioned)

- **Ferramenta nova `Hi3D/`** (Hi3D v1.0 / SPARC3D): re-mesha o shape com a
  imagem de referência como condição. É o stage `refine` do batch, default ON,
  e corre como worker com protocolo JSONL próprio (`Hi3D/src/hi3d/ums.py`).
- **Único venv Python 3.10 do monorepo** — o `tools.yaml` declara piso e teto
  `3.10` porque o upstream só mediu 3.10.12 / torch 2.5.0+cu124.
- **Admit de 4300 MiB** a 512³/25 passos com residência por fase (3,74 GiB de
  pico, ~60 s por asset); `--no-staging` desce a 0,9 GiB a ~137 s.
- `hi3d download-model` (~7,7 GB de pesos), `./install.sh hi3d` e
  `make test-hi3d`.
- Estudo: [`docs/findings/HI3D_REFINE_FINDINGS.md`](docs/findings/HI3D_REFINE_FINDINGS.md).

### Added — GameAssets

- **Stage `refine` integrado no pipeline**, com gates: `humanoid`/`creature` e
  tudo o que vai ser rigado fica de fora — o Hi3D re-sintetiza a forma e muda a
  pose, e o rigging espera a A-pose. A entrada leva teto de 1,2M faces (o
  spconv estoura a VRAM de 6 GB).
- **Imagem de referência em sidecar** `<stem>_input.png`: um `text3d generate`
  manual passa a ter condição para o refine.
- **Overrides por linha** no bloco `paint3d:` do manifest (faces a pintar,
  vistas, resolução) e flag `--pbr-enrich`.
- O `pipeline:` do manifest passa a filtrar mesmo lod/collision; o `resume`
  confirma que o artefacto existe antes de marcar a linha como ok; o `doctor`
  verifica o Hi3D.

### Changed — Text3D

- **A-pose oficial sem dedos** (21 ossos): os 30 ossos de dedo do upstream
  abrem em profundidade enquanto as referências saem com a palma virada à
  câmara, e o modelo construía a mão duas vezes — mãos «balão» e 7–9 dedos.
  Tabela de presets em [`docs/OMNI_SHAPE_FINDINGS.md`](docs/OMNI_SHAPE_FINDINGS.md).
- **Pisos de octree por categoria**: humanoid 384, creature 256, edifícios 576,
  teto 608 e 128 células no eixo fino (a casca deixava de perfurar).
- **`text3d refine` no CLI** (Hi3D pelo vramd; in-process sem ele), alvos
  explícitos de LOD1/LOD2 e a identidade do esqueleto no fingerprint do resume.
- **Finish:** binário instalado em vez de `npx --yes`, export a q92, KTX2
  descomprimido antes do bpy e sem roundtrip quando NORMAL+TANGENT já existem.
- **Ladder com guarda de silhueta**: se o LOD encolher >5% da AABB, refaz com o
  alvo do LOD0; o remesh troca listas Python de 16,8M floats por `foreach_get`.
- **Decode:** marching cubes recorta à AABB da máscara antes de ir para NumPy.

### Changed — Paint3D

- **xatlas em subprocesso com timeout de 300 s** — a chamada C não se
  interrompe e o refine produz malhas que a penduram (>170 s); em timeout a
  linha falha e o pack continua.
- **Bake de 6 canais num só passe** (albedo + mr); o `mr` deixa de passar pelo
  Real-ESRGAN (`PAINT3D_ENHANCE_MR=1` repõe); gutter de costuras por resolução.
- **Frame correto**: Blender Z-up → glTF Y-up para as câmaras do Hunyuan, sem
  rodar duas vezes inputs já baked.
- `PAINT3D_DEBUG_DIR` despeja as vistas (normal/posição/albedo/mr).

### Fixed — Rigging3D

- **Cotovelo falso do SkinTokens**: o joint 2–3 cm fora da linha ombro→pulso
  aparecia invertido depois do retarget («cotovelo virado»). O `bone_repair`
  põe-no na linha e preserva só a dobra para trás, que é a única natural numa
  A-pose; acima de 45° assume braço dobrado e não toca.

### Changed — SDNQ 0.2.9

- **Presets de baixo bit corrigidos e mais precisos.** O codebook Lloyd-Max
  guarda `2**bits` níveis fp32 **por grupo**: a `group_size=32` isso inflacionava
  `sdnq-int3`/`sdnq-int2` para 11/6 b/peso (mais do que o int4/uint8) e
  `sdnq-uint8` para 10–18 b/peso. Os presets deixam de usar codebook e passam a
  usar **escalas double-quant** (`scale_dtype="uint8"`, SDNQ 0.2.8). Medido em
  FLUX.3/SD1.5/Paint/Omni: `sdnq-int4` 5.1–7.0 b/peso com -22% de erro vs o
  int4+SVD anterior; `sdnq-uint8` 8.1 b/peso e load ~100× mais rápido.
- **Novos tiers:** `sdnq-uint4-hq` (group 16) e `sdnq-int3`/`sdnq-int2` com
  poupança real (3.5/2.5 b/peso). Expostos no Text3D; Part3D ganha `-q hq`.
- **`quantize_model` passa `quantization_config=config`:** o filtro por
  assinatura descartava em silêncio `scale_dtype`/`zero_point_dtype`.
- **`aigamekit-lab bench sdnq`:** compara receitas em pesos reais (erro de
  pesos/saída, b/w exatos, tempo) sem carregar o modelo.
- Pins `sdnq>=0.2.9`; repo irmão `vramd` 0.4.0 com os fatores novos
  (`uint4-hq` 0.38, int3 0.24, int2 0.18).
- Estudo: [`docs/findings/SDNQ_029_FINDINGS.md`](docs/findings/SDNQ_029_FINDINGS.md).

### Changed — Shared

- **19 categorias de áudio** (novas `vocal`, `movement`, `collectible`), cada
  uma com o seu `audio_kind`: sem ele a geração saía sem hints e o pipeline não
  sabia que um passo é UM passo, não uma sequência.
- **Fatores de VRAM dos tiers novos** no `lowvram` (0,38 / 0,24 / 0,18) e
  orçamento do decode com piso soft quando o orçamento o alcança.

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
  (pipeline/dream/quality, env vars, manual, Makefile).
- `installer/unified.py` ressincronizado com a API do clified 0.9 (o
  `text2d_venv_only` rebentava com TypeError); wrappers `scripts/installer.py`
  consolidados num template único; dead code removido (`tool_script.py`, ramo
  `requirements_file`).
- CI verde: ruff/mypy/clippy pré-existentes limpos (Viber tools, Shared,
  Materialize `approx_constant`); código vendored excluído do ruff.
