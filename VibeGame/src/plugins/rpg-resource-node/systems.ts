import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { emitEvent } from '../rpg-core/events';
import { ResourceNode } from './components';
import { kindToString, NODE_RESPAWNED } from './utils';
import type { NodeRespawnedPayload } from './utils';

const depletedQuery = defineQuery([ResourceNode]);

/**
 * Restores depleted resource nodes whose respawn timer has elapsed. Emits
 * {@link NODE_RESPAWNED} for each restored node. Runs in the `simulation`
 * group so it ticks every frame alongside gameplay logic.
 */
export const ResourceNodeRespawnSystem: System = {
  group: 'simulation',

  update(state: State) {
    const now = state.time.elapsed;
    for (const eid of depletedQuery(state.world)) {
      if (ResourceNode.depleted[eid] === 0) continue;
      const respawnAt = ResourceNode.respawnAt[eid];
      if (respawnAt <= 0 || now < respawnAt) continue;

      ResourceNode.depleted[eid] = 0;
      ResourceNode.respawnAt[eid] = 0;
      emitEvent(state, NODE_RESPAWNED, {
        target: eid,
        kind: kindToString(state, ResourceNode.kind[eid]),
      } satisfies NodeRespawnedPayload);
    }
  },
};
