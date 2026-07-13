# Unified Model Server (UMS)

Supervisor único de VRAM para o monorepo GameDev. Um processo detém toda a VRAM
da máquina e roteia pedidos de geração para **backends** (ferramentas GPU)
carregados sob procura, com evicção inteligente **peso + LRU** quando a VRAM
escasseia.

## Problema que resolve

Antes do UMS, cada ferramenta GPU (Text2Icon, Texture2D, ...) corria o seu próprio
model server num socket separado, peer-to-peer. Problemas:

- **Sem "cérebro"**: Nenhuma entidade sabe o inventário completo (o que está
  carregado, quanto pesa, quando foi usado).
- **Evicção cega**: `ensure_vram_available` pedia `release` a todos os servers
  sem saber qual liberta quanta VRAM.
- **Consumers pesados desprotegidos**: Text3D/Paint3D não eram servers — os pesos
  Hunyuan deles ficavam sujeitos a SIGTERM de irmãos.

O UMS resolve isto com **1 socket, 1 processo, inventário global**.

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│            Unified Model Server (1 processo)             │
│  ~/.cache/gamedev/model-server.sock  (socket único)     │
│                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐ │
│  │ BackendMgr   │   │ VRAMPlanner  │   │  Registry    │ │
│  │ load/evict   │◄─►│ peso + LRU   │   │ (YAML + 9    │ │
│  │ ref counting │   │              │   │  adapters)   │ │
│  └──────┬───────┘   └──────────────┘   └──────────────┘ │
│         │ lazy import                                      │
│         ▼                                                 │
│  [modelo carregado em VRAM — só 1-2 de cada vez]        │
└─────────────────────────────────────────────────────────┘
       ▲          ▲          ▲          ▲
       │ generate │ generate │ generate │ generate
   text2icon   texture2d   text3d    paint3d   (todos clientes)
```

## Backends suportados (9)

| Backend | Tool | VRAM (MiB) | Priority | API normalizada |
|---------|------|------------|----------|-----------------|
| text2icon | Text2Icon | 3000 | 20 | `warmup()` |
| texture2d | Texture2D | 7000 | 20 | `warmup()` |
| text2d | Text2D | 4500 | 25 | `warmup()` |
| skymap2d | Skymap2D | 7000 | 25 | `warmup()` |
| text3d | Text3D | 8000 | 40 | `_load_hunyuan()` |
| paint3d | Paint3D | 4000 | 40 | context-manager |
| part3d | Part3D | 4500 | 35 | `load()` |
| text2sound | Text2Sound | 5000 | 30 | `load()` |
| terrain3d | Terrain3D | 6000 | 40 | procedural |

**VRAM** = estimativa do footprint (afinar com profiling real).
**Priority** = menor = evicted primeiro quando VRAM escasseia.

## Instalação

```bash
# Do monorepo root (requer gamedev-shared instalado primeiro)
cd Shared && pip install -e .
cd ../ModelServer && pip install -e .

# Ou via instalador unificado
./install.sh modelserver
```

Isto instala o comando `gamedev-model-server` no PATH.

## Uso

```bash
# Arrancar o UMS (foreground; backends carregam sob procura)
gamedev-model-server start

# Ver estado (backends carregados, VRAM em uso)
gamedev-model-server status

# Listar backends registados
gamedev-model-server backends

# Pré-carregar um backend (quente para próximas gerações)
gamedev-model-server preload text2icon

# Evictar um backend específico (liberta VRAM)
gamedev-model-server evict text2icon

# Evictar todos
gamedev-model-server evict

# Parar o UMS (graceful)
gamedev-model-server stop
```

### Integração com CLIs das tools

Todas as CLIs `generate` das 9 tools delegam automaticamente no UMS se ativo:

```bash
# Se o UMS estiver a correr, isto delega nele (evicção inteligente de VRAM).
# Senão, fallback in-process normal.
text2icon generate "espada" -o sword.png
texture2d generate "madeira" -o wood.png
text2d generate "castelo" -o castle.png
```

### Coordenação de VRAM automática

Ferramentas pesadas (Text3D, Paint3D) chamam `ensure_vram_available(N)` antes de
ocupar a GPU. Se o UMS estiver ativo, este envia `ensure-vram` que evicta
backends peso+LRU até libertar N MiB. Sem o UMS, cai no comportamento legacy
(release cego a todos os servers).

## Protocolo

JSON sobre Unix socket (`~/.cache/gamedev/model-server.sock`):

| Request | Comportamento |
|---------|---------------|
| `{"cmd": "generate", "backend": "text2icon", ...}` | Gera (carrega/evicta se preciso) |
| `{"cmd": "release"}` | Evicta TODOS os backends |
| `{"cmd": "release", "backend": "X"}` | Evicta só o backend X |
| `{"cmd": "status"}` | Estado do UMS + backends carregados |
| `{"cmd": "shutdown"}` | Graceful shutdown |
| `{"cmd": "list-backends"}` | Lista registry (name, vram_mib, loaded?) |
| `{"cmd": "preload", "backend": "X"}` | Pré-carrega backend X |
| `{"cmd": "ensure-vram", "needed_mib": N}` | Evicta peso+LRU até ter N MiB livres |

## Retrocompatibilidade

O UMS é **totalmente retrocompatível** com o sistema anterior:

- `ensure_vram_available`, `discover_server_pids`, `discover_active_sockets`,
  `is_server_running` continuam a funcionar.
- Per-tool legacy servers (Text2Icon, Texture2D) não são removidos — ficam como
  fallback standalone.
- `delegate_to_ums(backend, request)` é o helper canónico para CLIs.
