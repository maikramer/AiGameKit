import type { State } from '../core';
import type { MonoBehaviourModule } from '../plugins/entity-script';

/**
 * Author-facing MonoBehaviour base for the extras behaviours (interactable,
 * melee AI, turret AI). Lifecycle signature is `(state, eid)` — not the
 * engine's `MonoBehaviourContext` — so behaviours are unit-testable with a
 * lightweight state stub. Use {@link toMonoBehaviourModule} to adapt an
 * instance into the {@link MonoBehaviourModule} shape the entity-script
 * system loads via `script="…"` attributes.
 *
 * Shared home for the copies that used to live in each extras base.
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

/**
 * Adapt a {@link MonoBehaviour} instance into the {@link MonoBehaviourModule}
 * shape the entity-script system loads. `(state, eid)` methods are bound to
 * the instance and called with `ctx.state` / `ctx.entity`.
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
