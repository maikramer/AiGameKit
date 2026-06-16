import { defineQuery } from '../core';
import type { Recipe, State } from '../core';
import type { MonoBehaviourModule } from '../plugins/entity-script';
import { Transform } from '../plugins/transforms';
import {
  FactionComponent,
  Health,
  getFaction,
  isHostile,
  spawnProjectileFromTemplate,
} from '../plugins/combat';

/**
 * Author-facing MonoBehaviour base for turret / aggressor AI. Lifecycle is
 * `(state, eid)` (not `MonoBehaviourContext`) so behaviours are unit-testable
 * with a real State. Use {@link toMonoBehaviourModule} to bind an instance to
 * the entity-script system via `script="…"`.
 */
export class MonoBehaviour {
  start?(state: State, eid: number): void;
  update(state: State, eid: number): void {
    void state;
    void eid;
  }
  onDestroy?(state: State, eid: number): void;
}

export interface TurretAiConfig {
  /** XZ distance within which a hostile target is acquired. */
  range: number;
  /** Seconds between shots (elapsed-time based, never setTimeout). */
  cooldown: number;
  /** Cap of simultaneously live projectiles originated by this turret. */
  maxProjectiles: number;
  /** Registered projectile template id (see `spawnProjectileFromTemplate`). */
  projectileTemplate: string;
  /** World-space offset from the turret's origin applied to spawned shots. */
  spawnOffset: [number, number, number];
  /**
   * Faction name to attack (e.g. `'enemy'`). A candidate is a valid target
   * when it is hostile (via `isHostile`) OR its faction equals this name.
   */
  targetFaction: string;
}

interface TurretRuntime {
  /** Earliest `state.time.elapsed` at which the turret may fire again. */
  nextFireAt: number;
  /** Live projectile eids originated by this turret (maxProjectiles cap). */
  projectiles: Set<number>;
}

const runtimes = new Map<number, TurretRuntime>();
const healthQuery = defineQuery([Health]);

/**
 * Config-driven turret FSM: scan hostiles in range (nearest-first) → wait for
 * cooldown → spawn a projectile from the configured template. Firing delegates
 * to {@link spawnProjectileFromTemplate} (T15).
 */
export class TurretAiBehaviour extends MonoBehaviour {
  protected readonly turretAiConfig: TurretAiConfig;

  constructor(config: TurretAiConfig) {
    super();
    this.turretAiConfig = config;
  }

  start(state: State, eid: number): void {
    runtimes.set(eid, {
      nextFireAt: state.time.elapsed,
      projectiles: new Set(),
    });
  }

  update(state: State, eid: number): void {
    const cfg = this.turretAiConfig;
    const rt = runtimes.get(eid);
    if (!rt) return;

    pruneProjectiles(state, rt);

    const elapsed = state.time.elapsed;
    if (elapsed < rt.nextFireAt) return;
    if (rt.projectiles.size >= cfg.maxProjectiles) return;

    const target = acquireTarget(state, eid, cfg.range, cfg.targetFaction);
    if (target === 0) return;

    const projectileEid = spawnProjectileFromTemplate(
      state,
      eid,
      cfg.projectileTemplate,
      {
        eid: target,
      }
    );
    Transform.posX[projectileEid] += cfg.spawnOffset[0];
    Transform.posY[projectileEid] += cfg.spawnOffset[1];
    Transform.posZ[projectileEid] += cfg.spawnOffset[2];

    rt.projectiles.add(projectileEid);
    rt.nextFireAt = elapsed + cfg.cooldown;
  }

  onDestroy(state: State, eid: number): void {
    void state;
    runtimes.delete(eid);
  }
}

/**
 * Build a parameterless MonoBehaviour subclass with the given turret config
 * baked in. `const ArrowTurret = createTurretAi(cfg); const ai = new ArrowTurret();`
 */
export function createTurretAi(config: TurretAiConfig): typeof MonoBehaviour {
  return class ConfiguredTurretAi extends TurretAiBehaviour {
    constructor() {
      super(config);
    }
  };
}

/** Entity shell consumed via `script="…"`; config lives in the script module. */
export const turretAiScriptRecipe: Recipe = {
  name: 'TurretAi',
  components: ['transform', 'monoBehaviour'],
};

/**
 * Adapt a {@link TurretAiBehaviour} instance into the MonoBehaviourModule shape
 * the entity-script system loads. Mirrors the helper in sibling extras bases.
 */
export function toMonoBehaviourModule(
  instance: MonoBehaviour
): MonoBehaviourModule {
  const wrap = (fn: ((state: State, eid: number) => void) | undefined) =>
    fn
      ? (ctx: { state: State; entity: number }): void =>
          fn.call(instance, ctx.state, ctx.entity)
      : undefined;
  return {
    start: wrap(instance.start),
    update: wrap(instance.update),
    onDestroy: wrap(instance.onDestroy),
  } as MonoBehaviourModule;
}

function pruneProjectiles(state: State, rt: TurretRuntime): void {
  for (const peid of rt.projectiles) {
    if (!state.exists(peid)) rt.projectiles.delete(peid);
  }
}

function acquireTarget(
  state: State,
  selfEid: number,
  range: number,
  targetFaction: string
): number {
  const px = Transform.posX[selfEid];
  const py = Transform.posY[selfEid];
  const pz = Transform.posZ[selfEid];

  let nearest = 0;
  let nearestDist = range;

  for (const candidate of healthQuery(state.world)) {
    if (candidate === selfEid) continue;
    if (Health.current[candidate] <= 0) continue;
    if (!state.hasComponent(candidate, FactionComponent)) continue;
    if (!isTargetFaction(state, selfEid, candidate, targetFaction)) continue;

    const dx = Transform.posX[candidate] - px;
    const dy = Transform.posY[candidate] - py;
    const dz = Transform.posZ[candidate] - pz;
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
    if (dist <= nearestDist) {
      nearestDist = dist;
      nearest = candidate;
    }
  }

  return nearest;
}

function isTargetFaction(
  state: State,
  selfEid: number,
  candidateEid: number,
  targetFaction: string
): boolean {
  if (isHostile(state, selfEid, candidateEid)) return true;
  return (
    targetFaction !== '' && getFaction(state, candidateEid) === targetFaction
  );
}
