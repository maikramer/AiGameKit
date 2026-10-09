# Vramd — supervisor de VRAM do monorepo

O vramd é o **gestor único de GPU/VRAM** do AiGameKit: um processo, um socket
(`~/.cache/vramd/vramd.sock`), fila com prioridade + afinidade, admissão pelo
**pico real** de VRAM, evicção peso+LRU e um worker subprocesso persistente por
tool (`serve --ums-worker`). Substituiu o antigo ModelServer.

Este package é um **wrapper clified**: instala o
[`vramd`](https://pypi.org/project/vramd/) (PyPI) no venv canónico `Vramd/.venv`.
O upstream desenvolve-se em repo próprio; a configuração de backends do
monorepo vive em `Shared/src/aigamekit_shared/data/backends.yaml`.

## Instalação

```bash
./install.sh vramd          # raiz do monorepo (perfil core inclui-o)
```

## Arranque

Normalmente **não precisas de o arrancar**: o cliente
(`aigamekit_shared.vramd_client.ensure_vramd_running`) auto-arranca o supervisor
no primeiro job GPU, injetando `VRAMD_TOOLS_ROOT` (checkout do monorepo) e
`VRAMD_BACKENDS_FILE` (backends.yaml + overlay de calibração da tua GPU).
Kill-switch: `VRAMD_AUTO_START=0`.

```bash
vramd start                 # arranque manual, se quiseres
vramd status                # socket, fila, VRAM por backend
```

## Comandos essenciais

```bash
vramd status                # estado do supervisor + backends
vramd queue                 # fila de jobs (quem espera, quem corre)
vramd wait <job_id>         # esperar por um job (ou --vramd-stream nas tools)
vramd cancel <job_id>|--all # cancelar jobs
vramd zero                  # libertar TODA a VRAM (mata workers idle) sem parar o supervisor
vramd respawn <backend>     # reiniciar SÓ o worker de uma tool (após editar código da tool)
vramd doctor                # diagnóstico: socket, fila, órfãos, driver, deps dos backends
vramd calibrate <backend>   # medir o footprint real e emitir descriptor calibrado
```

As tools delegam automaticamente (`text2d generate …` corre via vramd); flags
úteis: `--vramd-priority interactive|batch`, `--vramd-stream`, `--no-vramd`
(bypass intencional).

## Regras de ouro (agentes e humanos)

1. Antes de mexer na GPU: `vramd status` / `queue` / `doctor`.
2. **Nunca** `kill`/pkill de processos GPU com o vramd ocupado — mata o workload
   errado. Usa `vramd wait` ou `vramd cancel`.
3. Editaste código de uma tool? `vramd respawn <backend>` — não reinicies o
   supervisor inteiro.
4. VRAM presa com o vramd idle: `vramd zero`.

## Documentação

- Operação detalhada + anti-padrões: secção *vramd* do [`AGENTS.md`](../AGENTS.md)
- VRAM/operações: [`docs/MODEL_FINDINGS.md`](../docs/MODEL_FINDINGS.md),
  [`docs/findings/UMS_VRAM_FINDINGS.md`](../docs/findings/UMS_VRAM_FINDINGS.md)
- Batch em waves: [`docs/GAMEASSETS_UMS_BATCH.md`](../docs/GAMEASSETS_UMS_BATCH.md)
- API cliente (Python): `aigamekit_shared.vramd_client`
