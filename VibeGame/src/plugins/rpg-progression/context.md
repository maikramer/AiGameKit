# RPG Progression Plugin (context.md)

<!-- LLM:OVERVIEW -->

XP, leveling, skill ranks, and stat modifiers for RPG entities. Each entity with a `progression` component tracks `xp`, `level`, `unspentPoints`, and `spent` in typed arrays. XP curves are pluggable: the rpg-core data registry maps an `xp-curve` id to a function `(level) => xpNeeded`, with a built-in `default` curve of `5 + level`. `addXp` grants XP and loops `levelUp` while XP meets the curve threshold, granting `skillPointsPerLevel` points (default 3) per level. Skill ranks are bought with `spendSkillPoint` against `SkillDef` entries from the rpg-core registry; skills whose effect is `stat-modifier` are aggregated by `getStatModifiers` for combat/stats consumers, and `stack` mode scales magnitude by rank. Mutations never emit events inline: they push to a per-state `pendingEvents` queue that `ProgressionEventBridgeSystem` drains each simulation step and emits on the shared event bus, which keeps callers iterating entities reentrancy-safe. Snapshot helpers support save/load.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-progression/
├── context.md       # This file
├── plugin.ts        # ProgressionPlugin (recipe + parser + default xp-curve)
├── components.ts    # ProgressionComponent + all public functions
├── systems.ts       # ProgressionEventBridgeSystem (drain + emit)
└── index.ts         # Public re-exports
```

## Scope

- **In-scope**: XP grant, level-up loop, skill point spending, skill ranks, stat modifier aggregation, per-entity xp-curve config, save/load snapshots, declarative `<Progression>` recipe.
- **Out-of-scope**: Applying stat modifiers to health/combat (consumers call `getStatModifiers`), XP source triggers (combat/quests call `addXp`), skill def authoring (lives in rpg-core registry).

## Entry Points

- **plugin.ts**: `ProgressionPlugin` definition (systems, recipe, parser, initialize registers the default xp-curve).
- **components.ts**: `ProgressionComponent` plus the mutation/query API and snapshots.
- **index.ts**: Public re-exports (also re-exported from the `vibegame` barrel).

## Dependencies

- **Internal**: rpg-core (`getDataRegistry`, `getEventBus`, `onEvent`, `emitEvent`, event constants `PROGRESSION_XP_GAINED` / `PROGRESSION_LEVEL_UP` / `PROGRESSION_SKILL_PURCHASED`, types `SkillDef` / `StatModifier`), core ECS (`State`, `MAX_ENTITIES`).
- **External**: none beyond the engine core.

## Public API

- `addXp(state, eid, amount)`, `levelUp(state, eid)`, `getXpToNextLevel(state, eid)`.
- `spendSkillPoint(state, eid, skillId): boolean`, `getSkillRank(state, eid, skillId)`.
- `getStatModifiers(state, eid): StatModifier[]`.
- `getProgressionConfig` / `setProgressionConfig` (xpCurve id, skillPointsPerLevel).
- `getProgressionEntitySnapshot` / `applyProgressionEntitySnapshot` (save/load).
- `drainPendingEvents` (consumed by the bridge system).

<!-- LLM:REFERENCE -->

### Component

#### ProgressionComponent

- xp: f64 (cumulative XP toward next level; reduced on level-up)
- level: ui16 (starts at 1)
- unspentPoints: ui16 (skill points available to spend)
- spent: ui16 (points spent on skills, lifetime)

Per-entity config (xpCurve id, skillPointsPerLevel) and skill ranks are held in side tables keyed by `WeakMap<State, ...>`, not in ECS arrays. Component defaults: `xp=0, level=1, unspentPoints=0, spent=0`.

### System

#### ProgressionEventBridgeSystem

- Group: `simulation`.
- Drains `pendingEvents` queued by `addXp` / `levelUp` / `spendSkillPoint` and emits each on the rpg-core event bus. This decouples mutation from dispatch so callers iterating entities stay reentrancy-safe.

### Recipe

- **Progression**: components `['progression']`, override `'progression.level': 1`, parserAttributes `xp-curve` and `skill-points-per-level`. The parser resolves `xp-curve` (string id, falls back to the entity's current config) and `skill-points-per-level` (int, default `DEFAULT_SKILL_POINTS_PER_LEVEL = 3`) into per-entity config via `setProgressionConfig`.

### Systems (ordem no plugin)

1. **ProgressionEventBridgeSystem** (`simulation`): único sistema. Faz flush dos eventos pendentes para o event bus em cada step de simulação.

### Hook de level-up e modificadores

- `addXp` percorre `levelUp` em loop enquanto `xp >= curve(level)`. Cada `levelUp` subtrai o custo da curva, incrementa `level`, adiciona `skillPointsPerLevel` a `unspentPoints` e enfileira `PROGRESSION_LEVEL_UP`.
- `spendSkillPoint` valida `unspentPoints`, existência do `SkillDef`, `rank < maxRank` e custo (`cost` numérico ou array indexado por rank). Deduz pontos, incrementa o rank e aplica o efeito. Efeitos `stat-modifier` são lidos por `getStatModifiers` (modo `stack` multiplica magnitude pelo rank); efeitos `event-trigger` registram um `onEvent` que emite o evento `triggers` com `{ eid, skillId, rank }`.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Declarative entity with a custom XP curve and 5 skill points per level:

```xml
<Progression xp-curve="steep" skill-points-per-level="5"></Progression>
```

Granting XP and reading modifiers from code:

```ts
import { addXp, getXpToNextLevel, getStatModifiers } from 'vibegame';

addXp(state, heroEid, 50);
console.log('XP to next:', getXpToNextLevel(state, heroEid));
const mods = getStatModifiers(state, heroEid); // [{ stat, magnitude, stackMode, duration? }]
```

Buying a skill rank and reacting to level-up:

```ts
import { spendSkillPoint } from 'vibegame';
import { onEvent, PROGRESSION_LEVEL_UP } from 'vibegame';

spendSkillPoint(state, heroEid, 'toughness'); // false if no points, max rank, or missing def
onEvent(state, PROGRESSION_LEVEL_UP, ({ eid, level }) => { /* ... */ });
```

<!-- /LLM:EXAMPLES -->

## Known Limitations

- `spendSkillPoint` returns `false` silently for missing skill defs, insufficient points, or max rank; callers must validate intent.
- XP curves must be registered in the data registry before `addXp`; an unknown id falls back to `default`, then to `(lvl) => 5 + lvl`.
- Stat modifiers are aggregated, not applied: a combat/stats plugin must consume `getStatModifiers` and resolve `replace` / `stack` / `max` conflicts.
- Queued events are delivered on the next simulation step (bridge flush), not synchronously with the mutation.
