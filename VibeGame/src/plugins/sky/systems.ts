import * as THREE from 'three';
import { defineQuery, type State, type System } from '../../core';
import { getRenderingContext } from '../rendering';
import { EquirectSky, getEquirectSkyUrl } from './components';

const equirectSkyQuery = defineQuery([EquirectSky]);
/** Entities whose async load is in progress — avoids re-triggering each frame. */
const inFlight = new Set<number>();

const _loader = new THREE.TextureLoader();

/**
 * Loads an equirectangular sky texture and applies it as scene background
 * while preserving the PMREM environment for IBL/PBR lighting.
 */
async function applyEquirectSky(
  scene: THREE.Scene,
  url: string,
  setBackground: boolean
): Promise<void> {
  const texture = await _loader.loadAsync(url);
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.colorSpace = THREE.SRGBColorSpace;

  // Background only — keep the PMREM RoomEnvironment for IBL/PBR.
  if (setBackground) {
    const prev = scene.background;
    scene.background = texture;
    scene.backgroundIntensity = 1.2;
    if (prev && (prev as THREE.Texture).isTexture && prev !== texture) {
      (prev as THREE.Texture).dispose();
    }
  }

  // Strengthen the existing PMREM environment so PBR surfaces are well-lit.
  scene.environmentIntensity = 1.0;
}

/**
 * Loads the equirectangular sky once the renderer exists (texture upload needs
 * a live renderer). Applies it as `scene.background` (visual sky dome) while
 * preserving the PMREM `RoomEnvironment` for IBL.
 */
export const EquirectSkyLoadSystem: System = {
  group: 'simulation',
  update(state: State) {
    if (state.headless) return;

    const ctx = getRenderingContext(state);
    if (!ctx.renderer || !ctx.scene) return;

    for (const eid of equirectSkyQuery(state.world)) {
      if (EquirectSky.applied[eid] || inFlight.has(eid)) continue;

      const url = getEquirectSkyUrl(eid);
      if (!url) {
        EquirectSky.applied[eid] = 1;
        continue;
      }

      inFlight.add(eid);
      applyEquirectSky(ctx.scene, url, EquirectSky.setBackground[eid] !== 0)
        .then(() => {
          EquirectSky.applied[eid] = 1;
        })
        .catch((err) => {
          console.error(`[sky] Failed to load equirect sky "${url}"`, err);
          EquirectSky.applied[eid] = 1;
        })
        .finally(() => {
          inFlight.delete(eid);
        });
    }
  },
};
