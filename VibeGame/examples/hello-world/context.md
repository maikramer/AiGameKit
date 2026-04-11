# Hello World Example

<!-- LLM:OVERVIEW -->
Minimal example: procedural terrain, one dynamic physics sphere, and a small **`<GameObject place="…">`** demo (terrain-sampled XZ + surface Y) with a child `ParticleSystem`. Entry is `GAME.run()` with default plugins. Use as a template for new Vite + VibeGame apps.
<!-- /LLM:OVERVIEW -->

## Purpose

- Show the smallest `index.html` + `main.ts` integration
- Demonstrate **entity-centric placement** (`place` attribute on `<GameObject>`)
- Demonstrate physics with `<dynamic-part>`

## Layout

```
hello-world/
├── context.md  # This file
├── src/
│   └── main.ts  # Entry: GAME.run()
├── index.html      # World XML (terrain, dynamic-part, entity + particles)
├── package.json
├── vite.config.ts
└── tsconfig.json
```

## Scope

- **In-scope**: Declarative scene, default plugins, one placement demo
- **Out-of-scope**: GLB handoff, save/load, custom pipeline

## Entry Points

- **`src/main.ts`**: `import * as GAME from 'vibegame'; GAME.run();`
- **`index.html`**: `<Scene>` + canvas + Vite module entry

## Features Demonstrated

- `<Terrain>` — procedural LOD terrain
- `<dynamic-part>` — Rapier dynamic body (sphere)
- **`<GameObject place="at: x z; …">`** — deterministic XZ + terrain height; `ParticleSystem` as child (local transform)
- Default plugins (physics, rendering, particles, spawner, etc.)

## Running

```bash
cd VibeGame/examples/hello-world
bun install
bun run dev
```

## Related

- Spawner / placement behaviour: [`../../src/plugins/spawner/context.md`](../../src/plugins/spawner/context.md)
- Larger demo: [`../simple-rpg/README.md`](../simple-rpg/README.md)

<!-- LLM:EXAMPLES -->
## Code snippet — entity placement

```xml
<GameObject place="at: 0 -12; base-y-offset: 0.02">
  <ParticleSystem     preset="fire"
    rate="12"
    transform="pos: 0 0.25 0"
  ></ParticleSystem>
</GameObject>
```

The root entity is positioned on the terrain; emitters (or `GLTFLoader`, `NPC`, etc.) are children in the ECS hierarchy.
<!-- /LLM:EXAMPLES -->
