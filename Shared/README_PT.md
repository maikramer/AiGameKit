# gamedev-shared

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

Biblioteca partilhada do monorepo **GameDev** — código comum entre Text2D, Text3D, GameAssets, Texture2D, Skymap2D, Text2Sound, Rigging3D e Materialize.

## Módulos

| Módulo | Descrição |
|--------|-----------|
| `gamedev_shared.logging` | Logger Rich/ANSI unificado (info, warn, error, step, header, success) |
| `gamedev_shared.cli_rich` | `rich-click`: `setup_rich_click`, `setup_rich_click_module` (devolve `(click, rich_ok)`); todas as CLIs Python do monorepo usam o segundo no seu `cli_rich.py` |
| `gamedev_shared.hf` | Token HF (`get_hf_token`) e texto de cache para Rich (`hf_home_display_rich`) — sem dependência de `huggingface_hub` |
| `gamedev_shared.skill_install` | Instalação de Agent Skills Cursor genérica por `tool_name` (ex.: `rigging3d` quando existir `SKILL.md`) |
| `gamedev_shared.gpu` | Utilitários GPU/memória (format_bytes, get_gpu_info, clear_cuda_memory, ...) |
| `gamedev_shared.subprocess_utils` | Execução de ferramentas via subprocess (resolve_binary, run_cmd, RunResult) |
| `gamedev_shared.env` | Constantes e helpers para variáveis de ambiente do monorepo (`TOOL_BINS`, `get_tool_bin`, …) |
| `gamedev_shared.installer` | Ponte Clified (`gamedev-install` / `install.sh` → `tools.yaml`) |
| `gamedev_shared.installer.monorepo` | `find_monorepo_root`, `try_find_monorepo_root` |
| `gamedev_shared.installer.clified_hooks` | Hooks por ferramenta (Text3D, Text2Sound, Part3D, Paint3D, Rigging3D) |
| `gamedev_shared.installer.text3d_extras` | Pós-venv Text3D (`~/.config/text3d`, wrappers) |
| `gamedev_shared.installer.part3d_extras` | Extras PyG (torch-scatter, torch-cluster) e resumo Part3D |
| `gamedev_shared.multi_gpu` | Planeador de split multi-GPU (MultiGPUPlanner, DevicePlan, ModelArchitectureRegistry) — envolve o accelerate para colocação inteligente de dispositivos |
| `gamedev_shared.profiler` | Spans com tempo, CPU, RSS e VRAM CUDA (`ProfilerSession`, `profile_span`, `cuda_memory_snapshot_all` para todas as GPUs; extra `[profiler]` → `psutil`) |

## Exemplo de uso

```python
from gamedev_shared.logging import get_logger

log = get_logger("meu_modulo")
log.info("Mensagem informativa")
log.step("A processar item 1/10")
log.success("Concluído com sucesso")
```

```python
from gamedev_shared.subprocess_utils import resolve_binary, run_cmd

bin_path = resolve_binary("TEXT2D_BIN", "text2d")
result = run_cmd([bin_path, "generate", "um gato"], verbose=True)
```

```python
from gamedev_shared import MultiGPUPlanner

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

Ao instalar o pacote `gamedev-shared`, fica disponível o comando `gamedev-install`:

```bash
gamedev-install --list                     # Listar ferramentas
gamedev-install materialize                # Instalar Materialize (Rust)
gamedev-install text2d                    # Cria projecto/.venv se necessário; wrappers usam esse Python
gamedev-install all                        # Instalar tudo
gamedev-install materialize --action uninstall
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

- `gpu` — torch (para `gamedev_shared.gpu`)
- `cli` — click + rich-click (para `gamedev_shared.cli_rich`)
- `dev` — pytest

## Desenvolvimento

```bash
# Instalar com extras de dev
pip install -e "Shared/[dev]"

# Correr testes
pytest Shared/tests/ -v

# Ou via Makefile na raiz do monorepo
make test-shared
```

## Variáveis de Ambiente

Definidas em `gamedev_shared.env` e usadas por todos os pacotes do monorepo:

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
| `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` | Token Hugging Face (ver também `gamedev_shared.hf`) |
| `HF_HOME` | Diretório de cache Hugging Face |
| `PYTORCH_CUDA_ALLOC_CONF` | Configuração de alocação CUDA (auto-definida pelo monorepo se vazia) |
