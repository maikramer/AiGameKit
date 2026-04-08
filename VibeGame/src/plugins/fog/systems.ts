import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getScene } from '../rendering/utils';
import { CameraSyncSystem } from '../rendering/systems';
import { Fog } from './components';
import {
  getPostprocessingContext,
  registerExternalEffect,
  triggerRebuild,
} from '../postprocessing';
import { VolumetricFogEffect } from './effects/volumetric-fog-effect';

const fogQuery = defineQuery([Fog]);

const FOG_STATE = new WeakMap<
  State,
  {
    trackedEid: number;
    mode: number;
    density: number;
    near: number;
    far: number;
    colorR: number;
    colorG: number;
    colorB: number;
  }
>();

export const FogSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;

    const scene = getScene(state);
    if (!scene) return;

    const entities = fogQuery(state.world);

    if (entities.length === 0) {
      const prev = FOG_STATE.get(state);
      if (prev) {
        scene.fog = null;
        FOG_STATE.delete(state);
      }
      return;
    }

    const eid = entities[0];
    if (entities.length > 1) {
      console.warn(
        `[fog] Multiple Fog entities found (ids: ${entities.join(', ')}). Using first (id: ${eid}).`
      );
    }

    const mode = Fog.mode[eid];
    const density = Fog.density[eid];
    const near = Fog.near[eid];
    const far = Fog.far[eid];
    const r = Fog.colorR[eid];
    const g = Fog.colorG[eid];
    const b = Fog.colorB[eid];

    const prev = FOG_STATE.get(state);
    if (
      prev &&
      prev.trackedEid === eid &&
      prev.mode === mode &&
      prev.density === density &&
      prev.near === near &&
      prev.far === far &&
      prev.colorR === r &&
      prev.colorG === g &&
      prev.colorB === b
    ) {
      return;
    }

    const color = new THREE.Color(r, g, b);

    if (mode === 2) {
      scene.fog = new THREE.Fog(color, near, far);
    } else {
      scene.fog = new THREE.FogExp2(color, density);
    }

    FOG_STATE.set(state, {
      trackedEid: eid,
      mode,
      density,
      near,
      far,
      colorR: r,
      colorG: g,
      colorB: b,
    });
  },
};

interface FogEffectState {
  effect: VolumetricFogEffect;
  trackedEid: number;
}

const FOG_EFFECT_STATE = new WeakMap<State, FogEffectState>();

function removeFogEffect(state: State): void {
  const prev = FOG_EFFECT_STATE.get(state);
  if (!prev) return;
  const postContext = getPostprocessingContext(state);
  const idx = postContext.externalEffects.indexOf(prev.effect);
  if (idx !== -1) postContext.externalEffects.splice(idx, 1);
  FOG_EFFECT_STATE.delete(state);
  triggerRebuild(state);
}

export const FogEffectSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;

    const postContext = getPostprocessingContext(state);
    if (postContext.composers.size === 0) return;

    const entities = fogQuery(state.world);

    if (entities.length === 0) {
      removeFogEffect(state);
      return;
    }

    const eid = entities[0];
    const quality = Fog.quality[eid];

    if (quality === 0) {
      removeFogEffect(state);
      return;
    }

    const prev = FOG_EFFECT_STATE.get(state);

    if (!prev || prev.trackedEid !== eid) {
      if (prev) {
        const idx = postContext.externalEffects.indexOf(prev.effect);
        if (idx !== -1) postContext.externalEffects.splice(idx, 1);
      }

      const effect = new VolumetricFogEffect({
        fogColor: [Fog.colorR[eid], Fog.colorG[eid], Fog.colorB[eid]],
        density: Fog.density[eid],
        heightFalloff: Fog.heightFalloff[eid],
        baseHeight: Fog.baseHeight[eid],
        volumetricStrength: Fog.volumetricStrength[eid],
        noiseScale: Fog.noiseScale[eid],
      });

      registerExternalEffect(state, effect);
      FOG_EFFECT_STATE.set(state, { effect, trackedEid: eid });
      triggerRebuild(state);
      return;
    }

    prev.effect.fogColor = [Fog.colorR[eid], Fog.colorG[eid], Fog.colorB[eid]];
    prev.effect.density = Fog.density[eid];
    prev.effect.heightFalloff = Fog.heightFalloff[eid];
    prev.effect.baseHeight = Fog.baseHeight[eid];
    prev.effect.volumetricStrength = Fog.volumetricStrength[eid];
    prev.effect.noiseScale = Fog.noiseScale[eid];
  },
};
