# VibeGame — 3D Game Engine for the Web

A browser-based 3D game engine designed for vibe coding. Declarative HTML-like scene syntax, ECS architecture with bitecs, Three.js rendering, and game-ready features including physics, terrain, animation, audio, particles, and player controls — all out of the box.

<div align="center">

[![npm](https://img.shields.io/npm/v/vibegame)](https://www.npmjs.com/package/vibegame)
[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Spaces-blue)](https://huggingface.co/spaces/dylanebert/VibeGame)
[![JSFiddle](https://img.shields.io/badge/JSFiddle-Try%20It-blue)](https://jsfiddle.net/keLsxh5t/)

</div>

## Overview

VibeGame is a browser-based 3D game engine built on bitecs (ECS), Three.js (rendering), and Vite (build tooling). It supports declarative scene definition via XML, GLB loading with animation, equirectangular skybox environment maps (PMREM/IBL), terrain with LOD, particle effects, 3D audio, AI steering, save/load, i18n, and more.

**Key technologies:**

- **bitecs** — High-performance Entity Component System (data-oriented)
- **Three.js** — 3D rendering, PBR materials, post-processing
- **Vite** — Fast build tooling and dev server
- **Bun** — Test runner and package manager
- **Rapier** — Physics engine (via `@dimforge/rapier3d-compat`)

---

## Installation

### npm / Bun

```bash
bun install vibegame
```

### Unified Installer (GameDev monorepo)

With the [GameDev](https://github.com/maikramer/GameDev) repo checked out, **Bun** and **Node** on `PATH`:

```bash
cd /path/to/GameDev
./install.sh vibegame
# or: python3 -m gamedev_shared.installer.unified vibegame
```

This runs `bun install --frozen-lockfile`, builds the package with `bun run build`, and installs the `vibegame` CLI into `~/.local/bin` (wrapper pointing to `scripts/vibegame-cli.mjs`).

---

## CLI

### `vibegame create <name>`

Scaffold a new VibeGame project from a template. Creates a complete project with `llms.txt` system prompt for AI-assisted development.

```bash
vibegame create my-game
cd my-game
bun dev
```

### `vibegame run`

Run the development server with hot reload. Designed for apps that depend on the engine via `file:../VibeGame` — it builds the engine first, then starts Vite.

```bash
vibegame run
vibegame run -- --host    # pass --host to Vite
```

| Flag | Description |
|------|-------------|
| `--install` / `-i` | Force `bun install` in the engine directory |
| `--skip-install` / `--skip-engine-install` | Skip `bun install` when deps are already present |
| `--skip-build` | Skip the engine build step |
| `--skip-app-install` | Skip `bun install` in the app directory |
| `--` (separator) | All arguments after `--` are forwarded to `bun run dev` |

### `vibegame --version`

Show the installed VibeGame version.

---

## Core Concepts

### Entity Component System (ECS)

VibeGame uses [bitecs](https://github.com/NateTheGreatt/bitecs) for a data-oriented ECS architecture, inspired by Bevy:

- **Entities** — Numeric IDs (`eid`). Lightweight identifiers.
- **Components** — Pure data stored in typed arrays (SOA — Struct of Arrays). No logic.
- **Systems** — Functions that iterate over entities matching a query, executing logic each frame.
- **Plugins** — Self-contained modules that bundle components, systems, recipes, and config.

Component types available: `f32`, `ui8`, `ui32`, `eid` (entity reference), `i8`, `i32`.

### World XML

Scene content is defined declaratively in `index.html` using custom XML elements inside `<Scene>`. The engine parses these elements at startup and creates the corresponding ECS entities with components.

```html
<Scene canvas="#game-canvas" sky="#87ceeb">
  <!-- All entities defined here -->
</Scene>
```

> **Important:** Content under `<Scene>` is injected as `innerHTML`. The native HTML `<script>` tag does NOT work for engine TypeScript modules — use the `script` attribute on recipes or a custom element name that doesn't collide with HTML.

### Recipes

Recipes are predefined entity templates (e.g., `GLTFLoader`, `PlayerGLTF`, `OrbitCamera`, `static-part`) that map XML attributes to ECS components. They simplify entity creation by bundling a set of components with sensible defaults and attribute shorthands.

```html
<!-- Recipe: bundles transform + body + collider + renderer in one element -->
<static-part pos="0 -0.5 0" shape="box" size="20 1 20" color="#90ee90"></static-part>
```

### Plugins

Plugins extend the engine with new component types, systems, recipes, and configuration. Each plugin follows a standard interface:

```ts
export interface Plugin {
  readonly systems?: readonly System[];
  readonly recipes?: readonly Recipe[];
  readonly components?: Record<string, Component>;
  readonly config?: Config;
  readonly initialize?: (state: State) => void | Promise<void>;
}
```

Plugins are registered via `DefaultPlugins` in `defaults.ts`, or added at runtime with the builder API:

```ts
import * as GAME from 'vibegame';

GAME.withPlugin(SaveLoadPlugin)
  .withPlugin(I18nPlugin)
  .run();
```

System execution groups (in order): `setup` → `fixed` (physics tick) → `simulation` → `draw` (render).

---

## Declarative Scene (XML)

### Available Elements

| Element | Description | Key Attributes |
|---------|-------------|----------------|
| `<Scene>` | Root container for all entities | `canvas`, `sky` |
| `<static-part>` | Static physics body with renderer | `pos`, `shape`, `size`, `color`, `body` |
| `<dynamic-part>` | Dynamic physics body with renderer | `pos`, `shape`, `size`, `color`, `body` |
| `<GameObject>` | Generic entity with component mapping | `transform`, `body`, `renderer`, `collider` |
| `<GLTFLoader>` | Load a static GLB/GLTF model | `url`, `pos`, `scale`, `rotation` |
| `<PlayerGLTF>` | Animated player character (WASD + camera) | `pos`, `model-url`, `speed`, `jump` |
| `<Player>` | Capsule player controller | `speed`, `jump` |
| `<OrbitCamera>` | Orbital camera with zoom | `distance`, `angle` |
| `<FollowCamera>` | Third-person follow camera | (configured via profile) |
| `<Skybox>` | Equirectangular sky (PMREM IBL) | `url` |
| `<Terrain>` | Terrain with LOD from heightmap | `url`, `size`, `collision-resolution` |
| `<Water>` | Water plane with reflections | (configured via profile) |
| `<Fog>` | Atmospheric fog | `mode`, `density`, `color` |
| `<AudioSource>` | Audio emitter (Howler) | `src`, `loop`, `volume`, `spatial` |
| `<SpawnGroup>` | Batch-spawn entities on terrain | `profile`, `count`, `density-per-km2` |
| `<ParticleSystem>` | Particle emitter | `preset` |
| `<ParticleBurst>` | One-shot particle burst | `preset` |
| `<NavMeshSurface>` | Navigation mesh for AI pathfinding | `url` |
| `<NavMeshAgent>` | AI navigation agent | `target` |
| `<HudPanel>` | On-screen HUD overlay | `position`, `size` |

### Example Scene

```html
<Scene canvas="#game-canvas" sky="#87ceeb">

  <!-- Ground plane -->
  <static-part pos="0 -0.5 0" shape="box" size="20 1 20" color="#90ee90"></static-part>

  <!-- Dynamic ball -->
  <dynamic-part pos="-2 4 -3" shape="sphere" size="1" color="#ff4500"></dynamic-part>

  <!-- Static GLB prop -->
  <GLTFLoader url="/assets/models/tree.glb" pos="5 0 -3"></GLTFLoader>

  <!-- Animated player character -->
  <PlayerGLTF pos="0 0 0" model-url="/assets/models/hero.glb"></PlayerGLTF>

  <!-- Skybox with equirectangular image -->
  <Skybox url="/assets/sky/equirect.png"></Skybox>

  <!-- Terrain with heightmap -->
  <Terrain url="/assets/terrain/heightmap.png" size="256"></Terrain>

  <!-- Water plane -->
  <Water></Water>

  <!-- Atmospheric fog -->
  <Fog mode="exp" density="0.02" color="#c8d8e8"></Fog>

  <!-- Background music -->
  <AudioSource src="/assets/audio/bgm.mp3" loop volume="0.3"></AudioSource>

  <!-- Particle system -->
  <ParticleSystem preset="rain"></ParticleSystem>

</Scene>

<canvas id="game-canvas"></canvas>

<script type="module">
  import * as GAME from 'vibegame';
  GAME.run();
</script>
```

### CSS-Style Shorthand Expansion

Attributes on recipes are expanded via shorthands and adapters defined in each plugin's config. For example, `pos="5 0 -3"` expands to set `Transform.position` x/y/z components. Color strings like `color="#ff4500"` are parsed by adapters into float R/G/B values on the renderer component.

### Custom Components and Systems

Register custom components and systems to handle arbitrary game logic:

```html
<Scene canvas="#game-canvas" sky="#87ceeb">
  <static-part pos="0 -0.5 0" shape="box" size="20 1 20" color="#90ee90"></static-part>

  <!-- Entity with custom component data-attribute -->
  <GameObject my-component="10"></GameObject>
</Scene>

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

---

## GLTF Bridge

The GLTF bridge (`vibegame/extras/gltf-bridge`) provides functions for loading GLB/GLTF assets produced by the GameDev pipeline (Text3D, Paint3D, Rigging3D, Animator3D). All loaders attach KTX2 texture support when available and apply default shadow flags (`castShadow` + `receiveShadow`).

### `loadGltfToScene(state, url)`

Load a static GLB into the scene. Returns a `Promise<Group>` (Three.js root object).

```ts
import { loadGltfToScene, run } from 'vibegame';

const state = await run();
const group = await loadGltfToScene(state, '/assets/models/prop.glb');
```

### `loadGltfAnimated(state, url)`

Load a GLB with embedded animation clips. Returns a `Promise<GLTF>` containing `{ scene, animations }`. Prefer this when using `GltfAnimator`.

```ts
import { loadGltfAnimated, run } from 'vibegame';

const state = await run();
const gltf = await loadGltfAnimated(state, '/assets/models/hero.glb');
console.log('Clips:', gltf.animations.map(a => a.name));
```

### `loadGltfToSceneWithAnimator(state, url, options?)`

Load a GLB and wrap any embedded clips in a `GltfAnimator`. Returns `{ group, animator }` where `animator` is `null` if no clips are found.

```ts
import { loadGltfToSceneWithAnimator, run } from 'vibegame';

const state = await run();
const { group, animator } = await loadGltfToSceneWithAnimator(state, '/assets/models/hero.glb', {
  crossfadeDuration: 0.3,
});

if (animator) {
  animator.play('idle');
}
```

### `loadGltfLodToScene(state, urls)`

Load three LOD variants (LOD0/1/2), group them in a single `Group`, and add to the scene. Children are named `lod0`–`lod2`; only one is visible at a time.

```ts
import { loadGltfLodToScene, run } from 'vibegame';

const state = await run();
const root = await loadGltfLodToScene(state, [
  '/assets/models/tree_lod0.glb',
  '/assets/models/tree_lod1.glb',
  '/assets/models/tree_lod2.glb',
]);
```

### `GltfAnimator`

Runtime animation controller for GLTF models with embedded clips. Wraps Three.js `AnimationMixer` with crossfade and state management.

```ts
import { GltfAnimator } from 'vibegame';

// Create from a loaded GLTF result
const animator = new GltfAnimator(gltf, { crossfadeDuration: 0.25 });

// List available clips
console.log(animator.clipNames); // ['idle', 'walk', 'run', 'jump']

// Play with automatic crossfade
animator.play('walk');

// Play once (no loop)
animator.play('attack', { loop: false });

// Play with custom crossfade duration
animator.play('run', { crossfade: 0.5 });

// Get currently active clip
console.log(animator.activeClipName);

// Tick the mixer — call every frame
animator.update(deltaTime);

// Clean up
animator.dispose();
```

### `applyEquirectSkyEnvironment(state, url, options?)`

Load an equirectangular image (PNG/JPG/HDR), generate a PMREM environment map, and apply as scene background and environment for PBR lighting.

```ts
import { applyEquirectSkyEnvironment, run } from 'vibegame';

const state = await run();
await applyEquirectSkyEnvironment(state, '/assets/sky/equirect.png', {
  background: true,            // also set scene.background (default: true)
  rotationDeg: 90,            // rotate panorama horizontally (default: 0)
  environmentIntensity: 0.15,  // PMREM intensity (default: ~0.1)
});
```

> **Note:** Three.js equirectangular convention — center of image = horizon, top = zenith, bottom = nadir. Textures should be 2:1 aspect ratio (landscape, e.g. 2048×1024). The `rotationDeg` option rotates the bitmap at the pixel level (necessary because PMREM's internal shader ignores `texture.offset`).

### `autoLoadSkyEnvironment(state, options?)`

Auto-discover a sky texture by probing common paths (`/assets/sky/`, `/assets/skymaps/`, etc.) for files named `sky`, `environment`, `skybox`, or `equirect` with common extensions. Returns `true` if a sky was found and applied.

```ts
import { autoLoadSkyEnvironment, run } from 'vibegame';

const state = await run();
const found = await autoLoadSkyEnvironment(state);
if (!found) {
  console.log('No sky texture found — using solid color fallback.');
}
```

---

## PlayerGLTF Recipe

The `<PlayerGLTF>` recipe creates a fully playable third-person character in a single XML element:

```html
<PlayerGLTF pos="0 0 0" model-url="/assets/models/hero.glb"></PlayerGLTF>
```

Handles:
- GLB model loading with animation clips
- Animation controller (idle, walk, run, jump states)
- WASD movement relative to camera direction
- Jump with Space bar
- Third-person follow camera
- Physics collider and character controller

**Controls:**

| Input | Action |
|-------|--------|
| W A S D | Move (relative to camera) |
| Space | Jump |
| Right mouse drag | Orbit camera |
| Mouse wheel | Zoom |

---

## Plugin Reference

VibeGame includes 30+ plugins organized by category. See [`src/plugins/README.md`](src/plugins/README.md) for the full architecture and plugin creation template.

### Core

| Plugin | Description | Export Path |
|--------|-------------|-------------|
| `transforms` | Position, rotation, scale 3D (Transform, WorldTransform) | `vibegame/transforms` |
| `physics` | Rapier physics simulation + colliders | `vibegame/physics` |
| `rendering` | Three.js renderer, cameras, scenes (MeshRenderer) | `vibegame/rendering` |
| `input` | Keyboard, mouse, gamepad input | `vibegame/input` |
| `startup` | Deferred post-initialization execution | `vibegame/startup` |
| `animation` | Animation clips, AnimatedCharacter, HasAnimator | `vibegame/animation` |

### Camera

| Plugin | Description |
|--------|-------------|
| `orbit-camera` | Orbital camera with zoom and pan |
| `follow-camera` | Third-person follow camera with zoom presets |

### Player

| Plugin | Description |
|--------|-------------|
| `player` | Player controller (WASD, jump, character controller) |
| `gltf-anim` | GLTF animation plugin — registers and updates GltfAnimator instances |

### 3D Models

| Plugin | Description |
|--------|-------------|
| `gltf-xml` | Declarative GLB/GLTF loading via `<GLTFLoader>` XML element |
| `text-3d` | Text3D/Hunyuan GLB model loading |

### Environment

| Plugin | Description |
|--------|-------------|
| `sky` | Skybox with equirectangular IBL (PMREM) via `<Skybox>` |
| `fog` | Volumetric fog — exponential and linear modes |
| `water` | Water plane with physics, swimming, reflections |
| `terrain` | Terrain with LOD from heightmaps via `<Terrain>` |

### Post-Processing

| Plugin | Description |
|--------|-------------|
| `postprocessing` | Bloom, SMAA, dithering, tonemapping (effect registry API — see [`docs/EFFECT-REGISTRY.md`](docs/EFFECT-REGISTRY.md)) |

### Logic & Gameplay

| Plugin | Description |
|--------|-------------|
| `tweening` | Smooth interpolation (tweens) via GSAP |
| `spawner` | `<SpawnGroup>` for batch-spawning entities on terrain |
| `respawn` | Entity respawn system |
| `lod` | Level of Detail (near/far switching) |
| `audio` | Spatial audio via Howler — `<AudioSource>`, `playAudioEmitter` (see [`docs/AUDIO.md`](docs/AUDIO.md)) |
| `debug` | Debug overlays (wireframes, stats) |
| `entity-script` | Per-entity MonoBehaviour scripts (Unity-style) |
| `sprite` | 2D sprites |
| `line` | 3D lines |
| `hud` | On-screen HUD panels |
| `raycast` | Raycasting for interaction |
| `navmesh` | Navigation mesh and agents for AI pathfinding |
| `ai-steering` | Autonomous NPC wandering via Yuka |
| `particles` | Particle systems and bursts (three.quarks) |
| `joints` | Physics joints for connected objects |
| `combat` | Combat system |
| `save-load` | Save/load game state to localStorage (msgpackr) |
| `network` | Multiplayer networking via Colyseus |
| `i18n` | Internationalization with locale auto-detection |

### Pipeline

| Plugin | Description |
|--------|-------------|
| `scene-manifest` | Load scenes from GameAssets JSON manifest |

### Creating a New Plugin

Each plugin lives in `src/plugins/<name>/` with a standard structure:

```
plugins/
└── my-plugin/
    ├── plugin.ts        # Export MyPlugin: Plugin (required)
    ├── components.ts    # defineComponent() for ECS data
    ├── systems.ts       # Query + update logic per frame
    ├── recipes.ts       # Entity creation shortcuts
    ├── index.ts         # Public re-exports
    └── context.md       # AI agent notes (optional)
```

See [`src/plugins/README.md`](src/plugins/README.md) for the full template and detailed examples.

---

## Audio System

VibeGame provides a spatial audio system built on [Howler.js](https://howlerjs.com/):

- **`AudioListener`** — Attached to the main camera for 3D positional audio
- **`<AudioSource>`** — Declarative audio emitters in XML (BGM, ambient, SFX)
- **`playAudioEmitter(name)`** — Trigger named sound effects from code
- **`resumeAudioContextOnFirstUserGesture()`** — Handle browser autoplay restrictions

```html
<!-- Background music -->
<AudioSource src="/assets/audio/bgm.mp3" loop volume="0.3"></AudioSource>

<!-- Resume audio on user gesture (for autoplay policy) -->
```

```ts
import { playAudioEmitter, resumeAudioContextOnFirstUserGesture } from 'vibegame';

resumeAudioContextOnFirstUserGesture();

// Trigger SFX by registered name
playAudioEmitter('jump');
playAudioEmitter('save');
```

Audio files are typically generated by [Text2Sound](../Text2Sound/) and placed in `public/assets/audio/` via the `gameassets handoff` command.

See [`docs/AUDIO.md`](docs/AUDIO.md) for full documentation.

---

## Shared Module

`src/shared/` provides lightweight, engine-agnostic utilities used across plugins:

- **types** — `Vector3Like`, `ColorLike`, `AABB`, `QuaternionLike`, etc.
- **math** — `vec3`, `vec2`, `aabb` operations + `clamp`, `lerp`, `smoothstep`
- **validation** — Zod schemas for flexible XML/JSON parsing (`vector3Schema`, `colorSchema`, etc.)

See [`docs/SHARED.md`](docs/SHARED.md).

---

## Examples

### hello-world

Minimal example: [`VibeGame/examples/hello-world/`](examples/hello-world/)

- Single GLB in scene with `<GLTFLoader>`
- Orbit camera
- Demonstrates the simplest possible VibeGame project

### simple-rpg

Full pipeline example: [`VibeGame/examples/simple-rpg/`](examples/simple-rpg/)

End-to-end demo of the GameDev monorepo workflow:

| Element | Source | Plugin |
|---------|--------|--------|
| Animated player (WASD) | GameAssets → Animator3D | `<PlayerGLTF>` |
| Terrain (256m, LOD) | Built-in | `<Terrain>` |
| Sky IBL + background | Skymap2D (equirect PNG) | `<Skybox>` |
| Ocean water | Built-in | `<Water>` |
| Atmospheric fog | Built-in | `<Fog>` |
| Stone pillars (30) | Text3D + Paint3D | `<SpawnGroup>` |
| Lowpoly trees (density) | Text3D | `<SpawnGroup>` |
| Pushable crates (30) | Text3D | `<SpawnGroup profile="gltf-crate">` |
| BGM + SFX | Text2Sound | `<AudioSource>` + `playAudioEmitter` |
| Particles (fire, rain) | Built-in | `<ParticleSystem>` |
| AI-steering NPCs (3) | Built-in | `AiSteeringPlugin` |
| Save / Load | Built-in | `SaveLoadPlugin` (Q/E keys) |
| i18n (EN/PT) | Built-in | `I18nPlugin` |
| Follow camera + post-fx | Built-in | `<FollowCamera>` (bloom, vignette) |

**Controls:** WASD move, Space jump, Q save, E load, Right mouse drag orbit, Mouse wheel zoom.

See [`examples/simple-rpg/README.md`](examples/simple-rpg/README.md) for the full pipeline walkthrough.

---

## Development

```bash
cd VibeGame

# Install dependencies (frozen lockfile)
bun install --frozen-lockfile

# Build library
bun run build

# Run example (hello-world)
bun run example

# Run tests (unit + integration + e2e)
bun test tests/unit tests/integration tests/e2e

# TypeScript type check
bun run check    # tsc --noEmit

# Lint
bun run lint     # ESLint
bun run lint:fix # ESLint with auto-fix

# Format
bun run format       # Prettier write
bun run format:check # Prettier check

# Playwright E2E tests
bun run playwright:install
bun run test:playwright

# Clean build artifacts
bun run clean
```

### From the Monorepo (Make)

```bash
make test-vibegame      # Run bun install (frozen) + bun test
make check-vibegame     # tsc --noEmit
make lint-vibegame      # ESLint
make fmt-vibegame       # Prettier --write
make fmt-check-vibegame # Prettier --check
make build-vibegame     # Vite build
```

### Peer Dependencies

VibeGame requires `bitecs >= 0.3.40` and `three >= 0.183.0` as peer dependencies.

---

## Project Layout

```
VibeGame/
├── src/
│   ├── core/              — ECS core (State, World, System, Component, query)
│   ├── plugins/           — Plugin implementations (30+)
│   │   ├── rendering/     — Three.js renderer, cameras, scenes
│   │   ├── physics/       — Rapier physics + colliders
│   │   ├── player/        — Player controller (PlayerGLTF recipe)
│   │   ├── gltf-xml/      — Declarative GLB loading
│   │   ├── gltf-anim/     — GLTF animation system
│   │   ├── terrain/       — Terrain with LOD
│   │   ├── sky/           — Skybox + equirectangular IBL
│   │   ├── audio/         — Howler-based spatial audio
│   │   ├── spawner/       — Batch entity spawning on terrain
│   │   ├── particles/     — Particle system (three.quarks)
│   │   └── ...            — 20+ more plugins
│   ├── extras/            — GLTF bridge, sky-env, animator, loading progress
│   │   ├── gltf-bridge.ts — loadGltfToScene, loadGltfAnimated, etc.
│   │   ├── gltf-animator.ts — GltfAnimator class
│   │   └── sky-env.ts     — applyEquirectSkyEnvironment
│   ├── shared/            — Types, math, validation (engine-agnostic)
│   ├── cli/               — CLI module
│   ├── vite/              — Vite plugin + hot-reload client
│   ├── builder.ts         — GameBuilder (fluent API)
│   ├── runtime.ts         — GameRuntime
│   ├── defaults.ts        — DefaultPlugins array
│   └── index.ts           — Public API (re-exports everything)
├── examples/
│   ├── hello-world/       — Minimal GLB + orbit camera
│   └── simple-rpg/        — Full pipeline demo
├── scripts/
│   ├── vibegame-cli.mjs   — CLI entry point (bin)
│   ├── prepare.ts         — Release build preparation
│   └── clean.mjs          — Clean build artifacts
├── tests/
│   ├── unit/              — Unit tests
│   ├── integration/       — Integration tests
│   └── e2e/               — End-to-end tests
├── docs/
│   ├── PLUGINS.md         — Complete plugin list
│   ├── SHARED.md          — Shared module documentation
│   ├── EFFECT-REGISTRY.md — Post-processing effect system
│   ├── ASSET-PIPELINE.md  — GameAssets → VibeGame pipeline
│   └── AUDIO.md           — Audio system documentation
├── package.json
├── tsconfig.json
├── vite.config.ts
├── llms.txt               — AI system prompt documentation
└── README.md              — This file
```

---

## Builder API

The `GameBuilder` provides a fluent API for configuring and running the engine:

```ts
import * as GAME from 'vibegame';

// Add plugins
GAME.withPlugin(SaveLoadPlugin)
  .withPlugins(I18nPlugin, ParticlesPlugin);

// Remove default plugins
GAME.withoutDefaultPlugins();
GAME.withoutPlugins(PhysicsPlugin);

// Add custom systems and components
GAME.withSystem(MySystem)
  .withComponent('my-component', MyComponent);

// Configure options
GAME.configure({ canvas: '#game-canvas', sky: '#87ceeb' });

// Run the engine
await GAME.run();

// Reset for re-configuration
GAME.resetBuilder();
```

---

## GLB Handoff from GameDev Pipeline

Assets generated by the GameDev monorepo tools can be loaded directly into VibeGame:

| Tool | Output | VibeGame Loading |
|------|--------|------------------|
| **Text3D** → Paint3D | Static GLB | `<GLTFLoader url="…">` or `loadGltfToScene()` |
| **Text3D** → Rigging3D → Animator3D | Animated GLB | `<PlayerGLTF model-url="…">` or `loadGltfAnimated()` |
| **Skymap2D** | Equirect PNG (2:1) | `<Skybox url="…">` or `applyEquirectSkyEnvironment()` |
| **Text2Sound** | WAV audio | `<AudioSource src="…">` or `playAudioEmitter()` |
| **Terrain3D** | heightmap.png, terrain.json | `<Terrain url="…">` |
| **Texture2D** | Seamless 2D textures | Loaded as material maps via GLB |

The `gameassets handoff` command copies generated assets into `public/assets/` and prefers animated GLBs when present.

See [`docs/ASSET-PIPELINE.md`](docs/ASSET-PIPELINE.md) and [`docs/MONOREPO_GAME_PIPELINE.md`](../docs/MONOREPO_GAME_PIPELINE.md).

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/PLUGINS.md`](docs/PLUGINS.md) | Complete plugin list with details |
| [`docs/SHARED.md`](docs/SHARED.md) | Shared module (types, math, validation) |
| [`docs/EFFECT-REGISTRY.md`](docs/EFFECT-REGISTRY.md) | Post-processing effect system |
| [`docs/ASSET-PIPELINE.md`](docs/ASSET-PIPELINE.md) | GameAssets Python → VibeGame pipeline |
| [`docs/AUDIO.md`](docs/AUDIO.md) | Audio system (Howler, XML, autoplay) |
| [`src/plugins/README.md`](src/plugins/README.md) | Plugin architecture + template |
| [`../AGENTS.md`](../AGENTS.md) | Monorepo agent guide (CLI commands, conventions) |
| [`../docs/MONOREPO_GAME_PIPELINE.md`](../docs/MONOREPO_GAME_PIPELINE.md) | Full monorepo pipeline layout |
| [`../docs/ZERO_TO_GAME_AI.md`](../docs/ZERO_TO_GAME_AI.md) | AI-centric workflow and `dream` command |

---

## Links

**Upstream VibeGame (original project):** [dylanebert/vibegame on GitHub](https://github.com/dylanebert/vibegame) | [npm package](https://www.npmjs.com/package/vibegame) | [Hugging Face Space](https://huggingface.co/spaces/dylanebert/VibeGame) | [JSFiddle](https://jsfiddle.net/keLsxh5t/)

**This fork in the GameDev monorepo:** [maikramer/GameDev — VibeGame/](https://github.com/maikramer/GameDev/tree/main/VibeGame)

- [Shallot Context Manager](https://github.com/dylanebert/shallot) — AI context management for vibe coding sessions

---

## License

MIT

VibeGame is based on [vibegame](https://github.com/dylanebert/vibegame) by [dylanebert](https://github.com/dylanebert).
