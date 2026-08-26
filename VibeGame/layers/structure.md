# Project Structure

VibeGame - A vibe coding game engine using ECS architecture with bitECS, featuring a Bevy-inspired plugin system and A-frame-style declarative XML recipes.

**Required**: For comprehensive documentation, use Context7 to fetch vibegame docs

## Stack

- Runtime: Bun/Node.js
- Language: TypeScript 5.6
- Physics: Rapier 3D WASM
- Build: Vite 5.4 with TypeScript declarations

## Commands

- Build: `bun run build` (vibegame core only)
- Build: `bun run build:release` (all builds including CDN standalone)
- Example: `bun run example` (build and run demo application)
- Type Check: `bun run check` (TypeScript validation)
- Lint: `bun run lint --fix` (ESLint code analysis and formatting)
- Test: `bun test tests/unit` (full unit suite — must stay green); see [`docs/TESTING.md`](../docs/TESTING.md)
- Test one plugin: `bun test tests/unit/<plugin> tests/unit/plugins/<plugin>`
- Playwright: `bun run test:playwright`

## AI Context

**llms.txt** is automatically built from [layers/llms-template.txt](llms-template.txt) on `bun run build:release`. It serves as a comprehensive system prompt containing all engine documentation, component references, and usage patterns. The template pulls in reference material from context.md files throughout the codebase.

## Layout

```
vibegame/
├── AGENTS.md  # Global context (Tier 0)
├── create-vibegame/  # Project scaffolding CLI
│   ├── index.js  # CLI script
│   ├── package.json
│   └── template/  # Project template files
├── src/
│   ├── core/  # Engine foundation
│   │   ├── context.md  # Core module context
│   │   ├── ecs/  # ECS scheduler, state, ordering
│   │   ├── xml/  # XML parsing and entity creation
│   │   ├── math/  # Math utilities
│   │   ├── utils/  # Core utilities
│   │   └── index.ts  # Core exports
│   ├── plugins/  # Plugin modules (~49 plugins; see Plugin Registry)
│   │   ├── defaults.ts  # DefaultPlugins bundle
│   │   ├── rpg-bundle.ts  # Opt-in RpgPlugins bundle
│   │   └── …  # One folder per plugin (animation, physics, terrain, rpg-*, etc.)
│   ├── vite/  # Vite plugins
│   │   ├── index.ts  # Plugin exports
│   │   ├── console-plugin.ts  # Console forwarding
│   │   └── context.md  # Module context
│   ├── cli/  # Headless CLI utilities
│   │   ├── index.ts  # CLI exports
│   │   ├── headless.ts  # Headless state creation
│   │   ├── queries.ts  # Entity/sequence query utilities
│   │   ├── text.ts  # Typr.js text measurement
│   │   └── context.md  # Module context
│   ├── builder.ts  # Builder pattern API
│   ├── runtime.ts  # Game runtime engine
│   └── index.ts  # Main exports
├── examples/  # Shipped example applications (see examples/context.md)
│   ├── hello-world/  # Minimal: terrain, physics, <GameObject place="…">
│   │   ├── README.md
│   │   ├── context.md
│   │   ├── src/main.ts
│   │   ├── index.html
│   │   ├── package.json
│   │   └── vite.config.ts
│   ├── simple-rpg/  # Full AiGameKit pipeline demo + handoff
│   │   ├── README.md
│   │   ├── index.html
│   │   ├── src/main.ts
│   │   ├── public/assets/
│   │   └── sample-gameassets/  # perfis batch (binários em public/assets/)
│   ├── simple-racer/  # Racing plugin demo; Vale carts + shared packs
│   └── simple-farm/   # Isometric Harvest-Moon-style farming loop
│       ├── index.html
│       ├── src/main.ts        # boot + plugins + game layer wiring
│       ├── src/game/          # tools/stamina/sleep/shop game systems
│       ├── public/world/      # environment/terrain/farm/village/vegetation XML
│       └── public/data/       # crops/items/tools YAML (DataRegistry)
├── layers/
│   ├── structure.md  # Project-level context (Tier 1)
│   ├── context-template.md  # Template for context files
|   └── llms-template.md # Template for llms.txt
├── dist/  # Built output
├── tests/
│   ├── context.md  # Test tree overview
│   ├── unit/  # Unit tests (per-plugin dirs + vite/ + core/)
│   ├── integration/  # Integration tests
│   ├── e2e/  # End-to-end tests (bun)
│   └── playwright/  # Playwright E2E with debug bridge introspection
│       ├── helpers/  # GameInspector, visual, interaction helpers
│       ├── fixtures/  # Custom Playwright fixtures
│       └── context.md  # Playwright test context
├── package.json
├── tsconfig.json
├── vite.config.ts
├── .prettierrc  # Code formatting config
├── .prettierignore  # Prettier ignore patterns
├── eslint.config.js  # Linting configuration
└── README.md
```

## Plugin Architecture

### Standard Plugin Structure

Every plugin follows a predictable file structure for easy context loading:

- **index.ts** - Public API exports (front-facing interface)
- **plugin.ts** - Plugin definition bundling components, systems, recipes, and config
- **components.ts** - ECS component definitions (data structures)
- **systems.ts** - System definitions with logic factored out
- **recipes.ts** - Entity-component bundles for XML creation
- **utils.ts** - Business logic and helper functions

Optional files:

- **operations.ts** - Complex operations and algorithms
- **constants.ts** - Plugin-specific constants
- **parser.ts** - Custom tag parsing logic for XML elements
- **math.ts** - Mathematical utilities

### Plugin Registry

Bundles (not individual `plugin.ts` folders):

- **defaults.ts** — `DefaultPlugins` (standard engine stack; tree-shake via `withoutPlugins`)
- **rpg-bundle.ts** — `RpgPlugins` (opt-in RPG stack for games like simple-rpg)

All plugins under `src/plugins/*/plugin.ts` (see registry). Related modules without a top-level `*Plugin` export (e.g. `spawn-variation`) are helpers used by spawner/vegetation/nature.

| #   | Folder            | Export                                                                            |
| --- | ----------------- | --------------------------------------------------------------------------------- |
| 1   | adaptive-quality  | AdaptiveQualityPlugin                                                             |
| 2   | ai-yuka           | YukaAiPlugin                                                                      |
| 3   | animation         | AnimationPlugin                                                                   |
| 4   | audio             | AudioPlugin                                                                       |
| 5   | biomes            | BiomesPlugin                                                                      |
| 6   | bvh               | BvhPlugin                                                                         |
| 7   | combat            | CombatPlugin                                                                      |
| 8   | composition       | CompositionPlugin                                                                 |
| 9   | debug             | DebugPlugin                                                                       |
| 10  | destructible      | DestructiblePlugin                                                                |
| 10b | daycycle          | DayCyclePlugin (day/night calendar driving sky + ambient; `<Clock>` widget)       |
| 10c | farm-plot         | FarmPlotPlugin (`<FarmPlot>` tile grid, crops, instanced field render)            |
| 11  | entity-script     | EntityScriptPlugin                                                                |
| 12  | floating-text     | FloatingTextPlugin                                                                |
| 13  | gltf-anim         | GltfAnimPlugin                                                                    |
| 14  | gltf-xml          | GltfXmlPlugin                                                                     |
| 15  | group             | GroupPlugin                                                                       |
| 16  | hud               | HudPlugin (+ `HudRpgPlugin` in `hud/rpg-plugin.ts`)                               |
| 17  | i18n              | I18nPlugin                                                                        |
| 18  | input             | InputPlugin                                                                       |
| 18b | isometric-camera  | IsometricCameraPlugin (true isometric ortho rig; Q/E quadrants, wheel zoom)       |
| 19  | loading           | LoadingPlugin                                                                     |
| 20  | navmesh           | NavMeshPlugin                                                                     |
| 21  | orbit-camera      | OrbitCameraPlugin                                                                 |
| 22  | particles         | ParticlesPlugin                                                                   |
| 23  | physics           | PhysicsPlugin                                                                     |
| 24  | player            | PlayerPlugin                                                                      |
| 25  | player-controller | ThirdPersonCameraPlugin                                                           |
| 26  | postprocessing    | PostprocessingPlugin                                                              |
| 27  | quests            | QuestsPlugin                                                                      |
| 28  | raycast           | RaycastPlugin                                                                     |
| 29  | rendering         | RenderingPlugin                                                                   |
| 30  | road              | RoadPlugin                                                                        |
| 31  | rpg-ai            | RpgAiPlugin                                                                       |
| 32  | rpg-core          | RpgCorePlugin, RpgCoreEventsPlugin                                                |
| 33  | rpg-economy       | EconomyPlugin                                                                     |
| 34  | rpg-inventory     | InventoryPlugin                                                                   |
| 35  | rpg-pause         | PauseCoordinatorPlugin                                                            |
| 36  | rpg-progression   | ProgressionPlugin                                                                 |
| 37  | rpg-resource-node | ResourceNodePlugin                                                                |
| 38  | rpg-status        | StatusEffectsPlugin                                                               |
| 39  | rpg-vault         | RpgVaultPlugin                                                                    |
| 40  | save-load         | SaveLoadPlugin                                                                    |
| 41  | sky               | SkyPlugin (EquirectSky + procedural Sky)                                          |
| 41b | world-border      | WorldBorderPlugin                                                                 |
| 42  | spawn-gate        | SpawnGatePlugin                                                                   |
| 43  | spawn-variation   | helpers (`resolveVariationSpec`, presets) — used by spawner / vegetation / nature |
| 44  | spawner           | SpawnerPlugin                                                                     |
| 45  | nature            | NaturePlugin (`<NatureSpawner>` rule-driven composite scatter)                    |
| 46  | startup           | StartupPlugin                                                                     |
| 47  | terrain           | TerrainPlugin                                                                     |
| 48  | transforms        | TransformsPlugin                                                                  |
| 49  | tweening          | TweeningPlugin                                                                    |
| 50  | vegetation        | VegetationPlugin (`<Vegetation>` smart carpet)                                    |
| 51  | water             | WaterPlugin                                                                       |
| 52  | weather           | WeatherPlugin                                                                     |
| 53  | chrono            | ChronoPlugin (opt-in time travel; core API in `core/ecs/chrono.ts`)               |

**Note**: XML recipes and core ECS live under `src/core/recipes/` — not a plugin. Individual plugins define their own recipes in `recipes.ts`.

## Architecture

Bevy-inspired ECS with explicit update phases:

- **SetupBatch**: Input gathering and frame setup
- **FixedBatch**: Physics simulation and gameplay logic (50 Hz fixed step)
- **SimulationBatch**: Interpolation and presentation prep (e.g. physics interpolation)
- **LateBatch**: Deferred gameplay hooks (entity scripts, post-physics fixes)
- **DrawBatch**: Rendering and visual updates

### Declarative Design

- Plugin definitions are self-documenting through structure
- Components define data without behavior
- Systems contain logic with dependencies declared
- Recipes enable XML-based entity creation like A-frame
- Config bundles all parsing-related settings (defaults, shorthands, enums, validations, parsers)

### Live Worlds & Time Travel

- **World hot-swap**: `GameRuntime.reloadWorld()` / `Scene.swap` replace only the entities created by the last world parse — player, cameras and runtime spawns survive. Dev runs auto-watch the `<scene>` DOM; `vibegameWorldHmr()` (Vite) forwards `.xml` saves from disk. Failures keep the previous world and show an in-page error overlay.
- **Chrono**: `enableChrono(state, {seconds, hz})` records a snapshot ring buffer; `chronoRewind`/`chronoSeek` restore in place (named entities keep their eid) and fire `onChronoSeek` listeners (physics resync) plus the `vibegame:chrono-seek` window event.
- **Reactive queries**: `watchQuery(state, components, {onAdded, onRemoved})` flushes membership diffs once per frame in `late`.

## Entry Points

- **Package entry**: src/index.ts (namespace API with builder pattern)
- **Core module**: src/core/index.ts (ECS foundation, types, utilities)
- **Plugin modules**: src/plugins/\*/index.ts (individual plugin exports)
- **Vite plugin**: src/vite/index.ts (WASM setup for Rapier physics)
- **CLI module**: src/cli/index.ts (headless state, XML parsing)
- **Builder API**: src/builder.ts (fluent builder pattern)
- **Runtime**: src/runtime.ts (game runtime engine)
- **Example apps**: examples/\*/src/main.ts (demo applications)

## Naming Conventions

**All files and directories use kebab-case**

- Files: `components.ts`, `systems.ts`, `utils.ts`, `plugin.ts`
- Directories: `src/`, `core/`, `plugins/`, `orbit-camera/`, `input/`
- Components: PascalCase exports from `components.ts`
- Systems: PascalCase with `System` suffix from `systems.ts`
- Plugins: PascalCase with `Plugin` suffix from `plugin.ts`
- Recipes: camelCase exports from `recipes.ts`

## Configuration

- TypeScript: tsconfig.json (strict mode, ES2020 target, DOM types)
- Build: vite.config.ts (library mode, ESM output, DTS generation)
- Package: package.json (main package with plugin exports)
- Code Quality: eslint.config.js (TypeScript linting), .prettierrc (formatting)

## Where to Add Code

### Adding to Existing Plugin

1. Components → src/plugins/[plugin-name]/components.ts
2. Systems → src/plugins/[plugin-name]/systems.ts
3. Recipes → src/plugins/[plugin-name]/recipes.ts
4. Utils → src/plugins/[plugin-name]/utils.ts
5. Update exports → src/plugins/[plugin-name]/index.ts
6. Register in plugin → src/plugins/[plugin-name]/plugin.ts

### Creating New Plugin

1. Create directory → src/plugins/[plugin-name]/
2. Add standard files:
   - index.ts (exports)
   - plugin.ts (plugin definition)
   - components.ts (if needed)
   - systems.ts (if needed)
   - recipes.ts (if needed)
   - utils.ts (if needed)
   - context.md (folder documentation)
3. Add export to main package.json
4. Add to DefaultPlugins if standard (otherwise tree-shaken)

### Core Modifications

- ECS changes → src/core/ecs/
- XML parsing → src/core/xml/
- Math utilities → src/core/math/
- Core types → src/core/ecs/types.ts
