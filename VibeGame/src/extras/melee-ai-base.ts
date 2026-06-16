import type { Recipe, State } from '../core';
import type { MonoBehaviourModule } from '../plugins/entity-script';
import {
  getOrCreateAiInstanceState,
  removeAiInstanceState,
} from '../plugins/rpg-ai/components';
import type { MeleeAiConfig } from '../plugins/rpg-ai/components';
import { runMeleeAiFrame } from '../plugins/rpg-ai/behaviour';

/**
 * Author-facing MonoBehaviour base for melee creature AI. Lifecycle signature is
 * `(state, eid)` (not the engine's `MonoBehaviourContext`) so behaviours are
 * unit-testable with a lightweight state stub. Use {@link toMonoBehaviourModule}
 * to adapt an instance into the {@link MonoBehaviourModule} shape the
 * entity-script system loads via `script="…"` attributes.
 */
export class MonoBehaviour {
  start?(state: State, eid: number): void;
  update(state: State, eid: number): void {
    void state;
    void eid;
  }
  onDestroy?(state: State, eid: number): void;
}

/**
 * Config-driven melee AI FSM (idle→detect→chase→attack→lunge→dead). Movement
 * uses the engine NavMesh when available, falling back to direct Transform
 * steering. Damage is applied via the engine `damageHealth` helper.
 */
export class MeleeAiBehaviour extends MonoBehaviour {
  protected readonly meleeAiConfig: MeleeAiConfig;

  constructor(config: MeleeAiConfig) {
    super();
    this.meleeAiConfig = config;
  }

  update(state: State, eid: number): void {
    const inst = getOrCreateAiInstanceState(state, eid);
    runMeleeAiFrame(state, eid, this.meleeAiConfig, inst);
  }

  onDestroy(state: State, eid: number): void {
    removeAiInstanceState(state, eid);
  }
}

/**
 * Build a parameterless MonoBehaviour subclass with the given melee AI config
 * baked in. `const GoblinAi = createMeleeAi(cfg); const ai = new GoblinAi();`
 */
export function createMeleeAi(config: MeleeAiConfig): typeof MonoBehaviour {
  return class ConfiguredMeleeAi extends MeleeAiBehaviour {
    constructor() {
      super(config);
    }
  };
}

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

export const meleeAiScriptRecipe: Recipe = {
  name: 'MeleeAiScript',
  components: ['transform', 'monoBehaviour'],
};
