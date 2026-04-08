import {
  BloomEffect as BloomEffectLib,
  ChromaticAberrationEffect,
  DepthOfFieldEffect,
  NoiseEffect,
  SMAAEffect,
  SMAAPreset,
  ToneMappingEffect,
  ToneMappingMode,
  VignetteEffect,
} from 'postprocessing';
import { Vector2 } from 'three';
import { defineQuery } from '../../core';
import { Player } from '../player';
import { MainCamera, threeCameras } from '../rendering';
import { WorldTransform } from '../transforms';
import {
  Bloom,
  ChromaticAberration,
  DepthOfField,
  Dithering,
  Noise,
  SMAA,
  Tonemapping,
  Vignette,
} from './components';
import { DitheringEffect } from './effects/dithering-effect';
import { registerEffect, type EffectDefinition } from './effect-registry';

const playerTransformQuery = defineQuery([Player, WorldTransform]);
const cameraTransformQuery = defineQuery([MainCamera, WorldTransform]);
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
        luminanceSmoothing: Bloom.luminanceSmoothing[entity],
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
      bloom.luminanceMaterial.uniforms.smoothing.value =
        Bloom.luminanceSmoothing[entity];
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
  {
    key: 'vignette',
    component: Vignette,
    create(_state, entity) {
      return new VignetteEffect({
        darkness: Vignette.darkness[entity],
        offset: Vignette.offset[entity],
      });
    },
    update(_state, entity, effect) {
      const vignette = effect as VignetteEffect;
      vignette.darkness = Vignette.darkness[entity];
      vignette.offset = Vignette.offset[entity];
    },
  },
  {
    key: 'depthOfField',
    component: DepthOfField,
    create(_state, entity) {
      return new DepthOfFieldEffect(threeCameras.get(entity), {
        focusDistance: DepthOfField.focusDistance[entity],
        focalLength: DepthOfField.focalLength[entity],
        bokehScale: DepthOfField.bokehScale[entity],
      });
    },
    update(state, entity, effect) {
      const dof = effect as DepthOfFieldEffect;
      if (DepthOfField.autoFocus[entity] === 1) {
        const playerEntities = playerTransformQuery(state.world);
        const cameraEntities = cameraTransformQuery(state.world);
        if (playerEntities.length > 0 && cameraEntities.length > 0) {
          const playerEid = playerEntities[0];
          const cameraEid = cameraEntities[0];
          const dx =
            WorldTransform.posX[cameraEid] - WorldTransform.posX[playerEid];
          const dy =
            WorldTransform.posY[cameraEid] - WorldTransform.posY[playerEid];
          const dz =
            WorldTransform.posZ[cameraEid] - WorldTransform.posZ[playerEid];
          dof.cocMaterial.focusDistance = Math.sqrt(
            dx * dx + dy * dy + dz * dz
          );
        }
      } else {
        dof.cocMaterial.focusDistance = DepthOfField.focusDistance[entity];
      }
      dof.cocMaterial.focalLength = DepthOfField.focalLength[entity];
      dof.bokehScale = DepthOfField.bokehScale[entity];
    },
  },
  {
    key: 'chromaticAberration',
    component: ChromaticAberration,
    create(_state, entity) {
      return new ChromaticAberrationEffect({
        offset: new Vector2(
          ChromaticAberration.offsetX[entity],
          ChromaticAberration.offsetY[entity]
        ),
        radialModulation: ChromaticAberration.radialModulation[entity] === 1,
        modulationOffset: ChromaticAberration.modulationOffset[entity],
      });
    },
    update(_state, entity, effect) {
      const ca = effect as ChromaticAberrationEffect;
      ca.offset.set(
        ChromaticAberration.offsetX[entity],
        ChromaticAberration.offsetY[entity]
      );
      ca.radialModulation = ChromaticAberration.radialModulation[entity] === 1;
      ca.modulationOffset = ChromaticAberration.modulationOffset[entity];
    },
  },
  {
    key: 'noise',
    component: Noise,
    create(_state, entity) {
      const noise = new NoiseEffect({
        premultiply: Noise.opacity[entity] < 1.0,
      });
      noise.blendMode.setOpacity(Noise.opacity[entity]);
      noise.blendMode.blendFunction = Noise.blendFunction[entity];
      return noise;
    },
    update(_state, entity, effect) {
      const noise = effect as NoiseEffect;
      noise.blendMode.setOpacity(Noise.opacity[entity]);
      noise.blendMode.blendFunction = Noise.blendFunction[entity];
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
