import {
  BloomEffect as BloomEffectLib,
  SMAAEffect,
  SMAAPreset,
  ToneMappingEffect,
  ToneMappingMode,
} from 'postprocessing';
import { Bloom, Dithering, SMAA, Tonemapping } from './components';
import { DitheringEffect } from './effects/dithering-effect';
import { registerEffect, type EffectDefinition } from './effect-registry';
const builtinDefinitions: EffectDefinition[] = [
  {
    key: 'smaa',
    component: SMAA,
    position: 'first',
    create(_state, entity) {
      const presetValue = SMAA.preset[entity];
      return new SMAAEffect({ preset: presetValue as SMAAPreset });
    },
  },
  {
    key: 'bloom',
    component: Bloom,
    create(_state, entity) {
      return new BloomEffectLib({
        intensity: Bloom.intensity[entity],
        luminanceThreshold: Bloom.luminanceThreshold[entity],
        mipmapBlur: Bloom.mipmapBlur[entity] === 1,
        radius: Bloom.radius[entity],
        levels: Bloom.levels[entity],
      });
    },
    update(_state, entity, effect) {
      const bloom = effect as BloomEffectLib;
      bloom.intensity = Bloom.intensity[entity];
      bloom.luminanceMaterial.uniforms.threshold.value =
        Bloom.luminanceThreshold[entity];
    },
  },
  {
    key: 'dithering',
    component: Dithering,
    create(_state, entity) {
      return new DitheringEffect({
        colorBits: Dithering.colorBits[entity],
        intensity: Dithering.intensity[entity],
        grayscale: Dithering.grayscale[entity] === 1,
      });
    },
    update(_state, entity, effect) {
      const dithering = effect as DitheringEffect;
      dithering.colorBits = Dithering.colorBits[entity];
      dithering.intensity = Dithering.intensity[entity];
      dithering.grayscale = Dithering.grayscale[entity] === 1;
      dithering.scale = Dithering.scale[entity];
      dithering.noise = Dithering.noise[entity];
    },
  },
  {
    key: 'tonemapping',
    component: Tonemapping,
    position: 'last',
    create(_state, entity) {
      return new ToneMappingEffect({
        mode: Tonemapping.mode[entity] as ToneMappingMode,
        middleGrey: Tonemapping.middleGrey[entity],
        whitePoint: Tonemapping.whitePoint[entity],
        averageLuminance: Tonemapping.averageLuminance[entity],
        adaptationRate: Tonemapping.adaptationRate[entity],
      });
    },
    update(_state, entity, effect) {
      const tonemapping = effect as ToneMappingEffect;
      if (
        tonemapping.middleGrey !== Tonemapping.middleGrey[entity] ||
        tonemapping.whitePoint !== Tonemapping.whitePoint[entity] ||
        tonemapping.averageLuminance !== Tonemapping.averageLuminance[entity]
      ) {
        return true;
      }
      tonemapping.mode = Tonemapping.mode[entity] as ToneMappingMode;
      return;
    },
  },
];

/**
 * Register all builtin postprocessing effects.
 * Called automatically by the plugin.
 */
export function registerBuiltinEffects(): void {
  for (const def of builtinDefinitions) {
    registerEffect(def);
  }
}
