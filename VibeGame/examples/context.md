# Examples

<!-- LLM:OVERVIEW -->
Shipped examples in this repository: **hello-world** (minimal terrain + physics + deterministic placement) and **simple-rpg** (full GameDev pipeline demo). Other paths may exist in forks or history; these two are the maintained references.
<!-- /LLM:OVERVIEW -->

## Purpose

- Demonstrate engine capabilities
- Provide integration reference for the declarative world XML
- Test plugin combinations (terrain, spawner, particles, etc.)

## Layout

```
examples/
├── context.md          # This file
├── hello-world/        # Minimal: terrain, dynamic body, <GameObject place="…">
│   ├── context.md
│   ├── src/main.ts
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
└── simple-rpg/         # Full monorepo pipeline + GLBs, NPCs, save/load, i18n
    ├── README.md
    ├── index.html
    ├── src/main.ts
    ├── public/assets/  # After handoff / batch
    └── sample-gameassets/  # Optional gameassets dream + batch profile
```

## Deterministic terrain placement (entity-centric)

Use **`<GameObject place="at: x z; …">`** (not a separate wrapper tag): one root entity is anchored to the terrain at XZ; optional keys match the internal `place` profile (`base-y-offset`, `y-offset`, `ground-align`, `align-to-terrain`, …). Child recipes (`GLTFLoader`, `ParticleSystem`, `NPC` with merge, etc.) hang under that root. See [Spawner plugin context](../src/plugins/spawner/context.md).

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

From the **VibeGame** package root, if a root script `bun run example` exists, use it; otherwise run `dev` inside the example folder as above.

## Adding New Examples

1. Create a new directory under `examples/`
2. Copy structure from `hello-world/` (minimal deps + Vite)
3. Update `package.json` scripts if needed
4. Add a `context.md` following the hello-world template
