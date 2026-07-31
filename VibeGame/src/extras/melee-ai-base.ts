import type { Recipe, State } from '../core';
import {
  getOrCreateAiInstanceState,
  removeAiInstanceState,
} from '../plugins/rpg-ai/components';
import type { MeleeAiConfig } from '../plugins/rpg-ai/components';
import { runMeleeAiFrame } from '../plugins/rpg-ai/behaviour';
import { MonoBehaviour } from './mono-behaviour';

export { MonoBehaviour, toMonoBehaviourModule } from './mono-behaviour';

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

export const meleeAiScriptRecipe: Recipe = {
  name: 'MeleeAiScript',
  components: ['transform', 'monoBehaviour'],
};
