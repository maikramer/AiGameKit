# aigamekit-shared

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

Biblioteca partilhada do monorepo **AiGameKit** — código comum entre Text2D, Text3D, GameAssets, Texture2D, Skymap2D, Text2Sound, Rigging3D e Materialize.

## Módulos

| Módulo | Descrição |
|--------|-----------|
| `aigamekit_shared.logging` | Logger Rich/ANSI + ficheiro diário (`configure_logging`, bridge stdlib). Guia: [docs/LOGGING_PT.md](../docs/LOGGING_PT.md) |
| `aigamekit_shared.cli_rich` | `rich-click`: `setup_rich_click`, `setup_rich_click_module(tool=…)` — `tool=` activa logging em ficheiro; todas as CLIs Python usam isto no seu `cli_rich.py` |
| `aigamekit_shared.hf` | Token HF (`get_hf_token`) e texto de cache para Rich (`hf_home_display_rich`) — sem dependência de `huggingface_hub` |
| `aigamekit_shared.skill_install` | Instalação de Agent Skills Cursor genérica por `tool_name` (ex.: `rigging3d` quando existir `SKILL.md`) |
| `aigamekit_shared.gpu` | Utilitários GPU/memória (format_bytes, get_gpu_info, clear_cuda_memory, ...) |
| `aigamekit_shared.subprocess_utils` | Subprocess (`resolve_binary` prefere `<Tool>/.venv/bin` se `AIGAMEKIT_PREFER_MONOREPO=1`, `run_cmd`, `RunResult`) |
| `aigamekit_shared.cli_helpers` | UMS (`try_ums_delegation`, `with_ums_peak_opts`; `prepare_gpu_exclusive` só após UMS falhar / `--no-ums`) |
| `aigamekit_shared.env` | Env do monorepo (`TOOL_BINS`, `get_tool_bin`, `prefer_monorepo_tools`, …) |
| `aigamekit_shared.installer` | Ponte Clified (`aigamekit-install` / `install.sh` → `tools.yaml`) |
| `aigamekit_shared.installer.monorepo` | `find_monorepo_root`, `try_find_monorepo_root` |
| `aigamekit_shared.installer.clified_hooks` | Hooks por ferramenta (Text3D, Text2Sound, Paint3D, Rigging3D) |
| `aigamekit_shared.installer.text3d_extras` | Pós-venv Text3D (`~/.config/text3d`, wrappers) |
| `aigamekit_shared.multi_gpu` | Planeador de split multi-GPU (MultiGPUPlanner, DevicePlan, ModelArchitectureRegistry) — envolve o accelerate para colocação inteligente de dispositivos |
| `aigamekit_shared.profiler` | Spans com tempo, CPU, RSS e VRAM CUDA (`ProfilerSession`, `profile_span`, `cuda_memory_snapshot_all` para todas as GPUs; extra `[profiler]` → `psutil`) |

## Exemplo de uso

```python
from aigamekit_shared.logging import Logger, configure_logging

configure_logging("meu_modulo")  # ou via setup_rich_click_module(tool=…)
log = Logger()
log.info("Mensagem informativa")
log.step("A processar item 1/10")
log.success("Concluído com sucesso")
# Ficheiro: ~/.cache/aigamekit/logs/meu_modulo-YYYY-MM-DD.log
```

```python
from aigamekit_shared.subprocess_utils import resolve_binary, run_cmd

bin_path = resolve_binary("TEXT2D_BIN", "text2d")
result = run_cmd([bin_path, "generate", "um gato"], verbose=True)
```

```python
from aigamekit_shared import MultiGPUPlanner

planner = (
    MultiGPUPlanner()
    .for_model(model)
    .with_gpus([0, 1])
    .architecture("hunyuan3d")
)
plan = planner.plan()  # DevicePlan com device_map
model = planner.apply()  # Modelo despachado pelas GPUs
```

## Instalador unificado

Ao instalar o pacote `aigamekit-shared`, fica disponível o comando `aigamekit-install`:

```bash
aigamekit-install --list                     # Listar ferramentas
aigamekit-install materialize                # Instalar Materialize (Rust)
aigamekit-install text2d                    # Cria projecto/.venv se necessário; wrappers usam esse Python
aigamekit-install all                        # Instalar tudo
aigamekit-install materialize --action uninstall
```

Também pode ser executado sem `pip install` via scripts na raiz do monorepo:

```bash
./install.sh materialize     # Linux/macOS
.\install.ps1 materialize    # Windows PowerShell
```

## Instalação

```bash
# Dentro do monorepo (modo editável)
pip install -e Shared/

# Com suporte GPU
pip install -e "Shared/[gpu]"

# Com CLI (click + rich-click)
pip install -e "Shared/[cli]"
```

## Extras

- `gpu` — torch (para `aigamekit_shared.gpu`)
- `cli` — click + rich-click (para `aigamekit_shared.cli_rich`)
- `dev` — pytest

## Desenvolvimento

```bash
# Instalar com extras de dev
pip install -e "Shared/[dev]"

# Correr testes
pytest Shared/tests/ -v
pytest Shared/tests/test_shared_coverage_100.py -q   # piso de cobertura

# Ou via Makefile na raiz do monorepo
make test-shared
```

Guia: [`docs/TESTING_PT.md`](../docs/TESTING_PT.md).

## Logging em ficheiro

Todas as CLIs Python (+ UMS) espelham `Logger` e stdlib para
`~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log`. Guia completo:
[docs/LOGGING_PT.md](../docs/LOGGING_PT.md).

## Variáveis de Ambiente

Definidas em `aigamekit_shared.env` / `logging.py` e usadas por todos os pacotes:

| Variável | Descrição |
|----------|-----------|
| `TEXT2D_BIN` | Caminho para o binário `text2d` (fallback: `text2d` no `PATH`) |
| `TEXT3D_BIN` | Caminho para o binário `text3d` |
| `TEXT2SOUND_BIN` | Caminho para o binário `text2sound` |
| `TEXTURE2D_BIN` | Caminho para o binário `texture2d` |
| `SKYMAP2D_BIN` | Caminho para o binário `skymap2d` |
| `RIGGING3D_BIN` | Caminho para o binário `rigging3d` |
| `GAMEASSETS_BIN` | Caminho para o binário `gameassets` |
| `MATERIALIZE_BIN` | Caminho para o binário `materialize` |
| `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` | Token Hugging Face (ver também `aigamekit_shared.hf`) |
| `HF_HOME` | Diretório de cache Hugging Face |
| `PYTORCH_CUDA_ALLOC_CONF` | Configuração de alocação CUDA (auto-definida pelo monorepo se vazia) |
| `AIGAMEKIT_LOG_DIR` | Dir de logs (default `~/.cache/aigamekit/logs`) |
| `AIGAMEKIT_LOG_FILE` | Path exacto do ficheiro de log |
| `AIGAMEKIT_LOG_TOOL` | Nome da tool no ficheiro |
| `AIGAMEKIT_LOG_LEVEL` | Nível mínimo (`DEBUG`/`INFO`/`WARN`/`ERROR`) |
| `AIGAMEKIT_FILE_LOG` | `0` off; `1` força on (pytest) |
| `AIGAMEKIT_NO_FILE_LOG` | `1` desliga logging em ficheiro |
