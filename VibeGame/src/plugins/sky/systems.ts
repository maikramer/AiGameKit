import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getRenderingContext } from '../rendering';
import { EquirectSky, getEquirectSkyUrl } from './components';

const equirectSkyQuery = defineQuery([EquirectSky]);
/** Entities whose async load is in progress — avoids re-triggering each frame. */
const inFlight = new Set<number>();

const _loader = new THREE.TextureLoader();

/** Previous sky PMREM render target, disposed on the next sky swap (or plugin
 * dispose) to avoid leaking a GPU render target + texture per reload. */
let currentSkyRT: THREE.WebGLRenderTarget | null = null;

/**
 * Loads an equirectangular sky texture, applies it as scene background, and
 * PMREM-filters it into `scene.environment` so reflective/metallic PBR
 * materials actually mirror the sky's colors and sun position instead of the
 * generic neutral room set by {@link applyNeutralEnvironment}.
 */
async function applyEquirectSky(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  url: string,
  setBackground: boolean,
  envIntensity: number,
  bgIntensity: number
): Promise<void> {
  const texture = await _loader.loadAsync(url);
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.colorSpace = THREE.SRGBColorSpace;

  // Background: the sharp raw equirect, not the blurred PMREM version.
  if (setBackground) {
    const prev = scene.background;
    scene.background = texture;
    // 0 on the component means "use the loader default".
    scene.backgroundIntensity = bgIntensity > 0 ? bgIntensity : 1.2;
    if (prev && (prev as THREE.Texture).isTexture && prev !== texture) {
      (prev as THREE.Texture).dispose();
    }
  }

  // Reflections: a prefiltered (blurred, mip-chained) copy of the same sky so
  // glossy/metallic surfaces reflect real sky colors instead of a flat room.
  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const rt = pmrem.fromEquirectangular(texture);
  pmrem.dispose();
  if (!setBackground) texture.dispose();

  if (currentSkyRT && currentSkyRT !== rt) {
    currentSkyRT.dispose();
  }
  currentSkyRT = rt;

  if (scene.environment && scene.environment !== rt.texture) {
    scene.environment.dispose();
  }
  scene.environment = rt.texture;
  // Keep it subtle by default — the scene is already lit by the hemisphere +
  // directional lights; a full-strength IBL washes everything out. A positive
  // component value overrides per-scene (e.g. crank for stronger PBR reflections).
  scene.environmentIntensity = envIntensity > 0 ? envIntensity : 0.45;
}

/**
 * Loads the equirectangular sky once the renderer exists (texture upload needs
 * a live renderer). Applies it as `scene.background` (visual sky dome) and
 * PMREM-filters it into `scene.environment` for sky-accurate IBL reflections.
 */
export const EquirectSkyLoadSystem: System = defineSystem({
  name: 'EquirectSkyLoadSystem',
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
      applyEquirectSky(
        ctx.renderer,
        ctx.scene,
        url,
        EquirectSky.setBackground[eid] !== 0,
        EquirectSky.environmentIntensity[eid],
        EquirectSky.backgroundIntensity[eid]
      )
        .then(() => {
          EquirectSky.applied[eid] = 1;
        })
        .catch((err) => {
          logger.error(`[sky] Failed to load equirect sky "${url}"`, err);
          EquirectSky.applied[eid] = 1;
        })
        .finally(() => {
          inFlight.delete(eid);
        });
    }
  },
  dispose(state: State) {
    const ctx = getRenderingContext(state);
    if (ctx.scene) {
      const bg = ctx.scene.background;
      if (bg && (bg as THREE.Texture).isTexture) {
        (bg as THREE.Texture).dispose();
        ctx.scene.background = null;
      }
      if (currentSkyRT && ctx.scene.environment === currentSkyRT.texture) {
        ctx.scene.environment = null;
      }
    }
    if (currentSkyRT) {
      currentSkyRT.dispose();
      currentSkyRT = null;
    }
    inFlight.clear();
    for (const eid of equirectSkyQuery(state.world)) {
      EquirectSky.applied[eid] = 0;
    }
  },
});
