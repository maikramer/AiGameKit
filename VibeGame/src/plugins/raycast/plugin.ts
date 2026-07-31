import { parseVec3Attr } from '../../core';
import type { Parser, Plugin, State } from '../../core';
import { RaycastHit, RaycastSource } from './components';
import { raycastSourceRecipe } from './recipes';
import { RaycastResetSystem, RaycastSystem } from './systems';

function directionAdapter(entity: number, value: string, state: State): void {
  const parts = value
    .trim()
    .split(/[\s,]+/)
    .map(Number)
    .filter((n) => Number.isFinite(n));
  const x = parts[0] ?? 0;
  const y = parts[1] ?? 0;
  const z = parts[2] ?? -1;
  const len = Math.hypot(x, y, z) || 1;
  RaycastSource.dirX[entity] = x / len;
  RaycastSource.dirY[entity] = y / len;
  RaycastSource.dirZ[entity] = z / len;
  void state;
}

/**
 * `<RaycastSource direction="x y z">` — the standalone attribute arrives
 * pre-converted by XMLValueParser as a {x,y,z} object. The string adapter
 * path would receive "[object Object]" and fall back to (0,0,-1); this
 * parser runs AFTER attribute application (createEntityFromRecipeInternal
 * order) and overwrites with the typed value. `createFromRecipe` (string
 * values only) is served by the adapter alone.
 */
const raycastSourceParser: Parser = ({ entity, element }) => {
  if (element.tagName.toLowerCase() !== 'raycastsource') return;
  const dir = element.attributes.direction;
  if (dir === undefined || dir === null) return;
  const [x, y, z] = parseVec3Attr(dir, [0, 0, -1]);
  const len = Math.hypot(x, y, z) || 1;
  RaycastSource.dirX[entity] = x / len;
  RaycastSource.dirY[entity] = y / len;
  RaycastSource.dirZ[entity] = z / len;
};

export const RaycastPlugin: Plugin = {
  systems: [RaycastResetSystem, RaycastSystem],
  recipes: [raycastSourceRecipe],
  components: {
    raycastSource: RaycastSource,
    raycastHit: RaycastHit,
  },
  config: {
    parsers: {
      RaycastSource: raycastSourceParser,
    },
    defaults: {
      raycastSource: {
        dirX: 0,
        dirY: 0,
        dirZ: -1,
        maxDist: 100,
        layerMask: 0xffff,
        mode: 0,
      },
      raycastHit: {
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
