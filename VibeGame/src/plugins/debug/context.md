# Debug Plugin

<!-- LLM:OVERVIEW -->
Runtime state introspection bridge for AI-driven debug cycles. Exposes `window.__VIBEGAME__` in the browser with methods to snapshot game state, query entities, inspect components, and single-step the simulation. Only activates in browser context (not headless).
<!-- /LLM:OVERVIEW -->

## Layout

```
debug/
├── context.md  # This file
├── index.ts    # Public exports (DebugPlugin, VibeGameDebugBridge)
└── plugin.ts   # DebugPlugin definition + bridge implementation
```

## Scope

- **In-scope**: State introspection, entity/component queries, simulation stepping, JSON snapshots for AI agents
- **Out-of-scope**: Profiling, performance metrics, network debugging, visual debug overlays

## Entry Points

- **plugin.ts**: DebugPlugin — initializes `window.__VIBEGAME__` bridge
- **index.ts**: Exports `DebugPlugin` and `VibeGameDebugBridge` type

## Dependencies

- **Internal**: Core ECS (State, defineQuery, Component, getComponentNames, getNamedEntities)
- **External**: bitecs

<!-- LLM:REFERENCE -->
### Interface: VibeGameDebugBridge

```typescript
interface VibeGameDebugBridge {
  state: State;
  snapshot(options?: Record<string, unknown>): string;
  entities(): Array<{ eid: number; name: string | null; components: Record<string, Record<string, number>> }>;
  entity(name: string): { eid: number; name: string; components: Record<string, Record<string, number>> } | null;
  component(eid: number, name: string): Record<string, number> | null;
  query(...componentNames: string[]): number[];
  componentNames(): string[];
  namedEntities(): Array<{ name: string; eid: number }>;
  step(dt?: number): void;
}
```

### Methods

#### snapshot(options?): string
JSON string of full game state (entities, components, elapsed time)

#### entities(): EntityData[]
All entities with their component names and field values (typed arrays introspected)

#### entity(name): EntityData | null
Find entity by name (requires `name="..."` attribute in XML)

#### component(eid, name): Record<string, number> | null
Get all field values for a specific component on an entity

#### query(...componentNames): number[]
Entity IDs matching all given component names

#### componentNames(): string[]
All registered component names in the state

#### namedEntities(): Array<{ name, eid }>
Entities that have a registered name

#### step(dt?): void
Advance simulation by one step (defaults to frame delta)
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples

### Browser Console

```javascript
const bridge = window.__VIBEGAME__;

const snapshot = bridge.snapshot();
const entities = bridge.entities();
const names = bridge.componentNames();
const waterEntities = bridge.query("water");
const entity = bridge.entity("hero");
bridge.step(1/60);
```

### Playwright Integration

```typescript
import { GameInspector } from '../helpers/game-inspector';

const inspector = new GameInspector(page);
await inspector.waitForBridge();
const entities = await inspector.entities();
const snapshot = await inspector.snapshot();
```
<!-- /LLM:EXAMPLES -->
