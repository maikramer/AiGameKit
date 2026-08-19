import {
  defineSystem,
  defineQuery,
  Parent,
  type State,
  type System,
} from '../../core';
import { Transform } from '../transforms/components';
import { SpawnerPending } from '../spawner/components';
import { setSpawnGroupSpec } from '../spawner/context';
import { isGroundReadyForPlacement } from '../spawner/surface';
import { TerrainSpawnSystem } from '../spawner/systems';
import { LakeApplySystem, RiverApplySystem } from '../water/systems';
import { RoadApplySystem } from '../road/systems';
import { TerrainPadApplySystem } from '../terrain/pad-systems';
import { Nature } from './components';
import { getNaturePlans } from './context';
import { planNatureSpawns } from './planner';
import { speciesSpawnSpec } from './spec-from-rules';

const natureQuery = defineQuery([Nature]);

function createSpeciesChild(state: State, parent: number): number {
  const child = state.createEntity();
  state.addComponent(child, Transform);
  state.addComponent(child, Parent);
  Parent.entity[child] = parent;
  Transform.posX[child] = 0;
  Transform.posY[child] = 0;
  Transform.posZ[child] = 0;
  Transform.eulerX[child] = 0;
  Transform.eulerY[child] = 0;
  Transform.eulerZ[child] = 0;
  Transform.rotX[child] = 0;
  Transform.rotY[child] = 0;
  Transform.rotZ[child] = 0;
  Transform.rotW[child] = 1;
  Transform.scaleX[child] = 1;
  Transform.scaleY[child] = 1;
  Transform.scaleZ[child] = 1;
  Transform.dirty[child] = 1;
  return child;
}

/**
 * Materialize rule plans: candidate scatter + groves + near pass → one child
 * SpawnGroupSpec (explicit points) per species. Runs in setup after every
 * ground mutation has stamped and before TerrainSpawnSystem, mirroring the
 * VegetationPlannerSystem pattern.
 */
export const NaturePlannerSystem: System = defineSystem({
  name: 'NaturePlannerSystem',
  group: 'setup',
  after: [
    TerrainPadApplySystem,
    LakeApplySystem,
    RiverApplySystem,
    RoadApplySystem,
  ],
  before: [TerrainSpawnSystem],
  update(state) {
    if (state.headless) return;
    if (!isGroundReadyForPlacement(state)) return;

    const plans = getNaturePlans(state);
    if (plans.size === 0) return;

    for (const eid of natureQuery(state.world)) {
      const runtime = plans.get(eid);
      if (!runtime || runtime.planned) continue;

      const { buckets } = planNatureSpawns(state, runtime.plan);
      for (const species of runtime.plan.species) {
        const points = buckets.get(species.id);
        if (!points || points.length === 0) continue;
        const child = createSpeciesChild(state, eid);
        setSpawnGroupSpec(
          state,
          child,
          speciesSpawnSpec(runtime.plan, species, points)
        );
        state.addComponent(child, SpawnerPending);
        SpawnerPending.spawned[child] = 0;
      }

      runtime.planned = true;
      Nature.planned[eid] = 1;
    }
  },
});
