import type { Plugin, State } from '../../core';
import { RaycastResult, RaycastSource } from './components';
import { raycastSourceRecipe } from './recipes';
import { RaycastResetSystem, RaycastSystem } from './systems';

function directionAdapter(entity: number, value: string, state: State): void {
  const parts = value.trim().split(/\s+/).map(Number);
  const x = parts[0] ?? 0;
  const y = parts[1] ?? 0;
  const z = parts[2] ?? -1;
  const len = Math.hypot(x, y, z) || 1;
  RaycastSource.dirX[entity] = x / len;
  RaycastSource.dirY[entity] = y / len;
  RaycastSource.dirZ[entity] = z / len;
  void state;
}

export const RaycastPlugin: Plugin = {
  systems: [RaycastResetSystem, RaycastSystem],
  recipes: [raycastSourceRecipe],
  components: {
    raycastSource: RaycastSource,
    raycastResult: RaycastResult,
  },
  config: {
    defaults: {
      raycastSource: {
        dirX: 0,
        dirY: 0,
        dirZ: -1,
        maxDist: 100,
        layerMask: 0xffff,
        mode: 0,
      },
      raycastResult: {
        hitValid: 0,
        hitEntity: 0,
        hitDist: 0,
        hitNormalX: 0,
        hitNormalY: 1,
        hitNormalZ: 0,
        hitPointX: 0,
        hitPointY: 0,
        hitPointZ: 0,
      },
    },
    adapters: {
      'raycast-source': {
        direction: directionAdapter,
      },
    },
  },
};
