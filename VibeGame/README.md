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

| Flag                                       | Description                                             |
| ------------------------------------------ | ------------------------------------------------------- |
| `--install` / `-i`                         | Force `bun install` in the engine directory             |
| `--skip-install` / `--skip-engine-install` | Skip `bun install` when deps are already present        |
| `--skip-build`                             | Skip the engine build step                              |
| `--skip-app-install`                       | Skip `bun install` in the app directory                 |
| `--` (separator)                           | All arguments after `--` are forwarded to `bun run dev` |

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

System execution groups (in order): `setup` → `fixed` (physics tick) → `simulation` → `late` (UI/HUD/coroutine updates that must follow simulation) → `draw` (render).

---

## Declarative Scene (XML)

### Available Elements

Only recipes that exist in source are listed. See the [Plugin Reference](#plugin-reference) below and [`docs/PLUGINS.md`](docs/PLUGINS.md) for the full inventory.

| Element               | Description                               | Key Attributes                                  |
| --------------------- | ----------------------------------------- | ----------------------------------------------- |
| `<Scene>`             | Root container for all entities           | `canvas`, `sky`                                 |
| `<static-part>`       | Static physics body with renderer         | `pos`, `shape`, `size`, `color`, `body`         |
| `<dynamic-part>`      | Dynamic physics body with renderer        | `pos`, `shape`, `size`, `color`, `body`         |
| `<kinematic-part>`    | Kinematic physics body with renderer      | `pos`, `shape`, `size`, `color`, `body`         |
| `<GameObject>`        | Generic entity with component mapping     | `transform`, `body`, `renderer`, `collider`     |
| `<Composition>`       | Group of children with a shared transform | `pos`, `children`                               |
| `<GLTFLoader>`        | Load a static GLB/GLTF model              | `url`, `pos`, `scale`, `rotation`               |
| `<GLTFDynamic>`       | Load a dynamic (physics) GLB/GLTF model   | `url`, `pos`, `scale`, `rotation`               |
| `<Player>`            | Capsule player controller                 | `speed`, `jump`                                 |
| `<PlayerGLTF>`        | Animated player character (WASD + camera) | `pos`, `model-url`, `speed`, `jump`             |
| `<OrbitCamera>`       | Orbital camera with zoom                  | `distance`, `angle`                             |
| `<ThirdPersonCamera>` | Third-person follow camera                | `target`, `distance`, `height`                  |
| `<EquirectSky>`       | Equirectangular sky (PMREM IBL)           | `url`, `rotation-deg`, `set-background`         |
| `<Terrain>`           | Terrain with LOD from heightmap           | `heightmap-url`, `size`, `collision-resolution` |
| `<BiomeRegion>`       | Biome region marker for terrain spawning  | `biome`, `bounds`                               |
| `<AudioSource>`       | Audio emitter (Howler)                    | `src`, `loop`, `volume`, `spatial`              |
| `<SpawnGroup>`        | Batch-spawn entities on terrain           | `profile`, `count`, `density-per-km2`           |
| `<StaticSpawner>`     | Spawn static props on terrain             | `profile`, `count`, `density-per-km2`           |
| `<DynamicSpawner>`    | Spawn dynamic objects on terrain          | `profile`, `count`, `density-per-km2`           |
| `<SpawnGate>`         | Gated spawner trigger                     | `profile`, `trigger`                            |
| `<ParticleSystem>`    | Particle emitter                          | `preset`                                        |
| `<ParticleBurst>`     | One-shot particle burst                   | `preset`                                        |
| `<NavMesh>`           | Navigation mesh surface for AI            | `url`                                           |
| `<NavMeshWalkable>`   | Walkable area definition                  | `bounds`                                        |
| `<NavMeshAgent>`      | AI navigation agent                       | `target`                                        |
| `<HudPanel>`          | On-screen HUD overlay (world-space)       | `position`, `size`                              |
| `<HudScreenLayer>`    | Screen-space HUD layer container          | `anchor`                                        |
| `<Minimap>`           | Minimap HUD widget                        | `range`, `size`, `anchor`                       |
| `<DialogueNPC>`       | NPC with a dialogue tree                  | `dialogue`, `name`                              |
| `<ResourceNode>`      | Harvestable resource node                 | `resource`, `yield`                             |
| `<MonoBehaviour>`     | Per-entity script (Unity-style)           | `script`                                        |

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

  <!-- Equirectangular sky (PMREM IBL + background) -->
  <EquirectSky url="/assets/sky/equirect.png" set-background="true"></EquirectSky>

  <!-- Terrain with heightmap -->
  <Terrain heightmap-url="/assets/terrain/heightmap.png" size="256"></Terrain>

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

A component is a plain object literal of typed arrays (Struct-of-Arrays), declared with `as const` and registered with `withComponent(name, component)`. This matches how every built-in component is defined (see `Transform` in `src/plugins/transforms/components.ts`).

```html
<Scene canvas="#game-canvas" sky="#87ceeb">
  <static-part pos="0 -0.5 0" shape="box" size="20 1 20" color="#90ee90"></static-part>

  <!-- Entity with custom component data-attribute -->
  <GameObject my-component="10"></GameObject>
</Scene>

<script type="module">
  import * as GAME from 'vibegame';

  // Component = plain object of typed arrays (one slot per entity), `as const`.
  // Size arrays to the engine's MAX_ENTITIES capacity (100000).
  const MAX_ENTITIES = 100000;
  const MyComponent = {
    value: new Float32Array(MAX_ENTITIES),
  } as const;

  const query = GAME.defineQuery([MyComponent]);

  const MySystem = {
    group: 'simulation',
    update: (state) => {
      for (const entity of query(state.world)) {
        MyComponent.value[entity] += 1;
      }
    },
  };

  GAME.withComponent('my-component', MyComponent)
    .withSystem(MySystem)
    .run();
</script>
```

Supported array types: `Float32Array` (f32), `Uint8Array` (ui8), `Uint16Array`, `Uint32Array` (ui32/eid), `Int8Array` (i8), `Int32Array` (i32).

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

### `validateGltf(input, options?)`

Validate a GLB/glTF asset at runtime and return a structured report. Accepts a URL string (fetched via `fetch`), raw GLB bytes (`ArrayBuffer`/`Uint8Array`), or bare glTF JSON text bytes. The glTF-transform modules are imported lazily, so the main bundle is unaffected unless this is called.

The report exposes `valid` (no error-severity issues), plus `errors`, `warnings`, `infos`, and a combined `issues` array. Each issue has a stable `code` (e.g. `ASSET_VERSION_MISSING`), a human-readable `message`, and an RFC-6901 JSON `pointer` (e.g. `/asset/version`). Issue codes include: `ASSET_VERSION_MISSING`, `ASSET_VERSION_FORMAT`, `SCENE_INDEX_OUT_OF_RANGE`, `BUFFERVIEW_BUFFER_OUT_OF_RANGE`, `ACCESSOR_BUFFERVIEW_OUT_OF_RANGE`, `MESH_PRIMITIVE_NO_POSITION`, `GLTF_PARSE_FAILED`, plus `INSPECT_*` advisories folded in from `@gltf-transform/functions`' `inspect`.

> **Note:** This is a focused engine-bundled structural validator, not the full Khronos `gltf-validator`. For exhaustive spec validation use `gamedev-lab check glb` or the `gltf-validator` CLI.

```ts
import { validateGltf } from 'vibegame';

const report = await validateGltf('/assets/models/hero.glb');
if (!report.valid) {
  for (const issue of report.errors) {
    console.error(`${issue.code} at ${issue.pointer}: ${issue.message}`);
  }
}

// Skip advisory checks (duplicate materials, unused textures) for a fast spec-only pass:
const fast = await validateGltf(bytes, { includeAdvisory: false });
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

| Input            | Action                    |
| ---------------- | ------------------------- |
| W A S D          | Move (relative to camera) |
| Space            | Jump                      |
| Right mouse drag | Orbit camera              |
| Mouse wheel      | Zoom                      |

---

## Plugin Reference

VibeGame includes 44 plugins (29 registered by default, 15 opt-in) organized by category. See [`src/plugins/README.md`](src/plugins/README.md) for the full architecture and plugin creation template, and [`docs/PLUGINS.md`](docs/PLUGINS.md) for the complete list.

### Core

| Plugin          | Description                                              | Export Path              |
| --------------- | -------------------------------------------------------- | ------------------------ |
| `transforms`    | Position, rotation, scale 3D (Transform, WorldTransform) | `vibegame/transforms`    |
| `physics`       | Rapier physics simulation + colliders                    | `vibegame/physics`       |
| `rendering`     | Three.js renderer, cameras, scenes (MeshRenderer)        | `vibegame/rendering`     |
| `input`         | Keyboard, mouse, gamepad input                           | `vibegame/input`         |
| `startup`       | Deferred post-initialization execution                   | `vibegame/startup`       |
| `animation`     | Animation clips, AnimatedCharacter, HasAnimator          | `vibegame/animation`     |
| `bvh`           | Bounding Volume Hierarchy raycasting acceleration        | `vibegame/bvh`           |
| `composition`   | Entity groups with shared transform (`<Composition>`)    | `vibegame/composition`   |
| `entity-script` | Per-entity MonoBehaviour scripts (`<MonoBehaviour>`)     | `vibegame/entity-script` |

### Camera

| Plugin              | Description                                        |
| ------------------- | -------------------------------------------------- |
| `orbit-camera`      | Orbital camera (`<OrbitCamera>`) with zoom and pan |
| `player-controller` | Third-person follow camera (`<ThirdPersonCamera>`) |

### Player

| Plugin      | Description                                                          |
| ----------- | -------------------------------------------------------------------- |
| `player`    | Player controller (WASD, jump, character controller)                 |
| `gltf-anim` | GLTF animation plugin — registers and updates GltfAnimator instances |

### 3D Models

| Plugin     | Description                                                 |
| ---------- | ----------------------------------------------------------- |
| `gltf-xml` | Declarative GLB/GLTF loading via `<GLTFLoader>` XML element |

### Environment

| Plugin    | Description                                                    |
| --------- | -------------------------------------------------------------- |
| `sky`     | Equirectangular sky + IBL (PMREM) via `<EquirectSky>`          |
| `terrain` | Terrain with LOD from heightmaps via `<Terrain>`               |
| `biomes`  | Biome regions driving terrain-aware spawning (`<BiomeRegion>`) |

### Post-Processing

| Plugin           | Description                                                                                                          |
| ---------------- | -------------------------------------------------------------------------------------------------------------------- |
| `postprocessing` | Bloom, SMAA, dithering, tonemapping (effect registry API — see [`docs/EFFECT-REGISTRY.md`](docs/EFFECT-REGISTRY.md)) |

### Logic & Gameplay

Registered by default.

| Plugin          | Description                                                                                           |
| --------------- | ----------------------------------------------------------------------------------------------------- |
| `tweening`      | Smooth interpolation (tweens) via GSAP (`<Tween>`)                                                    |
| `spawner`       | `<SpawnGroup>`, `<StaticSpawner>`, `<DynamicSpawner>` for batch-spawning entities on terrain          |
| `audio`         | Spatial audio via Howler — `<AudioSource>`, `playAudioEmitter` (see [`docs/AUDIO.md`](docs/AUDIO.md)) |
| `hud`           | On-screen HUD panels and widgets (`<HudPanel>`, `<Minimap>`, HealthBar, XpBar, etc.)                  |
| `raycast`       | Raycasting for interaction (`<RaycastSource>`)                                                        |
| `navmesh`       | Navigation mesh and agents (`<NavMesh>`, `<NavMeshWalkable>`, `<NavMeshAgent>`)                       |
| `ai-steering`   | Autonomous NPC wandering via Yuka (`<NPC>`)                                                           |
| `particles`     | Particle systems and bursts (three.quarks)                                                            |
| `floating-text` | Floating damage / combat text overlays                                                                |
| `destructible`  | Destructible props and breakable objects                                                              |
| `quests`        | Dialogue and quests (`<DialogueNPC>`, `<QuestsTab>`, `<DialogueBalloon>`)                             |

### Opt-in (register with `withPlugin`)

Not in `DefaultPlugins`. Add via the [builder API](#builder-api).

| Plugin              | Description                                                      |
| ------------------- | ---------------------------------------------------------------- |
| `save-load`         | Save/load game state to localStorage (msgpackr)                  |
| `i18n`              | Internationalization with locale auto-detection (`<I18nText>`)   |
| `loading`           | Loading screen and asset progress tracking                       |
| `debug`             | Debug overlays (wireframes, stats, post-FX toggle)               |
| `combat`            | Combat system (factions, projectiles)                            |
| `spawn-gate`        | Gated spawner triggers (`<SpawnGate>`)                           |
| `rpg-core`          | RPG data containers and loot tables (`<RpgData>`, `<LootTable>`) |
| `rpg-ai`            | RPG enemy AI (`<MeleeAi>`)                                       |
| `rpg-economy`       | Shops and price tables (`<PriceTable>`)                          |
| `rpg-inventory`     | Inventory system (`<Inventory>`)                                 |
| `rpg-pause`         | Pause coordination (`<PauseCoordinator>`)                        |
| `rpg-progression`   | XP and leveling (`<Progression>`)                                |
| `rpg-resource-node` | Harvestable resources (`<ResourceNode>`)                         |
| `rpg-status`        | Status effects (poison, heal-over-time, buffs)                   |
| `rpg-vault`         | Persistent item storage (`<Vault>`)                              |

### Planned (not yet implemented)

The following appear in older docs or roadmap notes but have **no source directory** under `src/plugins/`. They are tracked as future work, not shipping features.

`follow-camera` (use `player-controller` / `<ThirdPersonCamera>` today), `fog`, `water`, `joints`, `lod`, `network`, `respawn`, `sprite`, `line`, `text`, `text-3d`, `scene-manifest` (asset manifests load via the GLTF bridge and `gameassets handoff` instead).

### Creating a New Plugin

Each plugin lives in `src/plugins/<name>/` with a standard structure:

```
plugins/
└── my-plugin/
    ├── plugin.ts        # Export MyPlugin: Plugin (required)
    ├── components.ts    # Typed-array component object (SOA) + `as const`
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

| Element                 | Source                  | Plugin/Recipe                                              |
| ----------------------- | ----------------------- | ---------------------------------------------------------- |
| Animated player (WASD)  | GameAssets → Animator3D | `<PlayerGLTF>`                                             |
| Terrain (256m, LOD)     | Built-in                | `<Terrain>`                                                |
| Sky IBL + background    | Skymap2D (equirect PNG) | `<EquirectSky>`                                            |
| Stone pillars (30)      | Text3D + Paint3D        | `<SpawnGroup>`                                             |
| Lowpoly trees (density) | Text3D                  | `<SpawnGroup>`                                             |
| Pushable crates (30)    | Text3D                  | `<SpawnGroup profile="gltf-crate">`                        |
| BGM + SFX               | Text2Sound              | `<AudioSource>` + `playAudioEmitter`                       |
| Particles (fire, rain)  | Built-in                | `<ParticleSystem>`                                         |
| AI-steering NPCs (3)    | Built-in                | `AiSteeringPlugin` (`<NPC>`)                               |
| Save / Load             | Built-in                | `SaveLoadPlugin` (Q/E keys)                                |
| i18n (EN/PT)            | Built-in                | `I18nPlugin`                                               |
| Follow camera + post-fx | Built-in                | `<ThirdPersonCamera>` + `postprocessing` (bloom, vignette) |

> **Note:** This table reflects the recipes that actually exist in the engine. Earlier revisions advertised `<Skybox>`, `<Water>`, `<Fog>`, and `<FollowCamera>`, which are not real recipes (`<Skybox>` is `<EquirectSky>`, `<FollowCamera>` is `<ThirdPersonCamera>`, and `<Water>`/`<Fog>` have no plugin yet). The `simple-rpg` example itself is being refreshed in Q9; the stone-pillars/crates spawn rows above are provisional and may change. See [`examples/simple-rpg/README.md`](examples/simple-rpg/README.md) for the current walkthrough.

**Controls:** WASD move, Space jump, Shift sprint, J attack/harvest, F interact, K trade, B bomb, V cycle weapon, 1 potion, 2 antidote, C dash, E heal, R power strike, Q pause menu (save/load via Options tab). Right mouse drag orbit, Mouse wheel zoom.

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
│   ├── plugins/           — Plugin implementations (44)
│   │   ├── rendering/     — Three.js renderer, cameras, scenes
│   │   ├── physics/       — Rapier physics + colliders
│   │   ├── player/        — Player controller (PlayerGLTF recipe)
│   │   ├── gltf-xml/      — Declarative GLB loading
│   │   ├── gltf-anim/     — GLTF animation system
│   │   ├── terrain/       — Terrain with LOD
│   │   ├── sky/           — Equirect sky + IBL (PMREM)
│   │   ├── audio/         — Howler-based spatial audio
│   │   ├── spawner/       — Batch entity spawning on terrain
│   │   ├── particles/     — Particle system (three.quarks)
│   │   └── ...            — 30+ more plugins
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

| Tool                                | Output                      | VibeGame Loading                                           |
| ----------------------------------- | --------------------------- | ---------------------------------------------------------- |
| **Text3D** → Paint3D                | Static GLB                  | `<GLTFLoader url="…">` or `loadGltfToScene()`              |
| **Text3D** → Rigging3D → Animator3D | Animated GLB                | `<PlayerGLTF model-url="…">` or `loadGltfAnimated()`       |
| **Skymap2D**                        | Equirect PNG (2:1)          | `<EquirectSky url="…">` or `applyEquirectSkyEnvironment()` |
| **Text2Sound**                      | WAV audio                   | `<AudioSource src="…">` or `playAudioEmitter()`            |
| **Terrain3D**                       | heightmap.png, terrain.json | `<Terrain heightmap-url="…">`                              |
| **Texture2D**                       | Seamless 2D textures        | Loaded as material maps via GLB                            |

The `gameassets handoff` command copies generated assets into `public/assets/` and prefers animated GLBs when present.

See [`docs/ASSET-PIPELINE.md`](docs/ASSET-PIPELINE.md) and [`docs/MONOREPO_GAME_PIPELINE.md`](../docs/MONOREPO_GAME_PIPELINE.md).

---

## Documentation

| Document                                                                 | Description                                      |
| ------------------------------------------------------------------------ | ------------------------------------------------ |
| [`docs/PLUGINS.md`](docs/PLUGINS.md)                                     | Complete plugin list with details                |
| [`docs/SHARED.md`](docs/SHARED.md)                                       | Shared module (types, math, validation)          |
| [`docs/EFFECT-REGISTRY.md`](docs/EFFECT-REGISTRY.md)                     | Post-processing effect system                    |
| [`docs/ASSET-PIPELINE.md`](docs/ASSET-PIPELINE.md)                       | GameAssets Python → VibeGame pipeline            |
| [`docs/AUDIO.md`](docs/AUDIO.md)                                         | Audio system (Howler, XML, autoplay)             |
| [`src/plugins/README.md`](src/plugins/README.md)                         | Plugin architecture + template                   |
| [`../AGENTS.md`](../AGENTS.md)                                           | Monorepo agent guide (CLI commands, conventions) |
| [`../docs/MONOREPO_GAME_PIPELINE.md`](../docs/MONOREPO_GAME_PIPELINE.md) | Full monorepo pipeline layout                    |
| [`../docs/ZERO_TO_GAME_AI.md`](../docs/ZERO_TO_GAME_AI.md)               | AI-centric workflow and `dream` command          |

---

## Links

**Upstream VibeGame (original project):** [dylanebert/vibegame on GitHub](https://github.com/dylanebert/vibegame) | [npm package](https://www.npmjs.com/package/vibegame) | [Hugging Face Space](https://huggingface.co/spaces/dylanebert/VibeGame) | [JSFiddle](https://jsfiddle.net/keLsxh5t/)

**This fork in the GameDev monorepo:** [maikramer/GameDev — VibeGame/](https://github.com/maikramer/GameDev/tree/main/VibeGame)

- [Shallot Context Manager](https://github.com/dylanebert/shallot) — AI context management for vibe coding sessions

---

## License

MIT

VibeGame is based on [vibegame](https://github.com/dylanebert/vibegame) by [dylanebert](https://github.com/dylanebert).
