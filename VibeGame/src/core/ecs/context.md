# ECS Module

<!-- LLM:OVERVIEW -->

Entity Component System scheduler and state management. Provides the State class for world management, system scheduling with execution phases, and plugin registration.
<!-- /LLM:OVERVIEW -->

## Purpose

- World state with entities and components
- System scheduling with execution phases
- Plugin registration and management
- Direct bitECS query usage (no abstraction)

## Layout

```
ecs/
├── context.md  # This file
├── components.ts  # Core ECS components
├── component-storage.ts  # defineComponent: lazy SoA field arrays
├── config.ts  # Configuration registry
├── constants.ts  # ECS constants and limits
├── ordering.ts  # System execution ordering
├── scheduler.ts  # Batch scheduler implementation
├── state.ts  # World state management
├── types.ts  # Core ECS types
├── utils.ts  # Component field utilities
└── index.ts  # Module exports
```

## Scope

- **In-scope**: ECS architecture, scheduling, state
- **Out-of-scope**: Specific components/systems

## Component storage is lazy

Declare components with `defineComponent({ posX: F32, dirty: U8 })` — never with
`new Float32Array(MAX_ENTITIES)` literals. Every field is a
`TypedArray[MAX_ENTITIES]`, so eager literals made the barrel import allocate
**~214 MB** before a single entity existed, in every game, for all ~104
components whether the plugin was registered or not. `defineComponent` keeps the
same `Component.field[eid]` access and allocates on the component's first field
touch (all fields at once, then plain data properties). Note that *enumerating*
a component counts as a touch. Non-zero defaults: `filled(U8, 1)`.

`MAX_ENTITIES` stays at 100 000, overridable with
`globalThis.VIBEGAME_MAX_ENTITIES` set **before** importing the engine.
Raising it is **not** a linear trade: measured on simple-rpg (same scene, heap
right after boot) 100k → 1781 MB but 150k → 3669 MB, near Chrome's ~4 GB limit.
`State.createEntity` warns at 90% and throws at the cap; past it, component
writes fall outside the array and are silently dropped. simple-rpg sits at
93 194 (93%), so the headroom fix is fewer entities — instanced foliage
spending one entity per blade — not a bigger cap.

## Entry Points

- **state.ts**: State class for world management
- **scheduler.ts**: Scheduler for system batches
- **types.ts**: Plugin, System, Component interfaces

## Dependencies

- **Internal**: None
- **External**: bitECS

## Execution Phases

### Frame Execution Flow

Each frame executes systems in three distinct phases:

1. **SetupBatch** (Once per frame)
   - Input gathering and processing
   - Frame state initialization
   - Runs exactly once at frame start

2. **FixedBatch** (0-N times per frame at 50 Hz)
   - Physics simulation step
   - Gameplay logic requiring deterministic timing
   - Accumulates time and catches up if behind
   - Example: At 25 FPS runs 2x per frame, at 144 FPS runs ~0.4x per frame
   - Always uses `fixedDeltaTime` (1/50 second)

3. **SimulationBatch** (Once per frame, after fixed)
   - Physics transform interpolation (`alpha` from fixed accumulator)
   - Presentation systems that need smoothed poses before draw

4. **LateBatch** (Once per frame, after simulation)
   - Deferred entity-script / gameplay hooks that run after interpolation

5. **DrawBatch** (Once per frame)
   - Rendering and visual updates
   - Runs at frame end
   - Uses variable `deltaTime` for smooth animations

<!-- LLM:REFERENCE -->

## API Reference

### Exported Classes

#### State

World container managing entities, components, systems, and plugins. See main core/context.md for full API.

### Exported Constants

- `NULL_ENTITY: 4294967295` - Invalid entity ID
- `TIME_CONSTANTS.FIXED_TIMESTEP: 1/50` - Fixed update rate (50 Hz)
- `TIME_CONSTANTS.DEFAULT_DELTA: 1/144` - Default frame delta

### Exported Types

- `System` - System definition interface
- `Plugin` - Plugin bundle interface
- `Recipe` - Entity recipe definition
- `Config` - Configuration interface
- `GameTime` - Time tracking interface
- `Parser` - XML tag parser function type
- `Adapter` - Property adapter function for custom handling (e.g., strings)
- `ComponentDefaults` - Default values mapping
- `ComponentEnums` - Enum value mappings
- `ShorthandMapping` - Attribute shorthand
- `ValidationRule` - Validation rule interface

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Examples

### System Execution Order

```typescript
import * as GAME from 'vibegame';

const EarlySystem: GAME.System = {
  group: 'setup',
  first: true,
  update: (state) => { /* runs first in setup */ }
};

const LateSystem: GAME.System = {
  group: 'draw',
  last: true,
  update: (state) => { /* runs last in draw */ }
};

const OrderedSystem: GAME.System = {
  after: [OtherSystem],
  before: [AnotherSystem],
  update: (state) => { /* runs between systems */ }
};
```

<!-- /LLM:EXAMPLES -->

## Orçamento de entidades

bitecs distribui ids sem tecto, mas **todos** os componentes deste motor são
`TypedArray[MAX_ENTITIES]` (100 000). Um id acima disso não endereça nada:
escritas fora do range de um TypedArray são **descartadas em silêncio** e as
leituras devolvem `undefined` — a entidade existe, as queries apanham-na, e os
dados dela não colam. Como isso é indiagnosticável pelos sintomas,
`State.createEntity` avisa aos 90 % e emite erro na parede.

Quem bate no aviso tem dois botões: baixar a densidade que gera entidades
(vegetação, spawners) ou subir `MAX_ENTITIES` — e pagar a memória em todos os
componentes. O `simple-rpg` corre a ~95,7 k.
