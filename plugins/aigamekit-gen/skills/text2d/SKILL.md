---
name: text2d
description: Gerar imagens a partir de texto com o CLI text2d (FLUX.3 Image Klein SDNQ) do AiGameKit — prompts, resolução, seeds, modo ícone com fundo transparente, tiers de qualidade e regras de VRAM/vramd. Usar quando for preciso gerar imagens 2D, ícones de UI ou imagens de referência para text3d/paint3d.
---

# Text2D — texto → imagem (FLUX.3 Image Klein SDNQ)

## Resolver o binário

Os CLIs instalam em `~/.local/bin` via `./install.sh`; se não estiverem no PATH, usar o venv do checkout do monorepo AiGameKit:

```bash
TEXT2D=$(command -v text2d || echo "$HOME/GitClones/AiGameKit/Text2D/.venv/bin/text2d")
```

## Comando principal

```bash
text2d generate "um goblin com lança, estilo fantasia estilizada" -o goblin_ref.png
```

Defaults: 1024×1024, 4 steps, guidance 1.0 (checkpoint SDNQ — não subir guidance).

| Flag | Efeito |
|------|--------|
| `-o out.png` | Saída (`.png` ou `.jpg`) |
| `-W` / `-H` | Largura/altura (default 1024) |
| `-s` / `-g` | Steps (4) / guidance (1.0) |
| `--seed N` | Reprodutibilidade |
| `--quality fast\|low\|medium\|high\|highest` | Preset soft de qualidade (default `medium`) |
| `--category icon` | Modo ícone: 512², 4 steps, styling de ícone |
| `--transparent` | Remove fundo (rembg/U2Net; exige `.png`) |
| `--cpu` / `--gpu-ids 0,1` | Forçar CPU / split multi-GPU |

## Ícones de UI

```bash
text2d generate "espada de ferro" --category icon --transparent -o sword_icon.png
```

## Referências para geração 3D

Quando a imagem alimenta `text3d generate`/`paint3d texture`, preferir vista **eye-level 3/4** do objeto inteiro, fundo limpo, sem cortes. Evitar vistas de topo e close-ups.

## VRAM / vramd (obrigatório ler antes de matar processos)

A delegação ao vramd é automática — não planear VRAM manualmente. Se um job bloquear:

1. `vramd status` / `vramd queue` — ver quem detém a GPU.
2. Esperar (`text2d generate … --vramd-stream`) ou `vramd cancel <job_id>`.
3. **Nunca** `kill`/`pkill` GPU com o vramd ocupado.
4. vramd idle a segurar VRAM → `vramd zero` (mata workers idle sem parar o supervisor).

Bypass intencional: `--no-vramd`. Flags de job: `--vramd-priority interactive|batch`, `--vramd-stream`.

## Diagnóstico

```bash
text2d doctor   # PyTorch, CUDA, VRAM, cache HF
text2d info     # sistema + GPU
text2d models   # modelos suportados
```

Logs: `~/.cache/aigamekit/logs/text2d-AAAA-MM-DD.log`.
