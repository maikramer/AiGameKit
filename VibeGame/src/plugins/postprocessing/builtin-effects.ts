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
    convolution: true,
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
      const intensity = Bloom.intensity[entity];
      const threshold = Bloom.luminanceThreshold[entity];
      const smoothing = Bloom.luminanceSmoothing[entity];
      if (bloom.intensity !== intensity) bloom.intensity = intensity;
      if (bloom.luminanceMaterial.uniforms.threshold.value !== threshold) {
        bloom.luminanceMaterial.uniforms.threshold.value = threshold;
      }
      if (bloom.luminanceMaterial.uniforms.smoothing.value !== smoothing) {
        bloom.luminanceMaterial.uniforms.smoothing.value = smoothing;
      }
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
      const colorBits = Dithering.colorBits[entity];
      const intensity = Dithering.intensity[entity];
      const grayscale = Dithering.grayscale[entity] === 1;
      const scale = Dithering.scale[entity];
      const noise = Dithering.noise[entity];
      if (dithering.colorBits !== colorBits) dithering.colorBits = colorBits;
      if (dithering.intensity !== intensity) dithering.intensity = intensity;
      if (dithering.grayscale !== grayscale) dithering.grayscale = grayscale;
      if (dithering.scale !== scale) dithering.scale = scale;
      if (dithering.noise !== noise) dithering.noise = noise;
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
      const mode = Tonemapping.mode[entity] as ToneMappingMode;
      const middleGrey = Tonemapping.middleGrey[entity];
      const whitePoint = Tonemapping.whitePoint[entity];
      const averageLuminance = Tonemapping.averageLuminance[entity];
      if (
        tonemapping.middleGrey !== middleGrey ||
        tonemapping.whitePoint !== whitePoint ||
        tonemapping.averageLuminance !== averageLuminance
      ) {
        return true;
      }
      if (tonemapping.mode !== mode) {
        tonemapping.mode = mode;
        return true;
      }
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
      const darkness = Vignette.darkness[entity];
      const offset = Vignette.offset[entity];
      if (vignette.darkness !== darkness) vignette.darkness = darkness;
      if (vignette.offset !== offset) vignette.offset = offset;
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
      const focalLength = DepthOfField.focalLength[entity];
      const bokehScale = DepthOfField.bokehScale[entity];
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
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
          if (dof.cocMaterial.focusDistance !== dist) {
            dof.cocMaterial.focusDistance = dist;
          }
        }
      } else {
        const focusDistance = DepthOfField.focusDistance[entity];
        if (dof.cocMaterial.focusDistance !== focusDistance) {
          dof.cocMaterial.focusDistance = focusDistance;
        }
      }
      if (dof.cocMaterial.focalLength !== focalLength) {
        dof.cocMaterial.focalLength = focalLength;
      }
      if (dof.bokehScale !== bokehScale) {
        dof.bokehScale = bokehScale;
      }
    },
  },
  {
    key: 'chromaticAberration',
    component: ChromaticAberration,
    convolution: true,
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
      const offsetX = ChromaticAberration.offsetX[entity];
      const offsetY = ChromaticAberration.offsetY[entity];
      const radialMod = ChromaticAberration.radialModulation[entity] === 1;
      const modOffset = ChromaticAberration.modulationOffset[entity];
      if (ca.offset.x !== offsetX || ca.offset.y !== offsetY) {
        ca.offset.set(offsetX, offsetY);
      }
      if (ca.radialModulation !== radialMod) {
        ca.radialModulation = radialMod;
      }
      if (ca.modulationOffset !== modOffset) {
        ca.modulationOffset = modOffset;
      }
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
      const opacity = Noise.opacity[entity];
      const blendFunction = Noise.blendFunction[entity];
      if (noise.blendMode.getOpacity() !== opacity) {
        noise.blendMode.setOpacity(opacity);
      }
      if (noise.blendMode.blendFunction !== blendFunction) {
        noise.blendMode.blendFunction = blendFunction;
      }
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
