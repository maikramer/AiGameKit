# Testes — guia do monorepo

Guia canónico para humanos e agentes. Comandos curtos ficam em [`AGENTS.md`](../AGENTS.md); este doc cobre **piso de cobertura**, nomes das suites e o que conta como teste útil aqui.

English: [`TESTING.md`](TESTING.md).

## Objectivos

1. **Todas as ferramentas do instalador + acessórios têm ≥100 testes automatizados** (casos pytest, Bun `it`/`test`, ou Rust `#[test]`).
2. **Suites de cobertura são CPU-first** — sem download de pesos HF, sem CUDA no happy path unitário.
3. **Agent-first:** um agente frio corre `make test-<pkg>` (ou `.venv` do pacote) e verde = contratos de helpers puros / superfície CLI ainda válidos.

Testes GPU / bpy / integração continuam úteis; têm de fazer **skip** limpo sem hardware (`pytest.importorskip`, guards). CI e smoke em laptop não dependem de GPU viva.

## Ferramentas e acessórios (alvos)

| Pacote | Tipo | Como correr | Suites de cobertura (exemplos) |
|--------|------|-------------|-------------------------------|
| Shared | acessório (lib) | `make test-shared` | `tests/test_shared_coverage_100.py` |
| ModelServer (UMS) | acessório | `make test-modelserver` | `tests/test_modelserver_coverage_100.py` |
| GameDevLab | acessório | `make test-gamedevlab` | `tests/test_gamedevlab_coverage_suite.py`, `*_100b.py` |
| Text2D | ferramenta | `make test-text2d` | `test_text2d_coverage_suite.py`, `*_100b.py` |
| Text2Icon | ferramenta | `make test-text2icon` | `test_text2icon_coverage_suite.py`, `*_100b.py` |
| Text3D | ferramenta | `make test-text3d` | `test_text3d_coverage_100.py` |
| Paint3D | ferramenta | `make test-paint3d` | `test_paint3d_coverage_suite.py`, `*_100b.py` |
| Part3D | ferramenta | `make test-part3d` | `test_part3d_coverage_100.py` |
| GameAssets | ferramenta | `make test-gameassets` | `test_gameassets_coverage_100.py` |
| Texture2D | ferramenta | `make test-texture2d` | `test_texture2d_coverage_100.py` |
| Skymap2D | ferramenta | `make test-skymap2d` | `test_skymap2d_coverage_100.py` |
| Text2Sound | ferramenta | `make test-text2sound` | `test_text2sound_coverage_100.py` |
| Terrain3D | ferramenta | `make test-terrain3d` | `test_terrain3d_coverage_suite.py`, `*_100b.py` |
| Rocks3D | ferramenta | `make test-rocks3d` | `test_rocks3d_coverage_100.py` |
| Rigging3D | ferramenta | `make test-rigging3d` | `test_rigging3d_coverage_suite.py`, `*_100b.py` |
| Animator3D | ferramenta | `make test-animator3d` | `test_animator3d_coverage_100.py` |
| Materialize | ferramenta (Rust) | `make test-materialize` | `#[cfg(test)]` em `src/{preset,analyze,io,error,cli}.rs` |
| VibeGame | ferramenta (Bun) | `make test-vibegame` | `tests/coverage-100.test.ts` + unit/integration |

Fallback: `<Pkg>/.venv/bin/pytest tests/ -q`.

CI Python+Rust: `make check` / `make test`. VibeGame à parte: `make test-vibegame`.

## Convenção das suites de cobertura

| Padrão | Uso |
|--------|-----|
| `tests/test_<pkg>_coverage_100.py` | Suite grande (≥100 casos recolhidos) |
| `tests/test_<pkg>_coverage_suite.py` + `*_100b.py` | Split quando um ficheiro cresceu demais |
| `VibeGame/tests/coverage-100.test.ts` | Piso Bun de helpers puros |
| Materialize `#[cfg(test)] mod tests` em `src/*.rs` | Crate só-binário — unit tests junto do código |

**Nome:** manter `coverage` no ficheiro para achar rápido (`rg coverage tests/`).

**Contagem:** casos **recolhidos** pelo pytest (parametrize multiplica). Preferir asserções reais em APIs públicas/puras a stubs vazios.

### O que cobrir (prioridade)

1. **Payloads UMS** — `build_*_request` / opts peak+load.
2. **Perfis de hardware soft** — `profile_from_specs` / `hw_auto_enabled` (puro).
3. **Validadores e defaults** — prompts, dimensões, presets, categorias.
4. **Math puro** — mesh repair, postprocess terrain, DSP áudio (`import torch` local no teste se preciso).
5. **Superfície CLI** — `CliRunner` / `python -m <tool> --help`.
6. **Materialize** — roundtrip `Preset`, `MapSelection`, analyze/classify em imagens sintéticas, exit codes.

### Anti-padrões (aprendidos)

- **Pads fluff** que só dizem “árvore de sources não vazia” — apagar; incham contagem sem proteger contratos.
- **Importar torch/diffusers no topo** das suites de cobertura — parte a collection; usar imports locais.
- **Exigir socket UMS ou GPU viva** em suites unitárias — mock / skip.
- **Hacks binários de GLB** como “teste de animação” — regenerar asset; testar math de retarget.
- **Mexer em código vendored** só para testabilidade — testar wrappers/adapters.
- **Importar wheels nativos in-process no CI** quando o wheel pode SIGILL (mata o processo pytest) — probe em subprocesso primeiro (Text2Sound abaixo).
- **Mutar estado partilhado de módulo** entre casos Bun/pytest (`INPUT_CONFIG`, defaults SoA bitecs) sem restaurar — flakes que passam local e falham por ordem no CI.

## GitHub Actions CI (`.github/workflows/ci.yml`)

Jobs em push/PR a `main`:

| Job | O quê |
|-----|--------|
| `lint` | `ruff check` + `ruff format --check` + `pre-commit run --all-files` (inclui **mypy** no Shared) |
| `test-python` | matrix: Shared, GameAssets, Texture2D, Skymap2D, Rigging3D, Text2Sound, GameDevLab, Rocks3D, Animator3D — cada um `pip install -e Shared/.[dev]` depois pacote `.[dev]` + pytest |
| `test-rust` | Materialize fmt/clippy/test (`continue-on-error`) |
| `vibegame` | Bun install + `check` + `lint` + `format:check` + `test` + `build` |

**Fora da matrix** (stacks GPU pesados): Text2D, Text3D, Paint3D, Part3D, Terrain3D, ModelServer — local / com GPU. O workflow raiz **inclui** VibeGame (não contar só com `VibeGame/.github/`).

### Armadilhas aprendidas (CI verde 2026-07)

| Área | Modo de falha | Contrato / fix |
|------|---------------|----------------|
| Shared `[dev]` | ImportError em `mesh_repair*` sem numpy/scipy/trimesh | Manter esses em `Shared/pyproject.toml` `[dev]` — CI instala `Shared/.[dev]` |
| pre-commit mypy | Ruff verde mas job lint vermelho | Tipar Shared; mypy corre depois do ruff no mesmo job |
| GameAssets Omni softfill | `ImportError: text3d` sem Text3D no runner | `omni_ctrl._CATEGORY_OMNI_DEFAULTS_FALLBACK` + `_category_omni_defaults()` — softfill não pode no-op sem Text3D |
| Text2Sound pedalboard | SIGILL na CPU do runner mata pytest | `Text2Sound/tests/_heavy_deps.py` probe em subprocesso; skip mastering se inseguro |
| Texture2D dtype | `device="cuda"` cai em CPU → float32 | Skip asserts de dtype CUDA sem CUDA real |
| Animator3D mocks bpy | Só `sys.modules` não substitui `animator3d.bpy_ops` já importado | `patch("animator3d.bpy_ops", …, create=True)` (+ stub) |
| GameAssets `to_paint` | Assumir último `run_cmd` == simplify | Pipeline faz simplify **e depois** re-`topology-fix` — assert simplify em `call_args_list` |
| VibeGame tsc | `Transform.dirty` em `Transform \| WorldTransform` | Guard `'dirty' in transform` antes de escrever |
| VibeGame tsconfig | Imports subpath (`vibegame/terrain`, …) falham no `tsc` | Manter `paths` em `VibeGame/tsconfig.json` alinhados com exports |
| VibeGame flakes | `INPUT_CONFIG` partilhado / defaults SoA NavMeshAgent / profiler | Restaurar config; refill SoA em `beforeAll`; permitir `ms >= 0` em timings custom |

Paridade local: `make check` (Python+Rust) e `make check-vibegame` / `make test-vibegame`. Preferir `gh run list` / `gh run view --log-failed` a adivinhar.

## Venvs por pacote

```bash
Text2D/.venv/bin/pytest Text2D/tests/test_text2d_coverage_suite.py -q
Materialize: cargo test --bin materialize-cli
VibeGame: bun test tests/coverage-100.test.ts
```

`GAMEDEV_FILE_LOG=1` pode ser preciso sob pytest (ver [`LOGGING_PT.md`](LOGGING_PT.md)).

## Estender cobertura

Ao adicionar ferramenta ou helper puro crítico:

1. Testes em `tests/` (ou `#[cfg(test)]` no Materialize).
2. Manter piso do pacote **≥100** casos recolhidos.
3. Preferir estender ficheiro `*coverage*` existente.
4. Target do pacote a verde antes de dar por feito.
5. Actualizar a tabela deste doc se o entry-point mudar.

## Docs relacionados

| Doc | Papel |
|-----|-------|
| [`AGENTS.md`](../AGENTS.md) | Missão + targets make + estilo |
| [`findings/`](findings/) | Aprendizados runtime/GPU |
| [`MODEL_FINDINGS.md`](MODEL_FINDINGS.md) | Hub VRAM / kernels / Omni |
| [`mission/04-agent-first-reproducibility.md`](mission/04-agent-first-reproducibility.md) | Porque testes de contrato importam |
| `README.md` / `AGENTS.md` por pacote | Notas locais |
