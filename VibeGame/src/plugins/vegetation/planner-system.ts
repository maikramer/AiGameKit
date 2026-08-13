import { defineSystem, defineQuery, Parent, type System } from '../../core';
import { Transform } from '../transforms/components';
import { SpawnerPending } from '../spawner/components';
import { setSpawnGroupSpec } from '../spawner/context';
import { isGroundReadyForPlacement } from '../spawner/surface';
import { TerrainSpawnSystem } from '../spawner/systems';
import { LakeApplySystem, RiverApplySystem } from '../water/systems';
import { RoadApplySystem } from '../road/systems';
import { TerrainPadApplySystem } from '../terrain/pad-systems';
import { Vegetation } from './components';
import { generateVegetationHubs, setVegetationHubs } from './hubs';
import { getVegetationPatches } from './patch-context';
import { spawnSpecFromLayer } from './spec-from-plan';

const vegQuery = defineQuery([Vegetation]);

/**
 * Materialize smart vegetation layers: shared cluster hubs → child SpawnGroupSpecs
 * → SpawnerPending. Runs in setup before TerrainSpawnSystem.
 */
export const VegetationPlannerSystem: System = defineSystem({
  name: 'VegetationPlannerSystem',
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
    if (!isGroundReadyForPlacement(state)) {
      return;
    }

    const patches = getVegetationPatches(state);
    if (patches.size === 0) return;

    for (const eid of vegQuery(state.world)) {
      const runtime = patches.get(eid);
      if (!runtime || runtime.hubsReady) continue;

      if (!runtime.plan.smart) {
        runtime.hubsReady = true;
        continue;
      }

      const plan = runtime.plan;
      const hubs = generateVegetationHubs(state, {
        seed: plan.seed,
        clusterCount: plan.clusterCount,
        regionMinX: plan.regionMin[0],
        regionMaxX: plan.regionMax[0],
        regionMinZ: plan.regionMin[2],
        regionMaxZ: plan.regionMax[2],
        avoidWater: plan.avoidWater,
        avoidRoad: plan.avoidRoad,
      });
      setVegetationHubs(state, eid, hubs);

      if (runtime.layerEntities.length === 0) {
        for (const layer of plan.layers) {
          if (layer.meshes.length === 0) continue;
          if (plan.spawnCountMode === 'density' && layer.densityPerKm2 <= 0) {
            continue;
          }
          if (plan.spawnCountMode === 'fixed' && layer.count < 1) continue;

          const child = state.createEntity();
          state.addComponent(child, Transform);
          state.addComponent(child, Parent);
          Parent.entity[child] = eid;
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

          setSpawnGroupSpec(
            state,
            child,
            spawnSpecFromLayer(plan, layer, hubs)
          );
          state.addComponent(child, SpawnerPending);
          SpawnerPending.spawned[child] = 0;
          runtime.layerEntities.push(child);
        }
      } else {
        for (let i = 0; i < runtime.layerEntities.length; i++) {
          const child = runtime.layerEntities[i]!;
          const layer = plan.layers[i];
          if (!layer) continue;
          setSpawnGroupSpec(
            state,
            child,
            spawnSpecFromLayer(plan, layer, hubs)
          );
        }
      }

      runtime.hubsReady = true;
    }
  },
});
