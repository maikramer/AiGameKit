# simple-farm

<!-- LLM:OVERVIEW -->

Isometric farming + gathering game: lavrar→plantar→regar→dormir→crescer→colher→
vender, plus chopping trees and breaking rocks for wood/stone. **512 m** of
continuous relief (matching `farm_valley.ahgt`'s metadata — the loader ignores
the XML attribute) with a town at the centre, farm fields west, dense forest
north, a rock field south-east and open meadow south. No terraces: the previous
four abutting pads with `falloff="0.6"` were ~75° steps, past the
CharacterController's 45° limit, so the valley was four islands joined by
staircases. Engine plugins (`isometric-camera`, `farm-plot`, `daycycle`,
`rpg-resource-node`, `destructible`, hud Hotbar/StatBar) carry the systems; the
game layer in `src/game/` owns tools, stamina, sleep, harvest loot and the
market stall economy. World XML in `public/world/` (include order matters);
data-driven crops/items/tools in `public/data/*.yaml`. Smoke:
`playwright/simple-farm-smoke.spec.ts` (webServer port 30987); ground contract:
`tests/integration/terrain/farm-plateau-flat.test.ts`.
<!-- /LLM:OVERVIEW -->

## Running

```bash
bun install
bun run dev          # vite on the example port
bun run lint:world   # headless world analyzer (errors fail)
```

## Layout

| Path             | Responsibility |
| ---------------- | -------------- |
| `index.html`     | `<Scene>` + includes + `<IsometricCamera>`; include order is structural |
| `src/main.ts`    | Boot: plugins, `withSystem(FarmGameSystem)`, data YAML fetch, debug actions |
| `src/game/`      | Game layer — `farm-game` ([J] dispatch, player setup), `tools`, `stamina`, `sleep`, `shop`, `harvest` (loot from broken nodes) |
| `src/scripts/`   | Per-entity scripts resolved by `script="…"`: `tree.ts`, `rock.ts` (prompt + node classification) |
| `src/data/i18n.ts` | EN/PT strings (shared kit keys merge underneath) |
| `public/world/`  | environment (sky/lights/daycycle/post), terrain (512 m + town/farm pads), water (river + lake), paths (roads + bridge + decal trails), town (bed, stall, shells, square), farm (plot + barn yard), landmarks (4 POIs), forest / quarry / meadow (carpet + spawners), hud |
| `public/data/`   | `crops.yaml` (crop kind), `items.yaml` (item + price), `tools.yaml` (tool) |

## Game loop contracts

- **Crop growth hangs off `DAY_ADVANCED`** (daycycle event): sleeping and
  staying past midnight both advance every `FarmGrid` exactly once per day.
- **The engine layer stays pure**: `farm-plot` mutators never touch inventory,
  stamina or gold — `src/game/tools.ts` pays the costs and banks the yield.
- **Entity names are load-bearing**: `player`, `farm_home` (bed prompt),
  `farm_market` (shop prompt), and the game-layer wiring runs AFTER
  `runtime.start()` because names only exist once the world is live.
- **`<FarmPlot at>` is the centre of cell (0,0)**, not of the grid
  (`farm-plot/grid.ts:4-5`): a 24x18 plot at `at` spans `at → at + (23, 17)`.
  The declaration is pre-offset so the tiles land centred on the farm pad, and
  `base-y` must equal that pad's `height`.
- **Harvest is engine-side**: `destructible` counts the hits and fells/shatters
  the prop, `<ResourceNode kind yield respawn>` holds the loot data, and one
  global `onDestructibleDestroyed` handler in `game/harvest.ts` credits the
  inventory. The entity scripts only register the "[J]" prompt — subscribing
  per instance would add one listener per tree.
- **Save/load** is one blob: world snapshot + serializer registry (farm tiles,
  inventories, clock, `farm-player` globals). See `save-load/serializer.ts`.
