import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { AiStateComponent } from './components';
import { runMeleeAiFrame } from './behaviour';
import { getMeleeAiConfig, getOrCreateAiInstanceState } from './components';

const aiQuery = defineQuery([AiStateComponent]);

export const RpgAiSystem: System = {
  group: 'simulation',
  update(state: State): void {
    for (const eid of aiQuery(state.world)) {
      const config = getMeleeAiConfig(state, eid);
      if (!config) continue;
      const inst = getOrCreateAiInstanceState(state, eid);
      runMeleeAiFrame(state, eid, config, inst);
    }
  },
};
