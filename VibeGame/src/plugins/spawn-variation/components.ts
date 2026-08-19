import { defineComponent, F32 } from '../../core/ecs/component-storage';
import { defineQuery, Parent, type State } from '../../core';
import type { VariationSample } from './types';

/**
 * Per-instance visual variation written at spawn; consumed by the GLTF
 * InstancedMesh2 pool (`setColorAt` + brightness/contrast uniforms).
 */
export const SpawnVariation = defineComponent({
  colorR: F32,
  colorG: F32,
  colorB: F32,
  brightness: F32,
  contrast: F32,
});

const parentQuery = defineQuery([Parent]);

function writeOne(
  state: State,
  eid: number,
  sample: Pick<
    VariationSample,
    'colorR' | 'colorG' | 'colorB' | 'brightness' | 'contrast'
  >
): void {
  state.addComponent(eid, SpawnVariation);
  SpawnVariation.colorR[eid] = sample.colorR;
  SpawnVariation.colorG[eid] = sample.colorG;
  SpawnVariation.colorB[eid] = sample.colorB;
  SpawnVariation.brightness[eid] = sample.brightness;
  SpawnVariation.contrast[eid] = sample.contrast;
}

export function writeSpawnVariation(
  state: State,
  eid: number,
  sample: Pick<
    VariationSample,
    'colorR' | 'colorG' | 'colorB' | 'brightness' | 'contrast'
  >
): void {
  writeOne(state, eid, sample);
  // GLTFLoader children (instanced pool entity) need the same values — Parent
  // walk also works, but stamping avoids races if the child is pooled first.
  for (const child of parentQuery(state.world)) {
    if (Parent.entity[child] === eid) writeOne(state, child, sample);
  }
}
