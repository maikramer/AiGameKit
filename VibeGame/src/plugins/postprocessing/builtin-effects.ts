import {
  Mesh,
  MeshBasicMaterial,
  SphereGeometry,
  Vector2,
  Vector3,
  type Camera,
  type Object3D,
  type Scene,
  type WebGLRenderer,
} from 'three';
import {
  BloomEffect,
  BrightnessContrastEffect,
  ChromaticAberrationEffect,
  DepthOfFieldEffect,
  EffectPass,
  FXAAEffect,
  GodRaysEffect,
  HueSaturationEffect,
  KernelSize,
  NoiseEffect,
  SMAAEffect,
  SMAAPreset,
  ToneMappingEffect,
  ToneMappingMode,
  VignetteEffect,
  type Effect,
  type Pass,
} from 'postprocessing';
import { N8AOPostPass } from 'n8ao';
import { getGpuTierForRenderer } from '../rendering/utils';
import { Postprocessing } from './components';
import { registerEffect } from './effect-registry';
import { ReflectionPass } from './reflection-pass';
import { HeightFogEffect } from './height-fog-effect';
import { findFirstDirectionalLight } from './sun-source';

type CS = Record<string, Float32Array | Uint8Array | Uint32Array>;

/**
 * Maps the component's toneMapping enum (0=off,1=AgX,2=ACES,3=Neutral,
 * 4=Reinhard) to the library's ToneMappingMode. Index 0 is handled by the
 * create() returning null (no tone-mapping pass).
 */
const ToneMappingModes = [
  ToneMappingMode.AGX,
  ToneMappingMode.ACES_FILMIC,
  ToneMappingMode.NEUTRAL,
  ToneMappingMode.REINHARD2,
] as const;

/**
 * EffectPass keeps its `effects` array private, so to update an effect's
 * parameters each frame we hold the Effect reference alongside the pass that
 * wraps it. SSAO uses N8AOPostPass directly (no EffectPass wrapper) and is
 * resolved by casting in its own update().
 */
const effectByPass = new WeakMap<Pass, Effect>();

function wrap(camera: Camera, effect: Effect): Pass {
  const pass = new EffectPass(camera, effect);
  effectByPass.set(pass, effect);
  return pass;
}

function effectOf(pass: Pass): Effect | undefined {
  return effectByPass.get(pass);
}

/** Same idea as {@link effectByPass} but for passes wrapping more than one
 * effect (color grading combines hue/saturation + brightness/contrast in a
 * single EffectPass so they share one shader compile). */
const multiEffectByPass = new WeakMap<Pass, Effect[]>();

function wrapMulti(camera: Camera, effects: Effect[]): Pass {
  const pass = new EffectPass(camera, ...effects);
  multiEffectByPass.set(pass, effects);
  return pass;
}

/** Maps a `detect-gpu` tier (0-3, undefined = unresolved yet) to n8ao's sample-count preset. */
function qualityModeForTier(
  tier: { tier: number } | null
): import('n8ao').N8AOQualityMode {
  if (!tier) return 'Medium';
  if (tier.tier <= 0) return 'Performance';
  if (tier.tier === 1) return 'Low';
  if (tier.tier === 2) return 'Medium';
  return 'High';
}

const ssaoBaseIntensityByPass = new WeakMap<Pass, number>();

registerEffect({
  key: 'smaa',
  position: 'first',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 2) return null;
    return wrap(camera, new SMAAEffect({ preset: SMAAPreset.MEDIUM }));
  },
});

registerEffect({
  key: 'fxaa',
  position: 'first',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.aa as Uint8Array)[entity] !== 1) return null;
    return wrap(camera, new FXAAEffect());
  },
});

registerEffect({
  key: 'bloom',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.bloom as Uint8Array)[entity]) return null;
    const bloom = new BloomEffect({
      mipmapBlur: true,
      luminanceThreshold: (cs.bloomThreshold as Float32Array)[entity],
      intensity: (cs.bloomStrength as Float32Array)[entity],
      radius: (cs.bloomRadius as Float32Array)[entity],
    });
    return wrap(camera, bloom);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const bloom = effectOf(pass) as BloomEffect | undefined;
    if (!bloom) return;
    bloom.intensity = (state.bloomStrength as Float32Array)[entity];
    bloom.mipmapBlurPass.radius = (state.bloomRadius as Float32Array)[entity];
    bloom.luminanceMaterial.threshold = (state.bloomThreshold as Float32Array)[
      entity
    ];
  },
});

registerEffect({
  key: 'vignette',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.vignette as Uint8Array)[entity]) return null;
    const vignette = new VignetteEffect({
      offset: (cs.vignetteOffset as Float32Array)[entity],
      darkness: (cs.vignetteDarkness as Float32Array)[entity],
    });
    return wrap(camera, vignette);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const vignette = effectOf(pass) as VignetteEffect | undefined;
    if (!vignette) return;
    vignette.offset = (state.vignetteOffset as Float32Array)[entity];
    vignette.darkness = (state.vignetteDarkness as Float32Array)[entity];
  },
});

registerEffect({
  key: 'ssao',
  // AO darkens creases first so the height fog that follows blends atmosphere
  // over an already-occluded image (order -10 = first among the regular passes;
  // the height-fog effect at -5 then runs ahead of bloom and the rest).
  order: -10,
  create(
    _state: CS,
    entity: number,
    renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.ssao as Uint8Array)[entity]) return null;
    const size = renderer.getDrawingBufferSize(new Vector2());
    const n8ao = new N8AOPostPass(scene, camera, size.x, size.y);
    n8ao.configuration.aoRadius = Math.max(
      1e-6,
      (cs.ssaoRadius as Float32Array)[entity]
    );
    n8ao.configuration.intensity = Math.max(
      0,
      (cs.ssaoIntensity as Float32Array)[entity]
    );
    n8ao.setQualityMode(qualityModeForTier(getGpuTierForRenderer(renderer)));
    return n8ao as unknown as Pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const ssao = pass as unknown as N8AOPostPass;
    const radius = Math.max(1e-6, (state.ssaoRadius as Float32Array)[entity]);
    if (ssao.configuration.aoRadius !== radius) {
      ssao.configuration.aoRadius = radius;
    }
    const intensity = Math.max(
      0,
      (state.ssaoIntensity as Float32Array)[entity]
    );
    if (ssaoBaseIntensityByPass.get(pass) !== intensity) {
      ssaoBaseIntensityByPass.set(pass, intensity);
      ssao.configuration.intensity = intensity;
    }
  },
});

const dofBaseBokehByPass = new WeakMap<Pass, number>();

registerEffect({
  key: 'depthOfField',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.depthOfField as Uint8Array)[entity]) return null;
    const dof = new DepthOfFieldEffect(camera, {
      focusDistance: (cs.dofFocusDistance as Float32Array)[entity],
      focusRange: (cs.dofFocusRange as Float32Array)[entity],
      bokehScale: (cs.dofBokehScale as Float32Array)[entity],
    });
    return wrap(camera, dof);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const dof = effectOf(pass) as DepthOfFieldEffect | undefined;
    if (!dof) return;
    const focusDistance = (state.dofFocusDistance as Float32Array)[entity];
    const focusRange = (state.dofFocusRange as Float32Array)[entity];
    if (dof.cocMaterial.focusDistance !== focusDistance) {
      dof.cocMaterial.focusDistance = focusDistance;
    }
    if (dof.cocMaterial.focusRange !== focusRange) {
      dof.cocMaterial.focusRange = focusRange;
    }
    // bokehScale is a target-circle count; use the component's coarse scale
    // divided down so the default (~3) lands near the library default (1).
    const bokehScale = (state.dofBokehScale as Float32Array)[entity] / 3;
    if (dofBaseBokehByPass.get(pass) !== bokehScale) {
      dofBaseBokehByPass.set(pass, bokehScale);
      dof.bokehScale = bokehScale;
    }
  },
});

registerEffect({
  key: 'tonemapping',
  position: 'last',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    const idx = (cs.toneMapping as Uint8Array)[entity];
    if (idx === 0) return null;
    const mode = ToneMappingModes[Math.min(idx, ToneMappingModes.length) - 1];
    return wrap(camera, new ToneMappingEffect({ mode }));
  },
});

registerEffect({
  key: 'chromaticAberration',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.chromaticAberration as Uint8Array)[entity]) return null;
    const strength = (cs.caStrength as Float32Array)[entity];
    const ca = new ChromaticAberrationEffect({
      offset: new Vector2(strength, strength),
      radialModulation: true,
      modulationOffset: 0.15,
    });
    return wrap(camera, ca);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const ca = effectOf(pass) as ChromaticAberrationEffect | undefined;
    if (!ca) return;
    const strength = (state.caStrength as Float32Array)[entity];
    ca.offset.set(strength, strength);
  },
});

registerEffect({
  key: 'colorGrading',
  position: 'last',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.colorGrading as Uint8Array)[entity]) return null;
    const hueSaturation = new HueSaturationEffect({
      saturation: (cs.saturation as Float32Array)[entity],
    });
    const brightnessContrast = new BrightnessContrastEffect({
      brightness: (cs.brightness as Float32Array)[entity],
      contrast: (cs.contrast as Float32Array)[entity],
    });
    return wrapMulti(camera, [hueSaturation, brightnessContrast]);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const effects = multiEffectByPass.get(pass);
    if (!effects) return;
    const hueSaturation = effects[0] as HueSaturationEffect;
    const brightnessContrast = effects[1] as BrightnessContrastEffect;
    hueSaturation.saturation = (state.saturation as Float32Array)[entity];
    brightnessContrast.brightness = (state.brightness as Float32Array)[entity];
    brightnessContrast.contrast = (state.contrast as Float32Array)[entity];
  },
});

registerEffect({
  key: 'filmGrain',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    _scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.filmGrain as Uint8Array)[entity]) return null;
    // NoiseEffect has no `opacity` of its own — control the grain strength via
    // the base Effect's blendMode opacity (SCREEN blend keeps it from crushing
    // blacks). premultiply keeps colours from blowing out at higher opacity.
    const noise = new NoiseEffect({ premultiply: true });
    noise.blendMode.opacity.value = (cs.filmGrainOpacity as Float32Array)[
      entity
    ];
    return wrap(camera, noise);
  },
  update(state: CS, entity: number, pass: Pass): void {
    const noise = effectOf(pass) as NoiseEffect | undefined;
    if (!noise) return;
    noise.blendMode.opacity.value = (state.filmGrainOpacity as Float32Array)[
      entity
    ];
  },
});

/**
 * Sun source mesh shared by `GodRaysEffect`. A small emissive sphere that the
 * effect samples as the centre of the radial blur — it does NOT need to be
 * visible to the player (it's occluded by the god-ray pass), it just has to be
 * positioned where the directional light comes from so the rays converge there.
 */
let sharedSunMesh: Mesh<SphereGeometry, MeshBasicMaterial> | null = null;
const _sunPosition = new Vector3();
const _lightDir = new Vector3();

function getOrCreateSunMesh(scene: Scene): Mesh {
  if (!sharedSunMesh) {
    const geo = new SphereGeometry(8, 16, 16);
    // GodRaysEffect requires: light source must NOT write depth and must be
    // flagged transparent. colorWrite=false keeps the sun sphere from showing
    // up as a visible ball in the main render pass — only the god-ray blur
    // samples its screen position.
    const mat = new MeshBasicMaterial({
      color: 0xfff4e0,
      transparent: true,
      depthWrite: false,
      colorWrite: false,
    });
    sharedSunMesh = new Mesh(geo, mat);
    sharedSunMesh.frustumCulled = false;
    scene.add(sharedSunMesh);
  } else if (sharedSunMesh.parent !== scene) {
    scene.add(sharedSunMesh);
  }
  return sharedSunMesh;
}

/**
 * Position the sun source far from the player along the inverse of the first
 * directional light's direction (i.e. where the sun appears in the sky). The
 * `GodRaysEffect` projects this world position into screen space each frame to
 * drive the radial blur, so the mesh follows the active light automatically.
 */
/** Last sun sync pose — skip rewrite when camera/light barely moved. */
const _lastSunCam = new Vector3(Number.NaN, Number.NaN, Number.NaN);
const _lastSunDir = new Vector3(Number.NaN, Number.NaN, Number.NaN);
const SUN_SYNC_CAM_EPS_SQ = 0.01; // ~0.1 m
const SUN_SYNC_DIR_EPS_SQ = 1e-6;

function syncSunSource(scene: Scene, camera: Camera): void {
  if (!sharedSunMesh) return;
  const light = findFirstDirectionalLight(scene);
  if (!light) return;
  // Light direction (scene → light) is opposite to the direction light travels.
  // The sun source sits along the scene→light vector, far from the camera.
  _lightDir.copy(light.position).sub(light.target.position).normalize();
  const camMoved =
    camera.position.distanceToSquared(_lastSunCam) > SUN_SYNC_CAM_EPS_SQ;
  const dirMoved =
    _lightDir.distanceToSquared(_lastSunDir) > SUN_SYNC_DIR_EPS_SQ;
  if (!camMoved && !dirMoved) return;
  _lastSunCam.copy(camera.position);
  _lastSunDir.copy(_lightDir);
  _sunPosition.copy(camera.position).addScaledVector(_lightDir, 400);
  sharedSunMesh.position.copy(_sunPosition);
}

/** Per-pass handles for the god rays effect: the wrapped effect plus the
 * scene/camera refs needed to reposition the sun source each frame (the camera
 * moves, so the sun mesh has to track it). */
interface GodRaysHandles {
  scene: Scene;
  camera: Camera;
}
const godRaysHandlesByPass = new WeakMap<Pass, GodRaysHandles>();

registerEffect({
  key: 'godRays',
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.godRays as Uint8Array)[entity]) return null;
    const sun = getOrCreateSunMesh(scene);
    syncSunSource(scene, camera);
    const godRays = new GodRaysEffect(camera, sun, {
      kernelSize: KernelSize.MEDIUM,
      density: (cs.godRaysDensity as Float32Array)[entity],
      decay: (cs.godRaysDecay as Float32Array)[entity],
      weight: (cs.godRaysWeight as Float32Array)[entity],
      exposure: (cs.godRaysExposure as Float32Array)[entity],
      samples: 48,
      clampMax: 1.0,
    });
    const pass = wrap(camera, godRays);
    godRaysHandlesByPass.set(pass, { scene, camera });
    return pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const godRays = effectOf(pass) as GodRaysEffect | undefined;
    if (!godRays) return;
    // GodRaysEffect doesn't expose density/decay/weight/exposure setters
    // directly — they live on the internal GodRaysMaterial.
    const mat = godRays.godRaysMaterial;
    mat.density = (state.godRaysDensity as Float32Array)[entity];
    mat.decay = (state.godRaysDecay as Float32Array)[entity];
    mat.weight = (state.godRaysWeight as Float32Array)[entity];
    mat.exposure = (state.godRaysExposure as Float32Array)[entity];
    // Keep the sun source aligned with the active directional light + camera so
    // the radial blur converges on the visible sun disc as the player moves.
    const handles = godRaysHandlesByPass.get(pass);
    if (handles) syncSunSource(handles.scene, handles.camera);
  },
});

/**
 * SSR reflects ONLY on selected meshes (SSRPass renders them into its
 * metalness mask). Selection is automatic and physically motivated: a surface
 * reflects when its material is shiny — metal (`metalness >= 0.5 &&
 * roughness <= 0.4`) or a highly polished dielectric (`roughness <= 0.15`:
 * water, crystal, marble). `mesh.userData.ssrReflective = true/false`
 * force-includes/-excludes a mesh regardless of its material. Reflecting the
 * whole scene (selects = null) mirrors grass onto rough walls/terrain and
 * reads as a wet-world bug, so it is deliberately not offered.
 */
const SSR_AUTO_METALNESS_MIN = 0.5;
const SSR_AUTO_ROUGHNESS_MAX = 0.4;
const SSR_AUTO_GLOSS_ROUGHNESS_MAX = 0.15;
/** Scene rescan cadence for the selects list. Meshes stream in as chunks and
 * GLTFs load, so the list must refresh — but a full traverse per frame is
 * wasteful. ~0.5 s at 60 fps. */
const SSR_SELECTS_REFRESH_FRAMES = 30;

interface SsrHandles {
  scene: Scene;
  selects: Mesh[];
  framesUntilRefresh: number;
}
const ssrHandlesByPass = new WeakMap<Pass, SsrHandles>();

function isSsrReflective(mesh: Mesh): boolean {
  const flag = mesh.userData.ssrReflective as boolean | undefined;
  if (flag === false) return false;
  if (flag === true) return true;
  const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  for (const mat of mats) {
    const std = mat as { metalness?: number; roughness?: number };
    if (
      typeof std.metalness !== 'number' ||
      typeof std.roughness !== 'number'
    ) {
      continue;
    }
    const shinyMetal =
      std.metalness >= SSR_AUTO_METALNESS_MIN &&
      std.roughness <= SSR_AUTO_ROUGHNESS_MAX;
    const polishedDielectric = std.roughness <= SSR_AUTO_GLOSS_ROUGHNESS_MAX;
    if (shinyMetal || polishedDielectric) return true;
  }
  return false;
}

/** Reused traversal stack — the rescan runs twice a second and would otherwise
 *  hand the GC a fresh closure chain over the whole scene graph each time. */
const ssrScanStack: Object3D[] = [];

function refreshSsrSelects(scene: Scene, selects: Mesh[]): void {
  selects.length = 0;
  ssrScanStack.length = 0;
  ssrScanStack.push(scene);
  while (ssrScanStack.length > 0) {
    const node = ssrScanStack.pop() as Object3D;
    // A hidden subtree cannot reflect anything, and skipping it here prunes
    // whole chunk hierarchies instead of testing every mesh inside them —
    // `Object3D.traverse` has no way to say "not this branch".
    if (node !== scene && node.visible === false) continue;
    const mesh = node as Mesh;
    if (mesh.isMesh === true && isSsrReflective(mesh)) selects.push(mesh);
    const children = node.children;
    for (let i = 0; i < children.length; i++) ssrScanStack.push(children[i]);
  }
}

/** Per-pass handles for the height-fog effect: the wrapped effect plus the
 * scene/camera refs needed to refresh the reconstruction matrices and the sun
 * inscattering direction every frame. */
interface HeightFogHandles {
  effect: HeightFogEffect;
  scene: Scene;
  camera: Camera;
}
const heightFogHandlesByPass = new WeakMap<Pass, HeightFogHandles>();

registerEffect({
  key: 'heightFog',
  // After SSAO (-10), before bloom and friends (order 0): the fog the camera
  // sees must be bloomed, graded and tone-mapped like the scene behind it.
  order: -5,
  create(
    _state: CS,
    entity: number,
    _renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if ((cs.heightFog as Uint8Array)[entity] !== 1) return null;
    const effect = new HeightFogEffect();
    effect.setFog({
      color: (cs.fogColor as Uint32Array)[entity],
      density: (cs.fogDensity as Float32Array)[entity],
      height: (cs.fogHeight as Float32Array)[entity],
      falloff: (cs.fogFalloff as Float32Array)[entity],
      noise: (cs.fogNoise as Float32Array)[entity],
      sunInfluence: (cs.fogSunInfluence as Float32Array)[entity],
      skyHaze: (cs.fogSkyHaze as Float32Array)[entity],
    });
    const pass = wrap(camera, effect);
    heightFogHandlesByPass.set(pass, { effect, scene, camera });
    return pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const handles = heightFogHandlesByPass.get(pass);
    if (!handles) return;
    handles.effect.setFog({
      color: (state.fogColor as Uint32Array)[entity],
      density: (state.fogDensity as Float32Array)[entity],
      height: (state.fogHeight as Float32Array)[entity],
      falloff: (state.fogFalloff as Float32Array)[entity],
      noise: (state.fogNoise as Float32Array)[entity],
      sunInfluence: (state.fogSunInfluence as Float32Array)[entity],
      skyHaze: (state.fogSkyHaze as Float32Array)[entity],
    });
    handles.effect.syncCameraSun(
      handles.camera,
      handles.scene,
      performance.now() / 1000
    );
  },
});

registerEffect({
  key: 'ssr',
  // Reflections belong on the raw lit frame: run before bloom, grading and
  // tone mapping so a reflected highlight blooms and grades exactly like the
  // highlight it is a reflection of. order -10 also puts it ahead of the AA
  // passes that share `position: 'first'`.
  position: 'first',
  order: -10,
  create(
    _state: CS,
    entity: number,
    renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera
  ): Pass | null {
    const cs = Postprocessing as unknown as CS;
    if (!(cs.ssr as Uint8Array)[entity]) return null;
    const size = renderer.getDrawingBufferSize(new Vector2());
    const selects: Mesh[] = [];
    refreshSsrSelects(scene, selects);
    const scale = (cs.ssrResolutionScale as Float32Array)[entity];
    const pass = new ReflectionPass({
      renderer,
      scene,
      camera,
      selects,
      resolutionScale: scale > 0 ? scale : 0.5,
    });
    pass.setSize(size.x, size.y);
    pass.configure({
      intensity: (cs.ssrOpacity as Float32Array)[entity],
      maxDistance: (cs.ssrMaxDistance as Float32Array)[entity],
      thickness: (cs.ssrThickness as Float32Array)[entity],
    });
    ssrHandlesByPass.set(pass as unknown as Pass, {
      scene,
      selects,
      framesUntilRefresh: SSR_SELECTS_REFRESH_FRAMES,
    });
    return pass as unknown as Pass;
  },
  update(state: CS, entity: number, pass: Pass): void {
    const reflection = pass as unknown as ReflectionPass;
    reflection.configure({
      intensity: (state.ssrOpacity as Float32Array)[entity],
      maxDistance: (state.ssrMaxDistance as Float32Array)[entity],
      thickness: (state.ssrThickness as Float32Array)[entity],
    });
    const handles = ssrHandlesByPass.get(pass);
    if (handles && --handles.framesUntilRefresh <= 0) {
      handles.framesUntilRefresh = SSR_SELECTS_REFRESH_FRAMES;
      refreshSsrSelects(handles.scene, handles.selects);
    }
  },
});

export function registerBuiltinEffects(): void {}

// Re-exported for the postprocessing plugin to call on dispose so the sun mesh
// doesn't leak across scene reloads.
export function disposeSharedSunMesh(): void {
  if (sharedSunMesh) {
    if (sharedSunMesh.parent) sharedSunMesh.parent.remove(sharedSunMesh);
    sharedSunMesh.geometry.dispose();
    sharedSunMesh.material.dispose();
    sharedSunMesh = null;
  }
}
