# Examples

<!-- LLM:OVERVIEW -->

Shipped examples in this repository: **hello-world** (minimal terrain + physics + deterministic placement), **simple-rpg** (full AiGameKit pipeline demo, Crystal Vale), **simple-racer** (racing plugin + shared Vale packs), and **simple-farm** (isometric Harvest-Moon-style farming: till→plant→water→sleep→harvest→sell). Shared GameAssets packs (forest/village/infra/terrain/props manifests + binaries) live in [`shared-assets/`](shared-assets/README.md).
<!-- /LLM:OVERVIEW -->

## Purpose

- Demonstrate engine capabilities
- Provide integration reference for the declarative world XML
- Test plugin combinations (terrain, spawner, particles, etc.)

## Layout

```
examples/
├── context.md          # This file
├── shared-assets/      # Crystal Vale shared packs: manifests + binaries (single pool, served via vibegame({ sharedAssets }))
├── shared/             # TS helpers (i18n, HUD) — not GLBs
├── hello-world/        # Minimal: terrain, dynamic body, <GameObject place="…">
│   ├── context.md
│   ├── src/main.ts
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── simple-rpg/         # Full monorepo pipeline + GLBs, NPCs, save/load, i18n
│   ├── README.md
│   ├── index.html
│   ├── src/main.ts
│   ├── public/assets/  # After handoff / batch
│   └── sample-gameassets/  # game.yaml + game-specific manifests (pack manifests live in shared-assets/)
├── simple-racer/       # Racing plugin; Vale carts + copied RPG scenery
│   ├── PROGRESS.md
│   ├── index.html
│   ├── src/
│   └── sample-gameassets/
└── simple-farm/        # Isometric farming loop (farm-plot + daycycle plugins)
    ├── index.html      # World includes + <IsometricCamera>
    ├── src/main.ts     # Boot, plugins, debug/QA actions
    ├── src/game/       # Game layer: tools, stamina, sleep, shop, FarmGameSystem
    └── public/world/   # environment/terrain/farm/village/vegetation/hud XML
```

## Deterministic terrain placement (entity-centric)

Use **`<GameObject place="at: x z; …">`** (not a separate wrapper tag): one root entity is anchored to the terrain at XZ; optional keys match the internal `place` profile (`base-y-offset`, `y-offset`, `ground-align`, `align-to-terrain`, …). Child recipes (`GLTFLoader`, `ParticleSystem`, `NPC` with merge, etc.) hang under that root. See [Spawner plugin context](../src/plugins/spawner/context.md).

**Spawned actors (enemies / NPCs):** profile `creature` (or `role="enemy"` → `roleToProfile`) — spawn seeds surface Y; `<Creature>` CCT owns runtime Y. No AABB / `TerrainSpawned` / script foot snaps.

## Running Examples

From the example directory (each has its own `package.json`):

```bash
cd VibeGame/examples/hello-world
bun install
bun run dev
```

```bash
cd VibeGame/examples/simple-rpg
bun install
bun run dev
```

```bash
cd VibeGame/examples/simple-racer
bun install
bun run dev
```

From the **VibeGame** package root, if a root script `bun run example` exists, use it; otherwise run `dev` inside the example folder as above.

## Adding New Examples

1. Create a new directory under `examples/`
2. Copy structure from `hello-world/` (minimal deps + Vite)
3. Update `package.json` scripts if needed
4. Add a `context.md` following the hello-world template
