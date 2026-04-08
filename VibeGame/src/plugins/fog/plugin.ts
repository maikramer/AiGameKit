import type { Plugin, State } from '../../core';
import { parseColor } from '../../core/validation/schemas';
import { Fog } from './components';
import { FogEffectSystem, FogSystem } from './systems';

function fogColorAdapter(
  entity: number,
  value: string | number,
  state: State
): void {
  const num = parseColor(value);
  const component = state.getComponent('fog');
  if (component) {
    (component as Record<string, Float32Array>).colorR[entity] =
      ((num >> 16) & 0xff) / 255;
    (component as Record<string, Float32Array>).colorG[entity] =
      ((num >> 8) & 0xff) / 255;
    (component as Record<string, Float32Array>).colorB[entity] =
      (num & 0xff) / 255;
  }
}

/** Volumetric fog plugin — exponential, exponential-squared, or linear fog with optional height falloff and volumetric scattering. */
export const FogPlugin: Plugin = {
  recipes: [{ name: 'fog', components: ['fog'] }],
  systems: [FogSystem, FogEffectSystem],
  components: {
    fog: Fog,
  },
  config: {
    adapters: {
      fog: {
        color: fogColorAdapter,
      },
    },
    defaults: {
      fog: {
        mode: 0,
        density: 0.015,
        near: 1,
        far: 1000,
        colorR: 0.533,
        colorG: 0.6,
        colorB: 0.667,
        heightFalloff: 1.0,
        baseHeight: 0,
        volumetricStrength: 0.5,
        quality: 1,
        noiseScale: 1.0,
      },
    },
    enums: {
      fog: {
        mode: { exponential: 0, 'exponential-squared': 1, linear: 2 },
        quality: { low: 0, medium: 1, high: 2 },
      },
    },
  },
};
