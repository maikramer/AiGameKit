# Combat Plugin (context.md)

<!-- LLM:OVERVIEW -->

Health, factions, and projectile spawners for RPG-style combat. Tracks HP in a typed-array `Health` component, resolves whether two entities are hostile through a `faction-hostility` matrix stored in the `rpg-core` data registry, and spawns sensor-collider projectiles that apply damage on contact (via the physics `TouchedEvent`). Emits `combat:damaged`, `combat:healed`, and `combat:killed` events through `rpg-core/events`, plus a one-shot `combat:death` event the first frame an entity's HP hits zero. No mesh or renderer is attached to spawned projectiles, callers own the visual.

<!-- /LLM:OVERVIEW -->

## Layout

```
combat/
├── context.md      # This file
├── index.ts        # Public re-exports
├── plugin.ts       # CombatPlugin (recipes + systems + config)
├── components.ts   # Health, ProjectileData, ProjectileConfig, FactionComponent + helpers
├── projectile.ts   # spawnProjectile / spawnProjectileFromTemplate
└── systems.ts      # DamageResolutionSystem, ProjectileCleanupSystem, CombatDeathCleanupSystem
```

## Scope

- **In-scope**: HP state, faction tags + hostility lookup, projectile spawning (Transform + Rigidbody + sensor Collider), damage/heal helpers with EventBus emission, projectile lifetime cleanup, death event emission.
- **Out-of-scope**: Projectile rendering (attach a mesh separately), AI targeting (see `rpg-ai`), loot drops, status effects (see `rpg-status`).

## Entry Points

- **plugin.ts**: `CombatPlugin` definition.
- **projectile.ts**: `spawnProjectile`, `spawnProjectileFromTemplate`.
- **index.ts**: Public re-exports.

## Dependencies

- **Internal**: `rpg-core` (`events`, `registry`), `physics` (`Collider`, `Rigidbody`, `CollisionEvents`, `TouchedEvent`), `transforms` (`Transform`).
- **External**: None.

<!-- LLM:REFERENCE -->

### Component

#### Health (f32)

- `current`: current HP.
- `max`: maximum HP.

#### ProjectileData (f32 / i32)

- `damage` (f32): damage applied on contact.
- `ownerEid` (i32): entity that fired the projectile (skipped on hit).
- `lifetime` (f32): legacy lifetime in seconds.
- `age` (f32): seconds since spawn.

#### ProjectileConfig (f32 / ui8)

- `speed` (f32): spawn speed in world units/s.
- `maxLife` (f32): authoritative lifetime (preferred over `ProjectileData.lifetime` by `ProjectileCleanupSystem`).
- `damage` (f32): mirrored damage (used by templates).
- `faction` (ui8): faction tag id.

#### FactionComponent (ui8)

- `tag`: faction id. Built-in tags: `player`=0, `enemy`=1, `neutral`=2, `merchant`=3. New tags register dynamically up to 256.

### System

All systems run in the `simulation` group.

#### DamageResolutionSystem

- Group: `simulation`.
- Queries entities with `TouchedEvent` + `ProjectileData`.
- If the touched `other` is the owner, destroys the projectile and skips damage.
- Otherwise applies `ProjectileData.damage` to `other` via `damageHealth` (if it has `Health`) and destroys the projectile.

#### ProjectileCleanupSystem

- Group: `simulation`.
- Ages every `ProjectileData` entity by `state.time.deltaTime`.
- Destroys the entity once `age >= maxLife`, preferring `ProjectileConfig.maxLife` when the entity also has `ProjectileConfig`, else falling back to `ProjectileData.lifetime`.

#### CombatDeathCleanupSystem

- Group: `simulation`.
- For each `Health` entity at `current <= 0` that has not already emitted, emits a single `combat:death` event and sets the per-state death flag (so resurrection does not re-emit).

### Recipe

- **Faction**: components `['faction']`. Use with the `faction.tag` enum (`<Faction tag="enemy">`).
- **ProjectileTemplate**: parser-only recipe (no components). Attributes `id`, `speed`, `damage`, `max-life`, `sensor-radius`, `faction`. The parser registers a `ProjectileTemplate` under registry kind `projectile` (consumed by `spawnProjectileFromTemplate`).

### Helpers (components.ts)

`bindCombatState`, `damageHealth`, `healHealth`, `isAlive`, `isDead`, `setMaxHealth`, `setProjectileOwner`, `incrementProjectileAge`, `isProjectileExpired`, `getFaction`, `setFaction`, `isHostile`, `getDeathFlags`. `isHostile` reads a `FactionHostilityMatrix` (`pairs` of hostile tag names) from registry kind `faction-hostility`, id `default`, and returns `false` when no matrix is registered.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

```xml
<!-- Tag an entity as enemy -->
<Faction tag="enemy"></Faction>

<!-- Register a reusable projectile template -->
<ProjectileTemplate
  id="arrow"
  speed="28"
  damage="12"
  max-life="2"
  sensor-radius="0.25"
  faction="enemy"
></ProjectileTemplate>
```

```ts
import { spawnProjectile, spawnProjectileFromTemplate, damageHealth, setMaxHealth } from 'vibegame';

// Direct spawn: fire from caster toward a target entity
spawnProjectile(state, casterEid, targetEid, {
  speed: 28,
  maxLife: 2,
  damage: 12,
  faction: 1, // enemy
  sensorRadius: 0.25,
});

// Spawn from a registered template
spawnProjectileFromTemplate(state, casterEid, 'arrow', targetEid);

// Manual HP control (emits combat:healed / combat:damaged)
setMaxHealth(npcEid, 150);
damageHealth(npcEid, 20);
```

### Known Limitations

- Spawned projectiles carry no renderer. Attach a GLB or mesh in a separate step if the projectile must be visible.
- `damageHealth` / `healHealth` rely on a bound State (`bindCombatState` is called by `CombatPlugin.initialize`); calling them outside a running engine is a no-op for event emission.
- Faction tags beyond the four built-ins are appended at runtime and not persisted across engine restarts.

### See Also

- `rpg-ai` plugin (target acquisition, melee FSM that consumes `damageHealth` / `isHostile`).
- `rpg-core` (`events`, `registry`) for the EventBus contract and data registry.
- `examples/simple-rpg/src/main.ts` registers `CombatPlugin` via `withPlugin`.

<!-- /LLM:EXAMPLES -->
