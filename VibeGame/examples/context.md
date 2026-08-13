# Examples

<!-- LLM:OVERVIEW -->

Shipped examples in this repository: **hello-world** (minimal terrain + physics + deterministic placement), **simple-rpg** (full AiGameKit pipeline demo, Crystal Vale), and **simple-racer** (racing plugin + shared Vale packs). Shared GameAssets manifests for forest/village/infra live in [`shared-assets/`](shared-assets/README.md).
<!-- /LLM:OVERVIEW -->

## Purpose

- Demonstrate engine capabilities
- Provide integration reference for the declarative world XML
- Test plugin combinations (terrain, spawner, particles, etc.)

## Layout

```
examples/
├── context.md          # This file
├── shared-assets/      # Crystal Vale forest/village/infra manifests + sync-from-rpg.sh
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
│   └── sample-gameassets/  # game.yaml + manifests (forest/village/infra = symlinks)
└── simple-racer/       # Racing plugin; Vale carts + copied RPG scenery
    ├── PROGRESS.md
    ├── index.html
    ├── src/
    └── sample-gameassets/
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
