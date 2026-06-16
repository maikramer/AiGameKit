import { defineQuery } from '../core';
import type { Recipe, State } from '../core';
import { isKeyDown } from '../plugins/input';
import type { MonoBehaviourModule } from '../plugins/entity-script';
import { PlayerController } from '../plugins/player';
import { Transform } from '../plugins/transforms';

/**
 * Author-facing MonoBehaviour base for the Pickup / Interactable extras.
 *
 * The lifecycle signature is `(state, eid)` (NOT the engine's
 * `MonoBehaviourContext`) so behaviours are trivially unit-testable with a
 * lightweight state stub. Use {@link toMonoBehaviourModule} to adapt an
 * instance into the {@link MonoBehaviourModule} shape the entity-script system
 * loads via `import.meta.glob` / `script="…"` attributes.
 */
export class MonoBehaviour {
  start?(state: State, eid: number): void;
  // Default no-op so `typeof MonoBehaviour` is an instantiable constructor.
  update(state: State, eid: number): void {
    void state;
    void eid;
  }
  onDestroy?(state: State, eid: number): void;
}

/** How a player "approves" collecting a pickup. */
export type PickupTrigger = 'proximity' | 'input';

export interface PickupConfig {
  /** XZ distance within which the player may collect the pickup. */
  pickupRange: number;
  /** Yaw spin speed (rad/sec) applied to Transform.eulerY. `0` disables spin. */
  visualSpin?: number;
  /** XZ distance for a subtle scale-pulse visual cue. `0` disables glow. */
  glowRange?: number;
  /**
   * Approval strategy:
   * - `'proximity'` (default): collected the moment the player enters pickupRange.
   * - `'input'`: collected only while in pickupRange AND {@link pickupKey} is held.
   */
  trigger?: PickupTrigger;
  /** Keyboard code (e.g. `'KeyE'`) required when `trigger === 'input'`. */
  pickupKey?: string;
  /** Explicit player entity. When omitted, the first PlayerController entity is used. */
  playerEid?: number;
  /** Key-state reader; defaults to the engine {@link isKeyDown}. Handy for tests. */
  isKeyDown?: (code: string) => boolean;
  /**
   * Called when the pickup is collected.
   * @returns `true` to destroy the pickup entity, `false` to keep it alive.
   */
  onPickup: (state: State, pickerEid: number) => boolean;
}

export interface InteractableConfig {
  /** XZ distance within which the activator may trigger the interactable. */
  range: number;
  /** Keyboard code that activates while in range. Defaults to `'KeyF'`. */
  promptKey?: string;
  /** Explicit player entity. When omitted, the first PlayerController entity is used. */
  playerEid?: number;
  /** Key-state reader; defaults to the engine {@link isKeyDown}. Handy for tests. */
  isKeyDown?: (code: string) => boolean;
  /** Fired once per key press while the activator is within range. */
  onActivate: (state: State, activatorEid: number) => void;
}

const playerQuery = defineQuery([PlayerController]);

function playerExists(state: State, eid: number): boolean {
  if (eid <= 0) return false;
  return typeof state.exists === 'function' ? state.exists(eid) : true;
}

/** Pickup base behaviour: spin + optional glow + range/collect + destroy-on-success. */
export class PickupBehaviour extends MonoBehaviour {
  protected readonly pickupConfig: PickupConfig;
  private cachedPlayerEid = 0;
  private consumed = false;

  constructor(config: PickupConfig) {
    super();
    this.pickupConfig = config;
  }

  start(state: State, eid: number): void {
    void eid;
    this.resolvePlayer(state);
  }

  update(state: State, eid: number): void {
    if (this.consumed) return;

    const cfg = this.pickupConfig;
    const dt = state.time.deltaTime;
    const spin = cfg.visualSpin ?? 0;
    if (spin) Transform.eulerY[eid] += spin * dt;

    const playerEid = this.resolvePlayer(state);
    if (!playerEid) return;

    const dx = Transform.posX[playerEid] - Transform.posX[eid];
    const dz = Transform.posZ[playerEid] - Transform.posZ[eid];
    const distSq = dx * dx + dz * dz;

    const glowRange = cfg.glowRange ?? 0;
    if (glowRange > 0) this.applyGlow(eid, distSq, glowRange);

    const range = cfg.pickupRange;
    if (distSq > range * range) return;

    if ((cfg.trigger ?? 'proximity') === 'input') {
      const key = cfg.pickupKey ?? '';
      const kd = cfg.isKeyDown ?? isKeyDown;
      if (!key || !kd(key)) return;
    }

    const destroy = cfg.onPickup(state, playerEid);
    if (destroy) {
      this.onDestroy?.(state, eid);
      state.destroyEntity(eid);
      this.consumed = true;
    }
  }

  private applyGlow(eid: number, distSq: number, glowRange: number): void {
    if (distSq <= glowRange * glowRange) {
      const pulse = 1.08 + 0.04 * Math.sin(performance.now() * 0.005);
      Transform.scaleX[eid] = pulse;
      Transform.scaleY[eid] = pulse;
      Transform.scaleZ[eid] = pulse;
    } else {
      Transform.scaleX[eid] = 1;
      Transform.scaleY[eid] = 1;
      Transform.scaleZ[eid] = 1;
    }
  }

  private resolvePlayer(state: State): number {
    const explicit = this.pickupConfig.playerEid;
    if (explicit !== undefined && explicit > 0) {
      this.cachedPlayerEid = explicit;
      return explicit;
    }
    if (this.cachedPlayerEid && playerExists(state, this.cachedPlayerEid)) {
      return this.cachedPlayerEid;
    }
    const players = playerQuery(state.world);
    this.cachedPlayerEid = players[0] ?? 0;
    return this.cachedPlayerEid;
  }
}

/** Interactable base behaviour: in-range gate + activate on a key press (edge-triggered). */
export class InteractableBehaviour extends MonoBehaviour {
  protected readonly interactableConfig: InteractableConfig;
  private cachedPlayerEid = 0;
  private prevKeyDown = false;

  constructor(config: InteractableConfig) {
    super();
    this.interactableConfig = config;
  }

  update(state: State, eid: number): void {
    const cfg = this.interactableConfig;
    const playerEid = this.resolvePlayer(state);
    const range = cfg.range;

    let inRange = false;
    if (playerEid) {
      const dx = Transform.posX[playerEid] - Transform.posX[eid];
      const dz = Transform.posZ[playerEid] - Transform.posZ[eid];
      inRange = dx * dx + dz * dz <= range * range;
    }
    if (!inRange) {
      this.prevKeyDown = false;
      return;
    }

    const key = cfg.promptKey ?? 'KeyF';
    const kd = cfg.isKeyDown ?? isKeyDown;
    const down = kd(key);
    if (down && !this.prevKeyDown) {
      cfg.onActivate(state, playerEid);
    }
    this.prevKeyDown = down;
  }

  private resolvePlayer(state: State): number {
    const explicit = this.interactableConfig.playerEid;
    if (explicit !== undefined && explicit > 0) {
      this.cachedPlayerEid = explicit;
      return explicit;
    }
    if (this.cachedPlayerEid && playerExists(state, this.cachedPlayerEid)) {
      return this.cachedPlayerEid;
    }
    const players = playerQuery(state.world);
    this.cachedPlayerEid = players[0] ?? 0;
    return this.cachedPlayerEid;
  }
}

/**
 * Build a parameterless MonoBehaviour subclass with the given pickup config baked in.
 * `const Heal = createPickup(cfg); const inst = new Heal(); inst.update(state, eid);`
 */
export function createPickup(config: PickupConfig): typeof MonoBehaviour {
  return class ConfiguredPickup extends PickupBehaviour {
    constructor() {
      super(config);
    }
  };
}

/**
 * Build a parameterless MonoBehaviour subclass with the given interactable config baked in.
 */
export function createInteractable(
  config: InteractableConfig
): typeof MonoBehaviour {
  return class ConfiguredInteractable extends InteractableBehaviour {
    constructor() {
      super(config);
    }
  };
}

/**
 * Adapt a MonoBehaviour instance (state, eid signature) into the
 * {@link MonoBehaviourModule} shape (MonoBehaviourContext signature) consumed by
 * the entity-script system. Lets a game author wire an extras behaviour into a
 * `script="…"` module:
 *
 * ```ts
 * import { createPickup, toMonoBehaviourModule } from 'vibegame';
 * const inst = new (createPickup({ pickupRange: 2, onPickup: healOnPickup }))();
 * export const { start, update, onDestroy } = toMonoBehaviourModule(inst);
 * ```
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

/**
 * Scaffold recipe for `<Pickup range="…" spin="…" trigger="…"/>`. Creates a
 * transform + monoBehaviour entity; the caller wires the actual collect logic via
 * `script="…"` (a module built with {@link createPickup}). Register the recipe on a
 * plugin or via `state.registerRecipe(pickupRecipe)` so the XML parser recognises it.
 */
export const pickupRecipe: Recipe = {
  name: 'Pickup',
  components: ['transform', 'monoBehaviour'],
  parserAttributes: [
    'range',
    'spin',
    'glow-range',
    'trigger',
    'pickup-key',
    'player-eid',
  ],
};

/**
 * Scaffold recipe for `<Interactable range="…" prompt-key="…"/>`. Creates a
 * transform + monoBehaviour entity; the caller wires the activate logic via
 * `script="…"`. Register via `state.registerRecipe(interactableRecipe)`.
 */
export const interactableRecipe: Recipe = {
  name: 'Interactable',
  components: ['transform', 'monoBehaviour'],
  parserAttributes: ['range', 'prompt-key', 'player-eid'],
};
