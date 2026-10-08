# AGENTS.md — examples/simple-rpg

Mundo de referência jogável (port do `VibeGame/examples/simple-rpg`).
Panorama e estado: [`README.md`](README.md). Aqui ficam as regras para editar.

## Mapa

| Caminho | O que é |
|---------|---------|
| `world.xml` | raiz do mundo (inclui os módulos + UI declarativa) |
| `world/**.xml` | módulos migrados: cidades (`world/cities/discordia/*`), criaturas, landmarks, interiors, frontier |
| `quests/*.json` | definições das 27 quests — lidas do DISCO no arranque (reiniciar a engine; sem `cargo build`) |
| `scripts/**.lua` | comportamento (inimigos/bosses, colheita, POIs, HUD/UI) — **sem hot-reload: alterar exige reiniciar a engine** |
| `ui/*.css` + `world/hud.xml` / `world/menu.xml` | HUD e menu [Q] declarativos |
| `shaders/sky.wgsl` | céu — **reescrito em disco a cada `run`** (especializado pelos attrs do `<Sky>`); não editar à mão expectando persistência |
| `assets/` | espelho **não versionado** dos GLBs decomprimidos — regenerar com `Viber/scripts/sync_assets.py`, nunca editar à mão |
| `public/` | conteúdo original migrado (referência histórica) |

## Regras ao editar

- Depois de qualquer edição: `cargo run -- analyze examples/simple-rpg/world.xml`
  (exit 1 em erro; warnings listam attrs desconhecidos).
- **Gotcha self-closing:** acrescentar filhos a `<Entity … />` cria um
  *irmão* afixado à origem (= spawn do player) — converter para forma aberta.
  O `analyze` **não** apanha isto.
- Estradas/lagos/rios/pads têm ordem de carve e limites (ver
  `src/terrain/AGENTS.md`); estradas < ~1.5 texéis viram no-op.
- QA visual: `viber run examples/simple-rpg/world.xml --bridge` +
  `viber debug screenshot/tree/prof/lua` (uma engine de cada vez — VRAM).

## Verificar

```bash
cd Viber && cargo test
cargo run -- analyze examples/simple-rpg/world.xml
cargo run -- run examples/simple-rpg/world.xml
```
