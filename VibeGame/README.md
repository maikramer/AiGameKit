# VibeGame

A 3D game engine designed for vibe coding. Declarative HTML-like syntax, ECS architecture, and game-ready features including physics, rendering, and player controls out of the box.

<div align="center">

[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Spaces-blue)](https://huggingface.co/spaces/dylanebert/VibeGame)
[![JSFiddle](https://img.shields.io/badge/JSFiddle-Try%20It-blue)](https://jsfiddle.net/keLsxh5t/)

</div>

## Problem

Vibe coding games works at first, but falls apart as the project grows.

## Quick Start

### Create a new project

```bash
npm create vibegame@latest my-game

cd my-game
bun dev
```

This scaffolds a complete project with `llms.txt` system prompt for AI-assisted development.

### From the GameDev monorepo (unified installer)

With the [GameDev](https://github.com/maikramer/GameDev) repo checked out, **Bun** and **Node** on `PATH`:

```bash
cd /path/to/GameDev
./install.sh vibegame
vibegame create my-game
```

Runs `bun install`, builds the package, and installs the `vibegame` CLI into `~/.local/bin` (see root `docs/INSTALLING.md`).

#### `vibegame run` (apps with `file:…/VibeGame`)

From an example app or a project that lists `vibegame` as a `file:` dependency, run the dev server with the local engine built first:

```bash
vibegame run
vibegame run -- --host
```

Useful flags: `--install` / `-i` (force `bun install` in the engine), `--skip-engine-install` / `--skip-install`, `--skip-build`, `--skip-app-install`. Arguments after `--` are passed to `bun run dev` (e.g. Vite). If the command cannot find the engine, run from the app directory that depends on the engine via `file:` or from a folder under the monorepo tree. See [`scripts/vibegame-cli.mjs`](scripts/vibegame-cli.mjs) and the monorepo [`AGENTS.md`](../AGENTS.md).

**GLB handoff (Text3D / Paint3D / `gameassets` batch):** import **`loadGltfToScene`** for static meshes; for **animated** rigged GLBs (Animator3D clips), use **`loadGltfAnimated`**, **`loadGltfToSceneWithAnimator`**, or **`GltfAnimator`** from `vibegame`. Declarative: **`<gltf-load url="…">`** for props; **`<player-gltf model-url="…">`** for a third-person character with idle/walk/run. See [examples/monorepo-game](examples/monorepo-game/) and [MONOREPO_GAME_PIPELINE.md](../docs/MONOREPO_GAME_PIPELINE.md). **Walkable demo + handoff:** [examples/simple-rpg](examples/simple-rpg/). **Céu equirect / IBL:** **`<sky url="…">`** no XML ou `applyEquirectSkyEnvironment` em código. **Áudio:** **`<audio-clip>`** + `playAudioEmitter` — ver [`docs/AUDIO.md`](docs/AUDIO.md). **`gameassets handoff`** copies into `public/assets` and can prefer animated GLBs when present.

### Or install directly

```bash
bun install vibegame
```

```html
<world canvas="#game-canvas" sky="#87ceeb">
  <!-- Ground -->
  <static-part pos="0 -0.5 0" shape="box" size="20 1 20" color="#90ee90"></static-part>

  <!-- Ball -->
  <dynamic-part pos="-2 4 -3" shape="sphere" size="1" color="#ff4500"></dynamic-part>
</world>

<canvas id="game-canvas"></canvas>

<script type="module">
  import * as GAME from 'vibegame';
  GAME.run();
</script>
```

## Solution

### 1. AI Context Management

**System Prompt**: Include `llms.txt` in your AI system prompt for comprehensive VibeGame documentation.

**Comprehensive Documentation**: Use Context7 to fetch detailed documentation:

```typescript
// Use mcp__context7__resolve-library-id to find "vibegame"
// Then use mcp__context7__get-library-docs for full documentation
```

**Context Workflow**: Use [Shallot](https://github.com/dylanebert/shallot) to manage context across conversations:

- Use `/peel` at conversation start to load necessary context
- Use `/nourish` at conversation end to update context

### 2. ECS Architecture with Plugins

Inspired by Bevy, VibeGame uses an Entity Component System architecture with plugins:

- **Components**: Pure data structures without behavior
- **Systems**: Logic separated from data
- **Plugins**: Self-contained modules that bundle related functionality

### 3. Declarative XML Syntax

Entities and components defined declaratively in HTML:

```html
<world canvas="#game-canvas" sky="#87ceeb">
  <static-part pos="0 -0.5 0" shape="box" size="20 1 20"></static-part>
</world>
```

### 4. Roblox-like Abstraction

Game-ready features out of the box:

- Controllable character
- Physics simulation
- Camera controls
- Rendering pipeline
- Input handling

## Core Concepts

### World

All entities are defined within the `<world>` tag:

```html
<world canvas="#game-canvas" sky="#87ceeb">
  <!-- All entities and components here -->
</world>
```

### Basic Entities and Components

Entities and components can be defined with a CSS-like syntax:

```html
<world canvas="#game-canvas" sky="#87ceeb">
  <entity
    transform
    body="type: 1; pos: 0 -0.5 0"
    renderer="shape: box; size: 20 1 20; color: 0x90ee90"
    collider="shape: box; size: 20 1 20"
  ></entity>
</world>
```

or, with CSS-style shorthand expansion:

```html
<world canvas="#game-canvas" sky="#87ceeb">
  <entity
    transform
    renderer
    collider
    pos="0 -0.5 0"
    body="type: 1"
    shape="box"
    size="20 1 20"
    color="#90ee90"
  ></entity>
</world>
```

or, with recipes (entity-component bundles):

```html
<world canvas="#game-canvas" sky="#87ceeb">
  <static-part pos="0 -0.5 0" shape="box" size="20 1 20" color="#90ee90"></static-part>
</world>
```

### Custom Systems

Register custom systems and components to handle arbitrary game logic:

```html
<world canvas="#game-canvas" sky="#87ceeb">
  <static-part pos="0 -0.5 0" shape="box" size="20 1 20" color="#90ee90"></static-part>

  <!-- Entity with custom component -->
  <entity my-component="10"></entity>
</world>

<script type="module">
  import * as GAME from 'vibegame';

  const MyComponent = GAME.defineComponent({
    value: GAME.Types.f32,
  });

  const query = GAME.defineQuery([MyComponent]);

  const MySystem: GAME.System = {
    update: (state: GAME.State): void => {
      const entities: number[] = query(state.world);
      for (const entity of entities) {
        console.log("my-component value for entity", entity, "is", MyComponent.value[entity]);
        MyComponent.value[entity] += 1;
      }
    },
  };

  GAME.withComponent('my-component', MyComponent)
    .withSystem(MySystem)
    .run();
</script>
```

When registered, the custom component `MyComponent` will be automatically parsed from HTML with value `10`. The custom system `MySystem` will be automatically run every frame, which will query for every entity with `my-component` and increment its value by 1.

## Development

```bash
# Install dependencies
bun install

# Run example (hello-world)
bun run example

# Build library (fast, library only)
bun run build

# Build for release (includes docs & CDN)
bun run build:release

# Run tests
bun test
```

## Plugins

VibeGame includes 20+ plugins for physics, rendering, player controls, and more.

| Category            | Plugins                                                                       |
| ------------------- | ----------------------------------------------------------------------------- |
| **Core**            | transforms, physics, rendering, input, startup                                |
| **Camera**          | orbit-camera, follow-camera                                                   |
| **Player**          | player, gltf-anim                                                             |
| **3D Models**       | gltf-xml, **text-3d** (Text3D/Hunyuan GLB)                                    |
| **Environment**     | **sky** (equirectangular IBL), fog, water                                     |
| **Post-processing** | bloom, SMAA, dithering, tonemapping ([registry API](docs/EFFECT-REGISTRY.md)) |
| **Logic**           | animation, tweening, spawner, respawn, lod, audio, debug                      |
| **Gameplay**        | raycast, navmesh, ai-steering, particles, hud, joints                         |
| **Opcionais**       | save-load (msgpackr), network (colyseus), i18n (`withPlugin`)                 |
| **Pipeline**        | scene-manifest (GameAssets JSON), TextureRecipe (Texture2D procedural)        |

Full list: [`docs/PLUGINS.md`](docs/PLUGINS.md)

## Documentation

| Doc                                                  | Description                             |
| ---------------------------------------------------- | --------------------------------------- |
| [`docs/PLUGINS.md`](docs/PLUGINS.md)                 | Lista completa de plugins               |
| [`docs/SHARED.md`](docs/SHARED.md)                   | Módulo shared (tipos, math, validation) |
| [`docs/EFFECT-REGISTRY.md`](docs/EFFECT-REGISTRY.md) | Sistema de efeitos pós-processamento    |
| [`docs/ASSET-PIPELINE.md`](docs/ASSET-PIPELINE.md)   | Pipeline GameAssets Python → VibeGame   |
| [`docs/AUDIO.md`](docs/AUDIO.md)                     | Sistema de áudio (Howler, XML, autoplay) |
| [`src/plugins/README.md`](src/plugins/README.md)     | Arquitetura de plugins + template       |

## Shared Module

`src/shared/` provides lightweight, engine-agnostic utilities used across plugins:

- **types** — `Vector3Like`, `ColorLike`, `AABB`, `QuaternionLike`, etc.
- **math** — `vec3`, `vec2`, `aabb` operations + `clamp`, `lerp`, `smoothstep`
- **validation** — Zod schemas for flexible XML/JSON parsing (`vector3Schema`, `colorSchema`, etc.)

See [`docs/SHARED.md`](docs/SHARED.md).

## Links

**Upstream VibeGame (original project):** [dylanebert/vibegame on GitHub](https://github.com/dylanebert/vibegame), [npm package](https://www.npmjs.com/package/vibegame), [Hugging Face Space](https://huggingface.co/spaces/dylanebert/VibeGame), [JSFiddle](https://jsfiddle.net/keLsxh5t/).

**This copy in the GameDev monorepo:** [maikramer/GameDev — `VibeGame/`](https://github.com/maikramer/GameDev/tree/main/VibeGame) (issues and PRs for the forked engine live on that repository).

- [Shallot Context Manager](https://github.com/dylanebert/shallot)
