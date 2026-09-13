# Gauntlet — simple-rpg → Breath of the Wild

**Meta:** simple-rpg ao nível visual do Zelda: BotW — cenário, iluminação, céu, água, atmosfera, game feel. Melhorias reutilizáveis entram na engine (`src/`), conteúdo no exemplo. Meta mensurável: **60 fps** (`viber debug prof --samples 10`).

**Barra:** capturas reais de BotW (em `refs/`). Comparação cega A/B por crítico com contexto fresco. Exit = crítico escolhe o nosso às cegas.

## Refs (BOTW real)

| Ficheiro | Cena |
|---|---|
| `refs/botw-great-plateau-day.jpg` | Great Plateau, dia, 4K |
| `refs/botw-great-plateau-tower.png` | Vista da torre, dia |
| `refs/botw-dueling-peaks-dusk.png` | Dueling Peaks, crepúsculo |
| `refs/botw-lake.png` | Lago |

## Peças (queue)

1. **Céu + luz golden hour** ← em curso
2. Terreno/splats
3. Vegetação (densidade + vento)
4. Água
5. Pós-processamento (exposição/bloom/tonemap)
6. Névoa/visibilidade
7. HUD
8. Game feel (gameplay)

## Bugs de engine descobertos no setup

| # | Bug | Estado |
|---|---|---|
| B1 | **OOM VRAM no boot do simple-rpg em 6 GB** (RTX 4050 Laptop): `Quitting the application due to OutOfMemory RenderError` mesmo com `VIBER_WATER_SSR=0 VIBER_PROBES=0 VIBER_NO_VOLUMETRICS=1`. Causa: streaming de colunas com `render-distance=950` + LOD0 célula 0,5 m | **fix em curso**: knob `VIBER_RENDER_DISTANCE` (builder bg) |
| B2 | `viber.debug.set_window(1920,1080)` derruba a engine (shutdown limpo, sem panic — CommandQueue flood + exit) | aberto |
| B3 | Engine morre em silêncio quando lançada como filha de sessão tmux que termina (SIGHUP não tratado) | aberto (workaround: `setsid nohup`) |

## Rounds

### Round 0 — setup
- ✅ Tooling verificado (`viber` 0.1.0, bridge BRP, session protocol)
- ✅ 4 refs BOTW descarregadas e validadas (magic bytes)
- ✅ Baseline bloqueada por B1 (3 engines mortas em sequência: 15:21, 15:28, 15:32)
- 🔄 Builder: `VIBER_RENDER_DISTANCE`
- ⏳ Baseline 3 cenas (amanhecer 380 / dia 600 / crepúsculo 1150, câmara d=18 p=30)
- ⏳ Critic cego round 1

## Protocolo QA (obrigatório, respeitado)

`viber session status` → `session up` (setsid) → `claim --owner gauntlet-botw` → QA → `release`. Porta descoberta por `--world`. Nunca matar engine.
