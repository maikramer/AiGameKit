import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import { Sky } from 'three/examples/jsm/objects/Sky.js';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getRenderingContext } from '../rendering';
import { LightSyncSystem } from '../rendering/systems';
import { DirectionalLight } from '../rendering/components';
import { EquirectSky, ProceduralSky, getEquirectSkyUrl } from './components';

const equirectSkyQuery = defineQuery([EquirectSky]);
const proceduralSkyQuery = defineQuery([ProceduralSky]);
const directionalQuery = defineQuery([DirectionalLight]);
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

/** Default `scene.environmentIntensity` for the procedural sky IBL when the
 * component leaves it at 0. The sky is the scene's real light source, so the
 * fallback is brighter than the equirect one (0.45). */
const PROCEDURAL_ENV_INTENSITY = 0.65;

/** Sun color ramps warm-orange at the horizon to warm-white at noon. */
const SUN_HORIZON_COLOR = new THREE.Color(0xffa050);
const SUN_NOON_COLOR = new THREE.Color(0xfff4e2);

interface ProceduralSkyHandles {
  /** Visible sky dome in the main scene. */
  mesh: Sky;
  /** Sky captured for IBL. Own material with `showSunDisc = 0`: the disc is
   * ~10⁷ nits and would blow every material's diffuse irradiance through the
   * PMREM — the sun's direct contribution is the directional light's job. */
  envSky: Sky;
  envScene: THREE.Scene;
  envRT: THREE.WebGLRenderTarget | null;
  /** Last applied parameter set — regenerating the PMREM only on change. */
  signature: string;
}

const proceduralSkyByState = new WeakMap<State, ProceduralSkyHandles>();
const _sunDir = new THREE.Vector3();
const _sunColor = new THREE.Color();

function disposeProceduralSky(state: State): void {
  const handles = proceduralSkyByState.get(state);
  if (!handles) return;
  const ctx = getRenderingContext(state);
  if (ctx.scene) {
    ctx.scene.remove(handles.mesh);
    if (handles.envRT && ctx.scene.environment === handles.envRT.texture) {
      ctx.scene.environment = null;
    }
  }
  handles.mesh.geometry.dispose();
  handles.mesh.material.dispose();
  handles.envSky.geometry.dispose();
  (handles.envSky.material as THREE.ShaderMaterial).dispose();
  handles.envRT?.dispose();
  proceduralSkyByState.delete(state);
}

/** Write a scalar/vector uniform to both the visible and the IBL materials. */
function setSkyUniform(
  handles: ProceduralSkyHandles,
  name: string,
  value: number | THREE.Vector3
): void {
  for (const material of [
    handles.mesh.material as THREE.ShaderMaterial,
    handles.envSky.material as THREE.ShaderMaterial,
  ]) {
    const uniform = material.uniforms[name];
    if (typeof value === 'number') {
      uniform.value = value;
    } else {
      (uniform.value as THREE.Vector3).copy(value);
    }
  }
}

/**
 * Renders the procedural atmospheric sky (Preetham scattering, shader clouds,
 * visible sun disc) as a world dome and PMREM-filters it into
 * `scene.environment` for sky-accurate IBL. The sun position also drives the
 * first directional-light entity (direction, warm horizon color, optional
 * intensity override) so sky, shadows and god rays share one sun.
 */
export const ProceduralSkySystem: System = defineSystem({
  name: 'ProceduralSkySystem',
  group: 'draw',
  before: [LightSyncSystem],
  update(state: State) {
    if (state.headless) return;
    const ctx = getRenderingContext(state);
    if (!ctx.renderer || !ctx.scene) return;

    const entities = proceduralSkyQuery(state.world);
    if (entities.length === 0) {
      disposeProceduralSky(state);
      return;
    }
    const e = entities[0];

    let handles = proceduralSkyByState.get(state);
    if (!handles) {
      const mesh = new Sky();
      mesh.scale.setScalar(45000);
      mesh.frustumCulled = false;
      ctx.scene.add(mesh);
      // Own material (a clone): the IBL sky hides the sun disc.
      const envSky = new Sky();
      envSky.material = (mesh.material as THREE.ShaderMaterial).clone();
      envSky.material.uniforms.showSunDisc.value = 0;
      envSky.frustumCulled = false;
      const envScene = new THREE.Scene();
      envScene.add(envSky);
      handles = { mesh, envSky, envScene, envRT: null, signature: '' };
      proceduralSkyByState.set(state, handles);
    }

    const uniforms = handles.mesh.material.uniforms;
    const elevation = ProceduralSky.sunElevation[e];
    const azimuth = ProceduralSky.sunAzimuth[e];
    _sunDir.setFromSphericalCoords(
      1,
      THREE.MathUtils.degToRad(90 - elevation),
      THREE.MathUtils.degToRad(azimuth)
    );

    const turbidity = ProceduralSky.turbidity[e];
    const rayleigh = ProceduralSky.rayleigh[e];
    const mieCoefficient = ProceduralSky.mieCoefficient[e];
    const mieDirectionalG = ProceduralSky.mieDirectionalG[e];
    const cloudCoverage = ProceduralSky.cloudCoverage[e];
    const cloudDensity = ProceduralSky.cloudDensity[e];
    const cloudElevation = ProceduralSky.cloudElevation[e];
    const signature = `${turbidity}|${rayleigh}|${mieCoefficient}|${mieDirectionalG}|${elevation}|${azimuth}|${cloudCoverage}|${cloudDensity}|${cloudElevation}`;

    if (handles.signature !== signature) {
      handles.signature = signature;
      setSkyUniform(handles, 'turbidity', turbidity);
      setSkyUniform(handles, 'rayleigh', rayleigh);
      setSkyUniform(handles, 'mieCoefficient', mieCoefficient);
      setSkyUniform(handles, 'mieDirectionalG', mieDirectionalG);
      setSkyUniform(handles, 'sunPosition', _sunDir);
      setSkyUniform(handles, 'cloudCoverage', cloudCoverage);
      setSkyUniform(handles, 'cloudDensity', cloudDensity);
      setSkyUniform(handles, 'cloudElevation', cloudElevation);

      // The dome is the background — drop any equirect/clear-color background.
      ctx.scene.background = null;

      // Regenerate the IBL from the updated sky (one cube render, only on
      // parameter change — not per frame).
      const pmrem = new THREE.PMREMGenerator(ctx.renderer);
      const rt = pmrem.fromScene(handles.envScene);
      pmrem.dispose();
      handles.envRT?.dispose();
      handles.envRT = rt;
      ctx.scene.environment = rt.texture;
      const envIntensity = ProceduralSky.environmentIntensity[e];
      ctx.scene.environmentIntensity =
        envIntensity > 0 ? envIntensity : PROCEDURAL_ENV_INTENSITY;
    }

    // Cloud drift.
    uniforms.time.value = state.time.elapsed;

    if (ProceduralSky.driveLight[e] !== 1) return;

    // One sun for everything: rewrite the first directional entity's fields
    // before LightSyncSystem reads them (direction is scene → light, the same
    // convention as the sun vector).
    for (const light of directionalQuery(state.world)) {
      DirectionalLight.directionX[light] = _sunDir.x;
      DirectionalLight.directionY[light] = _sunDir.y;
      DirectionalLight.directionZ[light] = _sunDir.z;
      const warmth = THREE.MathUtils.smoothstep(elevation, 0, 30);
      _sunColor.lerpColors(SUN_HORIZON_COLOR, SUN_NOON_COLOR, warmth);
      DirectionalLight.color[light] = _sunColor.getHex();
      const sunIntensity = ProceduralSky.sunIntensity[e];
      if (sunIntensity > 0) {
        DirectionalLight.intensity[light] = sunIntensity;
      }
      break;
    }
  },
  dispose(state: State) {
    disposeProceduralSky(state);
  },
});
