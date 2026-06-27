# RPG-AI Plugin (context.md)

<!-- LLM:OVERVIEW -->

Melee enemy AI built as a per-frame finite-state machine. The only queryable component is `AiStateComponent` (mode, target, cooldown, leash); the rich tuning (`MeleeAiConfig`) and the runtime scratch state (`AiInstanceState`) live in per-`State` side tables (WeakMap), not typed arrays. The system drives an idle/detect/chase/attack(lunge)/dead FSM that acquires the nearest hostile target, steers via the `navmesh` plugin when ready (falling back to direct `Transform` writes), and applies damage through `combat.damageHealth`. Presets extend the config with HP, assets, loot, and an optional boss roar intro.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-ai/
├── context.md      # This file
├── index.ts        # Public re-exports
├── plugin.ts       # RpgAiPlugin + meleeAiRecipe + MeleeAi parser
├── components.ts   # AiStateComponent, MeleeAiConfig, AiInstanceState, mode constants, side tables
├── behaviour.ts    # acquireTarget, runMeleeAiFrame (the FSM)
├── presets.ts      # MeleeAiPreset, BossAiPreset, BossAiBehaviour, createBossAi
└── systems.ts      # RpgAiSystem
```

## Scope

- **In-scope**: Melee FSM tick, target acquisition, navmesh-aware steering with direct-Transform fallback, lunge attack with windup/burst/recovery, leash to spawn origin, optional strafe / low-HP kite / enrage, preset storage, boss roar composition.
- **Out-of-scope**: The `MonoBehaviour` script wrappers `MeleeAiBehaviour` / `TurretAiBehaviour` (those live in `extras/melee-ai-base.ts` and `extras/turret-ai-base.ts`), projectile attacks (see `combat`), pathfinding internals (see `navmesh`).

## Entry Points

- **plugin.ts**: `RpgAiPlugin` definition, `meleeAiRecipe`.
- **behaviour.ts**: `runMeleeAiFrame`, `acquireTarget`.
- **presets.ts**: `loadMeleeAiPreset`, `presetToMeleeAiConfig`, `createBossAi`, `BossAiBehaviour`.
- **index.ts**: Public re-exports.

## Dependencies

- **Internal**: `combat` (`Health`, `FactionComponent`, `damageHealth`, `isHostile`), `transforms` (`Transform`), `navmesh` (`setAgentTarget`, `clearAgentTarget`, `isNavMeshReady`, `removeAgent`, `NavMeshAgent`), `rpg-core` (`registry`).
- **External**: `extras/melee-ai-base` (`MeleeAiBehaviour`, `MonoBehaviour`) imported by `presets.ts` for boss composition. Sibling `extras/turret-ai-base.ts` is a related ranged-AI script not consumed here.

<!-- LLM:REFERENCE -->

### Component

#### AiStateComponent

- `mode` (ui8): current FSM mode. Constants: `AI_MODE_IDLE`=0, `AI_MODE_DETECT`=1, `AI_MODE_CHASE`=2, `AI_MODE_ATTACK`=3, `AI_MODE_LUNGE`=4, `AI_MODE_DEAD`=5.
- `target` (ui32): currently targeted entity eid (0 when none).
- `cooldown` (f32): seconds remaining before the next lunge can begin.
- `leash` (f32): max pursuit radius from spawn origin (copied from config on first tick).

### Side tables (per-State WeakMaps, not queryable)

- `MeleeAiConfig`: full tuning (detectRange, attackRange, attackCooldown, attackDamage, chaseSpeed, wanderSpeed, wanderRadius, leashRadius, lungeWindup, lungeDuration, lungeRecovery, lungeStandoff, hoverMin, hoverMax, optional targetEid, strafe, lowHpKiteFrac, enrageBelowFrac, enrageSpeedMult, enrageCooldownMult). Access via `getMeleeAiConfig`, `setMeleeAiConfig`, `removeMeleeAiConfig`.
- `AiInstanceState`: runtime scratch (origin, lunge phase/timer/direction, detect/idle timers, hover/wander point, strafe direction/timer). Access via `getOrCreateAiInstanceState`, `removeAiInstanceState`, `createAiInstanceState`.

### System

#### RpgAiSystem

- Group: `simulation`.
- For each `AiStateComponent` entity with a registered `MeleeAiConfig`, fetches (or creates) the `AiInstanceState` and calls `runMeleeAiFrame(state, eid, config, inst)`. Entities without a config are skipped.

### FSM (behaviour.ts, `runMeleeAiFrame`)

On first tick the spawn origin is recorded, `leash` is copied, and a `NavMeshAgent` is attached (so `NavMeshAgentSystem` creates a crowd agent). Each frame: dead entities clear their agent and lock to `AI_MODE_DEAD`. Living entities acquire a target (`config.targetEid` if set and alive, else nearest hostile via `isHostile` and the `Health + FactionComponent` query). If no target or the target is beyond `leashRadius`, the FSM idles and wanders within `wanderRadius`. Within `detectRange` it transitions idle to detect then chase; within `attackRange` it runs the lunge sub-FSM (windup, burst, recovery). Optional behaviours: `strafe` orbits the target while closing, `lowHpKiteFrac` backs off and circles below an HP fraction, `enrageBelowFrac` raises chase speed (default 1.4x) and shortens cooldown (default 0.5x). Damage is applied by `damageHealth(targetEid, attackDamage)` at the end of the lunge burst when within `attackRange * 1.5`.

### Recipe

- **MeleeAi**: components `['aiState', 'health', 'faction', 'transform', 'gltfPending']`, parser attribute `preset`. The `MeleeAi` parser looks up a `MeleeAiConfig` in registry kind `melee-ai` under the preset name and binds it to the entity via `setMeleeAiConfig`. A missing preset logs a warning and leaves the entity without a config (the system then skips it).

### Presets (presets.ts)

- `MeleeAiPreset` extends `MeleeAiConfig` with `hp`, `assets` (`modelUrl`, `clips`), and `loot` (`goldMin`, `goldMax`). Stored under registry kind `melee-ai`.
- `BossAiPreset` adds a `roar` (`{ duration, sound? }`).
- `BossAiBehaviour` composes a `MeleeAiBehaviour` (from `extras/melee-ai-base`) and layers a one-time roar intro that freezes the boss for `roar.duration` seconds before delegating to melee. `createBossAi(meleeConfig, roarConfig)` returns a parameterless `MonoBehaviour` subclass.
- `presetToMeleeAiConfig` strips a preset down to the pure config consumed by the FSM.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

```xml
<!-- Declarative: bind a preset (must be registered beforehand) -->
<MeleeAi preset="goblin-brute"></MeleeAi>
```

```ts
import {
  setMeleeAiConfig,
  getOrCreateAiInstanceState,
  loadMeleeAiPreset,
  presetToMeleeAiConfig,
  createBossAi,
  AI_MODE_CHASE,
} from 'vibegame';

// Programmatic tuning
setMeleeAiConfig(state, enemyEid, {
  detectRange: 10,
  attackRange: 1.4,
  attackCooldown: 1.2,
  attackDamage: 8,
  chaseSpeed: 3.2,
  wanderSpeed: 1.0,
  wanderRadius: 4,
  leashRadius: 12,
  lungeWindup: 0.25,
  lungeDuration: 0.18,
  lungeRecovery: 0.4,
  lungeStandoff: 0.6,
  hoverMin: 1.5,
  hoverMax: 3.5,
  strafe: true,
  enrageBelowFrac: 0.3,
});

// Load a preset registered elsewhere and project to pure config
const preset = loadMeleeAiPreset(state, 'goblin-brute');
if (preset) setMeleeAiConfig(state, enemyEid, presetToMeleeAiConfig(preset));

// Boss as a MonoBehaviour script (used via the entity-script plugin)
const BossScript = createBossAi(meleeConfig, { duration: 1.5, sound: 'roar' });
```

### Known Limitations

- `MeleeAiConfig` and `AiInstanceState` are side tables keyed by `(State, eid)`, so they cannot be queried with `defineQuery` and are not serialized by the save-load plugin. Only `AiStateComponent` is queryable.
- The `MeleeAi` recipe hard-codes `gltfPending`, so it assumes a GLB-backed entity. Non-GLB enemies must be configured manually with `aiState` + `health` + `faction` + `transform` and a `setMeleeAiConfig` call.
- Steering depends on the `navmesh` plugin. Without a ready navmesh the FSM falls back to direct `Transform` writes with no obstacle avoidance, which can snag on geometry.
- The boss roar layer and the `MeleeAiBehaviour` / `TurretAiBehaviour` script wrappers live in `extras/melee-ai-base.ts` and `extras/turret-ai-base.ts`, not in this plugin. They are consumed through the `entity-script` plugin (`<MonoBehaviour>`), not by `RpgAiSystem`.

### See Also

- `combat` plugin (`damageHealth`, `isHostile`, `Health`, `FactionComponent`).
- `navmesh` plugin (`setAgentTarget`, `NavMeshAgent`, `isNavMeshReady`).
- `extras/melee-ai-base.ts` (`MeleeAiBehaviour`, `createMeleeAi`, `meleeAiScriptRecipe`) and `extras/turret-ai-base.ts` (`TurretAiBehaviour`, `createTurretAi`, `turretAiScriptRecipe`).
- `examples/simple-rpg/src/main.ts` registers `RpgAiPlugin`; creature wiring lives in `examples/simple-rpg/src/scripts/creature.ts`, `boss.ts`, and `bosses/witch.ts`.

<!-- /LLM:EXAMPLES -->
