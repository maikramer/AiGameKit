# Vramd (tool wrapper)

Tool clified do monorepo que instala o supervisor de VRAM
[`vramd`](https://pypi.org/project/vramd/) (PyPI) no venv canónico
`Vramd/.venv`. O vramd substitui o antigo ModelServer: fila com prioridade e
afinidade, admissão pelo pico real, evicção peso+LRU e um worker subprocesso
persistente por tool (`serve --ums-worker`).

Instalação e arranque:

```bash
./install.sh vramd
vramd start            # com VRAMD_TOOLS_ROOT e VRAMD_BACKENDS_FILE do auto-start
```

O cliente Shared (`aigamekit_shared.vramd_client`) arranca-o automaticamente
(`ensure_vramd_running`) com:

- `VRAMD_TOOLS_ROOT` = checkout do monorepo (derivação dos venvs das tools)
- `VRAMD_BACKENDS_FILE` = `Shared/src/aigamekit_shared/data/backends.yaml`
