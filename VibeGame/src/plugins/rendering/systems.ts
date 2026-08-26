import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import { CSM } from 'three/examples/jsm/csm/CSM.js';
import type { State } from '../../core';
import { defineSystem, defineQueryLive, type System } from '../../core';
import { WorldTransform } from '../transforms';
import { ThirdPersonCamera } from '../player-controller/components';
import {
  AmbientLight,
  DirectionalLight,
  DistanceCull,
  MainCamera,
  PointLight,
  RenderContext,
  MeshRenderer,
  SpotLight,
} from './components';
import { getOrCreateMesh, hideInstance, updateInstance } from './operations';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import {
  clearDistanceCullChanges,
  markDistanceCullChanged,
} from './cull-changes';
import {
  applyNeutralEnvironment,
  clearCsmMaterialPatch,
  createRenderer,
  createThreeCamera,
  deleteCanvasElement,
  detectGpuTier,
  getCanvasElement,
  getRenderingContext,
  getScene,
  handleWindowResize,
  instanceBoundsDirty,
  recomputeInstanceBounds,
  SHADOW_CONFIG,
  syncCameraSettings,
  threeCameras,
  // Aliased: `RenderingContext` is also a DOM global, and the unqualified name
  // resolves to that one in a type position.
  type RenderingContext as EngineRenderingContext,
} from './utils';
import { applyPcssShadowPatch } from './pcss-shadow';
import { getShadowFocusEntity } from './shadow-focus';
import { setSubtreeMatrixFrozen } from './matrix-freeze';
import {
  getAdaptiveQualityTier,
  TIER_PRESETS,
} from '../adaptive-quality/quality-tiers';

const rendererQuery = defineQueryLive([MeshRenderer]);
const distanceCullQuery = defineQueryLive([DistanceCull, WorldTransform]);
const ambientQuery = defineQueryLive([AmbientLight]);
const directionalQuery = defineQueryLive([DirectionalLight]);
const thirdPersonCameraQuery = defineQueryLive([ThirdPersonCamera]);
const mainCameraTransformQuery = defineQueryLive([MainCamera, WorldTransform]);
const mainCameraQuery = defineQueryLive([MainCamera]);
const renderContextQuery = defineQueryLive([RenderContext]);
const _lightDir = new THREE.Vector3();
const _lightOffset = new THREE.Vector3();
const _lightPos = new THREE.Vector3();
const _shadowCenter = new THREE.Vector3();
const _cameraForward = new THREE.Vector3();
const _cameraQuat = new THREE.Quaternion();
/** Fraction of the shadow radius the frustum centre leads the camera by. */
const SHADOW_CENTER_FORWARD_BIAS = 0.55;
const _lightPosition = new THREE.Vector3();
const _lightQuaternion = new THREE.Quaternion();
const _lightForward = new THREE.Vector3(0, 0, -1);

const pointLightQuery = defineQueryLive([PointLight, WorldTransform]);
const spotLightQuery = defineQueryLive([SpotLight, WorldTransform]);
const entityToPointLightByState = new WeakMap<
  State,
  Map<number, THREE.PointLight>
>();
const entityToSpotLightByState = new WeakMap<
  State,
  Map<number, THREE.SpotLight>
>();
const entityToDirectionalLightByState = new WeakMap<
  State,
  Map<number, THREE.DirectionalLight>
>();
const entityToAmbientLightByState = new WeakMap<
  State,
  Map<number, THREE.HemisphereLight>
>();

function getPointLightMap(state: State): Map<number, THREE.PointLight> {
  let map = entityToPointLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToPointLightByState.set(state, map);
  }
  return map;
}

/** THREE.PointLight objects currently unassigned, kept in the scene at zero
 * intensity. Reusing them (instead of add/remove) keeps the renderer's light
 * count stable — changing it invalidates every program in the material cache. */
const freePointLightsByState = new WeakMap<State, THREE.PointLight[]>();

function getFreePointLights(state: State): THREE.PointLight[] {
  let pool = freePointLightsByState.get(state);
  if (!pool) {
    pool = [];
    freePointLightsByState.set(state, pool);
  }
  return pool;
}

/**
 * How long the pool has to stay unused before a spare light is released, and
 * how far apart releases are spaced.
 *
 * A light at zero intensity is invisible but not free: three bakes the light
 * *count* into every program, so an idle slot is a full point-light iteration
 * in every fragment of the frame (measured at ~1.5 ms per slot in the RPG
 * village). Releasing a slot recompiles the affected materials, so the pool
 * only shrinks after it has been idle for a while, and one light at a time —
 * a player walking out of a lit square pays a little compilation once instead
 * of the shading forever, and a player pacing across the boundary pays
 * neither.
 */
const POINT_LIGHT_POOL_IDLE_MS = 2000;
const POINT_LIGHT_POOL_RELEASE_INTERVAL_MS = 500;

interface PointLightPoolTiming {
  idleSinceMs: number;
  lastReleaseMs: number;
}
const pointLightPoolTiming = new WeakMap<State, PointLightPoolTiming>();

/** Hand one spare light back to the driver once the pool has been idle long
 *  enough, shortening every material's light loop by one iteration. */
function releaseIdlePointLight(
  state: State,
  context: EngineRenderingContext,
  scene: THREE.Scene,
  free: THREE.PointLight[]
): void {
  let timing = pointLightPoolTiming.get(state);
  if (!timing) {
    timing = { idleSinceMs: 0, lastReleaseMs: 0 };
    pointLightPoolTiming.set(state, timing);
  }
  if (free.length === 0) {
    timing.idleSinceMs = 0;
    return;
  }
  const now = performance.now();
  if (timing.idleSinceMs === 0) {
    timing.idleSinceMs = now;
    return;
  }
  if (now - timing.idleSinceMs < POINT_LIGHT_POOL_IDLE_MS) return;
  if (now - timing.lastReleaseMs < POINT_LIGHT_POOL_RELEASE_INTERVAL_MS) return;

  const light = free.pop();
  if (!light) return;
  scene.remove(light);
  light.dispose();
  pointLightCache.delete(light);
  const index = context.lights.pointLights.indexOf(light);
  if (index !== -1) context.lights.pointLights.splice(index, 1);
  timing.lastReleaseMs = now;
}

/**
 * Picks which `PointLight` entities get one of the `MAX_POINT_LIGHTS` slots.
 *
 * Slots used to be first-come-first-served, which is wrong for an open world:
 * a dozen lanterns near the origin claimed every slot at boot and every torch,
 * brazier and shrine the player later walked up to stayed dark — while logging
 * a "limit reached" warning *per entity, per frame* (30k lines in two minutes
 * of simple-rpg). Now the nearest lights to the camera win.
 *
 * Current holders get a 25% distance advantage so a light doesn't flicker
 * on/off while the player walks along the boundary between two clusters.
 */
function selectActivePointLights(
  state: State,
  entities: ArrayLike<number>,
  holders: Map<number, THREE.PointLight>
): Set<number> {
  const camEntities = mainCameraQuery(state.world);
  const camera =
    camEntities.length > 0 ? threeCameras.get(camEntities[0]) : undefined;
  // No camera yet (first frames): keep whatever is already assigned rather
  // than reshuffling on an arbitrary order.
  if (!camera) {
    return new Set(
      Array.from(entities).slice(0, Math.min(entities.length, MAX_POINT_LIGHTS))
    );
  }

  const reachable = filterPointLightsInView(entities, camera);
  if (reachable.length <= MAX_POINT_LIGHTS) {
    return new Set(reachable);
  }

  const worldPosition = camera.getWorldPosition(_lightCameraPosition);
  return pickNearestLightSlots(
    reachable,
    holders,
    worldPosition.x,
    worldPosition.y,
    worldPosition.z,
    MAX_POINT_LIGHTS
  );
}

/** Scratch for the point-light reach test. */
const _lightSphere = new THREE.Sphere();
const _lightFrustum = new THREE.Frustum();
const _lightViewProjection = new THREE.Matrix4();
const _lightViewInverse = new THREE.Matrix4();
const _lightCameraPosition = new THREE.Vector3();

/**
 * Ten percent past the falloff cutoff. The extra radius costs nothing and
 * keeps a torch from switching slots exactly as its sphere grazes the frustum
 * plane, which is where a one-frame pop would be most visible.
 */
const POINT_LIGHT_REACH_MARGIN = 1.1;

/**
 * Drops the point lights that cannot reach anything on screen.
 *
 * A `PointLight` with a positive `distance` has a hard cutoff there, so a
 * light whose sphere misses the view frustum contributes exactly zero to every
 * visible pixel — and still costs a full light iteration in every fragment
 * shader of the frame. In the RPG village only 5 of the 12 torches can reach
 * the view at any time, and the other 7 were being paid for on every pixel.
 *
 * `distance === 0` means "no cutoff" in three, so those stay eligible.
 */
export function filterPointLightsInView(
  entities: ArrayLike<number>,
  camera: THREE.Camera
): number[] {
  camera.updateMatrixWorld();
  // `matrixWorldInverse` is the renderer's to maintain and is a frame stale
  // here; invert the world matrix we just refreshed instead.
  _lightViewInverse.copy(camera.matrixWorld).invert();
  _lightViewProjection.multiplyMatrices(
    (camera as THREE.PerspectiveCamera).projectionMatrix,
    _lightViewInverse
  );
  _lightFrustum.setFromProjectionMatrix(_lightViewProjection);

  const reachable: number[] = [];
  for (let i = 0; i < entities.length; i++) {
    const eid = entities[i];
    const range = PointLight.distance[eid];
    if (!(range > 0)) {
      reachable.push(eid);
      continue;
    }
    _lightSphere.center.set(
      WorldTransform.posX[eid],
      WorldTransform.posY[eid],
      WorldTransform.posZ[eid]
    );
    _lightSphere.radius = range * POINT_LIGHT_REACH_MARGIN;
    if (_lightFrustum.intersectsSphere(_lightSphere)) reachable.push(eid);
  }
  return reachable;
}

/** Hysteresis for {@link pickNearestLightSlots}: a holder is scored as if it
 * were 20% closer ((0.8·d)² = 0.64·d²) so lights don't blink on and off while
 * the player walks the boundary between two clusters. */
const LIGHT_SLOT_HOLDER_BIAS = 0.64;

/**
 * Nearest-`max` light slot assignment, split out from the ECS plumbing so the
 * ranking rules are unit-testable. Reads positions from `WorldTransform`.
 */
export function pickNearestLightSlots(
  entities: ArrayLike<number>,
  holders: ReadonlySet<number> | Map<number, unknown>,
  cx: number,
  cy: number,
  cz: number,
  max: number
): Set<number> {
  const scored: { eid: number; score: number }[] = [];
  for (let i = 0; i < entities.length; i++) {
    const eid = entities[i];
    const dx = WorldTransform.posX[eid] - cx;
    const dy = WorldTransform.posY[eid] - cy;
    const dz = WorldTransform.posZ[eid] - cz;
    const d2 = dx * dx + dy * dy + dz * dz;
    scored.push({
      eid,
      score: holders.has(eid) ? d2 * LIGHT_SLOT_HOLDER_BIAS : d2,
    });
  }
  scored.sort((a, b) => a.score - b.score);

  const active = new Set<number>();
  for (let i = 0; i < max && i < scored.length; i++) active.add(scored[i].eid);
  return active;
}

function getSpotLightMap(state: State): Map<number, THREE.SpotLight> {
  let map = entityToSpotLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToSpotLightByState.set(state, map);
  }
  return map;
}

function getDirectionalLightMap(
  state: State
): Map<number, THREE.DirectionalLight> {
  let map = entityToDirectionalLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToDirectionalLightByState.set(state, map);
  }
  return map;
}

function getAmbientLightMap(state: State): Map<number, THREE.HemisphereLight> {
  let map = entityToAmbientLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToAmbientLightByState.set(state, map);
  }
  return map;
}

// Last-applied light/shadow values keyed by the Three.js light object. The
// sync systems compare the current ECS values against these and only write to
// the light/uniform when something changed, so static lights cost ~0 per
// frame instead of rewriting uniforms and rebuilding the shadow projection
// matrix every tick. Mirrors the dirty-gating used in operations.ts for
// instanced meshes. NaN sentinels force a first-frame apply.
interface AmbientLightCache {
  skyColor: number;
  groundColor: number;
  intensity: number;
}
interface DirectionalLightCache {
  color: number;
  intensity: number;
  mapSize: number;
  bias: number;
  normalBias: number;
  shadowRadius: number;
  frustumLeft: number;
  frustumRight: number;
  frustumTop: number;
  frustumBottom: number;
  frustumNear: number;
  frustumFar: number;
}
interface PointLightCache {
  color: number;
  intensity: number;
  distance: number;
  decay: number;
  castShadow: number;
  px: number;
  py: number;
  pz: number;
  qx: number;
  qy: number;
  qz: number;
  qw: number;
}
interface SpotLightCache {
  color: number;
  intensity: number;
  distance: number;
  decay: number;
  castShadow: number;
  angle: number;
  penumbra: number;
  px: number;
  py: number;
  pz: number;
  qx: number;
  qy: number;
  qz: number;
  qw: number;
}
const ambientLightCache = new WeakMap<
  THREE.HemisphereLight,
  AmbientLightCache
>();
const directionalLightCache = new WeakMap<
  THREE.DirectionalLight,
  DirectionalLightCache
>();
const pointLightCache = new WeakMap<THREE.PointLight, PointLightCache>();
const spotLightCache = new WeakMap<THREE.SpotLight, SpotLightCache>();

// Soft budget guard, not a shader-uniform limit (three recompiles per light
// count). 12 comfortably covers a lit village (torches, hearths, beacons) on
// desktop GPUs. Shadow casting is opt-in per light (`cast-shadow="1"`) — most
// of the 12 are expected to just light, not cast, since a cube-map shadow
// pass per caster adds up fast (see POINT_SHADOW_MAP_SIZE below).
const MAX_POINT_LIGHTS = 12;
/** Entities already warned about the point-light cap (warn once, not per frame). */
const pointLightLimitWarned = new Set<number>();
const MAX_SPOT_LIGHTS = 2;
/** Small on purpose — point-light shadows render 6 faces per caster per
 * frame, so this is the per-face size, not a single 2D map like the sun. */
const POINT_SHADOW_MAP_SIZE = 512;
const SPOT_SHADOW_MAP_SIZE = 1024;

const _shadowBasis = new THREE.Matrix4();
const _shadowBasisInverse = new THREE.Matrix4();
const _shadowUp = new THREE.Vector3(0, 1, 0);
const _shadowUpFallback = new THREE.Vector3(0, 0, 1);
const _shadowOrigin = new THREE.Vector3();
const _snappedCenter = new THREE.Vector3();

/**
 * Quantises the shadow-frustum centre to the shadow map's own texel grid.
 *
 * A frustum that follows the camera by sub-texel amounts re-rasterises every
 * caster into slightly different texels each frame, so every shadow edge in
 * the scene boils/crawls — the faster the camera moves, the worse it reads
 * (a racer at 200 km/h is the worst case there is). Snapping the centre to
 * whole texels in the light's own basis means the depth samples land on the
 * same grid from frame to frame and the edges stay put.
 */
export function snapShadowCenterToTexels(
  center: THREE.Vector3,
  lightDir: THREE.Vector3,
  radius: number,
  mapSize: number
): THREE.Vector3 {
  const texel = (radius * 2) / mapSize;
  if (!(texel > 0)) return center;

  // A basis looking down the light. `lookAt` degenerates when the light is
  // exactly vertical, so pick a different up vector in that case.
  const up = Math.abs(lightDir.y) > 0.999 ? _shadowUpFallback : _shadowUp;
  _shadowBasis.lookAt(_shadowOrigin.set(0, 0, 0), lightDir, up);
  _shadowBasisInverse.copy(_shadowBasis).invert();

  _snappedCenter.copy(center).applyMatrix4(_shadowBasisInverse);
  _snappedCenter.x = Math.round(_snappedCenter.x / texel) * texel;
  _snappedCenter.y = Math.round(_snappedCenter.y / texel) * texel;
  _snappedCenter.applyMatrix4(_shadowBasis);
  return _snappedCenter;
}

/**
 * World point the sun's shadow frustum centres on.
 *
 * Priority: the third-person camera's target (the classic player recipe), then
 * the main camera itself, then the fixed anchor.
 *
 * The camera fallback matters: a game that drives its own camera (the racer's
 * chase camera, a cinematic, a fly-cam) has no `ThirdPersonCamera`, and before
 * this the frustum stayed pinned at the world origin — a 64 m box the player
 * leaves within seconds, so the whole game rendered with the sun casting **no
 * shadow at all** while the shadow pass still ran. The centre is pushed
 * `SHADOW_CENTER_FORWARD_BIAS` of the radius down the camera's view direction
 * so the box covers what is on screen instead of the empty half behind it.
 */
export function resolveShadowCenter(state: State): THREE.Vector3 {
  _shadowCenter.copy(SHADOW_CONFIG.FIXED_FRUSTUM_CENTER);

  // An explicit focus entity wins over every heuristic below. Camera rigs that
  // stand far back from their subject (orthographic isometric, top-down) set
  // this because the camera-centred fallback would put the box behind the
  // character. Unset by default → the chain below is unchanged.
  const focusEid = getShadowFocusEntity(state);
  if (focusEid > 0 && state.hasComponent(focusEid, WorldTransform)) {
    _shadowCenter.set(
      WorldTransform.posX[focusEid],
      WorldTransform.posY[focusEid],
      WorldTransform.posZ[focusEid]
    );
    return _shadowCenter;
  }

  const thirdPersonCams = thirdPersonCameraQuery(state.world);
  if (thirdPersonCams.length > 0) {
    const targetEid = ThirdPersonCamera.target[thirdPersonCams[0]];
    if (targetEid > 0 && state.hasComponent(targetEid, WorldTransform)) {
      _shadowCenter.set(
        WorldTransform.posX[targetEid],
        WorldTransform.posY[targetEid],
        WorldTransform.posZ[targetEid]
      );
      return _shadowCenter;
    }
  }

  const cams = mainCameraTransformQuery(state.world);
  if (cams.length > 0) {
    const eid = cams[0]!;
    _shadowCenter.set(
      WorldTransform.posX[eid],
      WorldTransform.posY[eid],
      WorldTransform.posZ[eid]
    );
    _cameraForward
      .set(0, 0, -1)
      .applyQuaternion(
        _cameraQuat.set(
          WorldTransform.rotX[eid],
          WorldTransform.rotY[eid],
          WorldTransform.rotZ[eid],
          WorldTransform.rotW[eid]
        )
      );
    // Only the horizontal heading biases the box — a camera looking at the
    // ground would otherwise drag the frustum centre under the terrain.
    _cameraForward.y = 0;
    if (_cameraForward.lengthSq() > 1e-6) {
      _cameraForward
        .normalize()
        .multiplyScalar(
          SHADOW_CONFIG.CAMERA_RADIUS * SHADOW_CENTER_FORWARD_BIAS
        );
      _shadowCenter.add(_cameraForward);
    }
  }

  return _shadowCenter;
}

/**
 * Adaptive Quality point-light shadow throttle. Each shadow-casting PointLight
 * renders a 6-face cube shadow map per frame when `shadow.autoUpdate` is on.
 * At higher quality tiers (sustained GPU pressure) we switch to manual updates:
 * autoUpdate off, `needsUpdate` fired every Nth frame. Static torches produce
 * identical shadows so this is visually lossless; moving lights are still
 * captured because their world position is written every frame and the
 * periodic refresh re-renders the cube map from the new position.
 *
 * `refreshFrames === 1` (tier 0 / Max, or no adaptive quality) restores the
 * default per-frame autoUpdate behaviour.
 */
function applyPointShadowThrottle(
  state: State,
  entityToPointLight: Map<number, THREE.PointLight>
): void {
  const tier = getAdaptiveQualityTier(state);
  const preset = TIER_PRESETS[tier] ?? TIER_PRESETS[0];
  const refreshFrames = preset.pointShadowRefreshFrames;
  if (refreshFrames === 1) {
    // Max tier: ensure autoUpdate is on (restores full quality immediately
    // when the scaler upgrades back to tier 0).
    for (const light of entityToPointLight.values()) {
      if (light.castShadow && light.shadow.autoUpdate === false) {
        light.shadow.autoUpdate = true;
      }
    }
    return;
  }
  // Throttled: autoUpdate off; refresh the cube map every Nth frame. Use the
  // global frame counter so all lights refresh on the same cadence (avoids
  // staggering 6-face renders across frames, which would spread the cost but
  // also keep more lights "live" at once).
  const frame = state.time.frameCount;
  const refreshThisFrame = frame % refreshFrames === 0;
  for (const light of entityToPointLight.values()) {
    if (!light.castShadow) continue;
    if (light.shadow.autoUpdate) light.shadow.autoUpdate = false;
    if (refreshThisFrame) light.shadow.needsUpdate = true;
  }
}

/** Same AQ throttle for spot shadow maps (perspective, not cube). */
function applySpotShadowThrottle(
  state: State,
  entityToSpotLight: Map<number, THREE.SpotLight>
): void {
  const tier = getAdaptiveQualityTier(state);
  const preset = TIER_PRESETS[tier] ?? TIER_PRESETS[0];
  const refreshFrames = preset.pointShadowRefreshFrames;
  if (refreshFrames === 1) {
    for (const light of entityToSpotLight.values()) {
      if (light.castShadow && light.shadow.autoUpdate === false) {
        light.shadow.autoUpdate = true;
      }
    }
    return;
  }
  const frame = state.time.frameCount;
  const refreshThisFrame = frame % refreshFrames === 0;
  for (const light of entityToSpotLight.values()) {
    if (!light.castShadow) continue;
    if (light.shadow.autoUpdate) light.shadow.autoUpdate = false;
    if (refreshThisFrame) light.shadow.needsUpdate = true;
  }
}

/** Dispose every geometry/material/texture reachable from `root`, dedup-guarded. */
function disposeSceneGraph(root: THREE.Object3D): void {
  const disposedGeometries = new Set<THREE.BufferGeometry>();
  const disposedMaterials = new Set<THREE.Material>();
  const disposedTextures = new Set<THREE.Texture>();
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const geos = Array.isArray(mesh.geometry) ? mesh.geometry : [mesh.geometry];
    for (const g of geos) {
      if (g && !disposedGeometries.has(g)) {
        try {
          g.dispose();
        } catch {
          /* one failed dispose must not block the rest */
        }
        disposedGeometries.add(g);
      }
    }
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of mats) {
      if (!m || disposedMaterials.has(m)) continue;
      disposedMaterials.add(m);
      for (const k in m) {
        const v = (m as unknown as Record<string, unknown>)[k];
        if (v && typeof v === 'object' && 'isTexture' in v) {
          const tex = v as THREE.Texture;
          if (!disposedTextures.has(tex)) {
            try {
              tex.dispose();
            } catch {
              /* ignore */
            }
            disposedTextures.add(tex);
          }
        }
      }
      try {
        m.dispose();
      } catch {
        /* ignore */
      }
    }
  });
}

export const MeshInstanceSystem: System = defineSystem({
  name: 'MeshInstanceSystem',
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);

    for (const [entity, instanceInfo] of context.entityInstances) {
      if (!state.exists(entity)) {
        const pools = instanceInfo.unlit
          ? context.unlitMeshPools
          : context.meshPools;
        const mesh = pools.get(instanceInfo.poolId);
        if (mesh) {
          hideInstance(mesh, entity, context);
        }
        context.entityInstances.delete(entity);
        context.totalInstanceCount--;
      }
    }

    const rendererEntities = rendererQuery(state.world);
    for (const entity of rendererEntities) {
      const unlit = MeshRenderer.unlit[entity] === 1;
      const mesh = getOrCreateMesh(context, MeshRenderer.shape[entity], unlit);
      if (!mesh) continue;

      if (MeshRenderer.visible[entity] !== 1) {
        hideInstance(mesh, entity, context);
        continue;
      }

      updateInstance(mesh, entity, context, state, unlit);
    }

    // Recompute dirty instance-pool bounds (throttled). computeBoundingSphere
    // is O(instances), so we don't run it per frame — only when a pool was
    // marked dirty (instance added/removed) AND enough frames have passed.
    // Small pools recompute immediately so frustumCulled returns sooner.
    const frame = state.time.frameCount;
    const due = frame % INSTANCE_BOUNDS_RECOMPUTE_INTERVAL === 0;
    for (const mesh of context.meshPools.values()) {
      if (!instanceBoundsDirty(mesh)) continue;
      if (due || mesh.count <= INSTANCE_BOUNDS_IMMEDIATE_MAX) {
        recomputeInstanceBounds(mesh);
      }
    }
    for (const mesh of context.unlitMeshPools.values()) {
      if (!instanceBoundsDirty(mesh)) continue;
      if (due || mesh.count <= INSTANCE_BOUNDS_IMMEDIATE_MAX) {
        recomputeInstanceBounds(mesh);
      }
    }
  },
});

/** How many frames between instance-bounds recomputes. Balances cull accuracy
 *  against the O(instances) cost of computeBoundingSphere. Every ~0.13s at
 *  60fps restores frustumCulled sooner after spawn/despawn dirty marks. */
const INSTANCE_BOUNDS_RECOMPUTE_INTERVAL = 8;
/** Pools at or below this instance count recompute bounds without waiting. */
const INSTANCE_BOUNDS_IMMEDIATE_MAX = 32;

/** Saved `castShadow` while DistanceCull hides a GLTF (shadow map still draws
 *  invisible=false casters in some paths; force off to cut fill cost). */
const distanceCullShadowSaved = new WeakMap<THREE.Object3D, boolean>();
const distanceCullLastFrame = new WeakMap<State, number>();
const DISTANCE_CULL_INTERVAL_FRAMES = 3;

/**
 * Apply / undo the culled state on a whole GLB subtree: drop its shadow
 * casters and stop paying for its matrix updates while it is hidden.
 */
function applyDistanceCullToGroup(root: THREE.Object3D, culled: boolean): void {
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (culled) {
      if (!distanceCullShadowSaved.has(mesh)) {
        distanceCullShadowSaved.set(mesh, mesh.castShadow);
      }
      mesh.castShadow = false;
    } else {
      const prev = distanceCullShadowSaved.get(mesh);
      if (prev !== undefined) {
        mesh.castShadow = prev;
        distanceCullShadowSaved.delete(mesh);
      }
    }
  });
  setSubtreeMatrixFrozen(root, culled);
}

export const DistanceCullSystem: System = defineSystem({
  name: 'DistanceCullSystem',
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const frame = state.time.frameCount;
    const lastFrame = distanceCullLastFrame.get(state);
    if (
      lastFrame !== undefined &&
      frame - lastFrame < DISTANCE_CULL_INTERVAL_FRAMES
    ) {
      return;
    }
    distanceCullLastFrame.set(state, frame);
    // Consumers (the instancing pool) read the previous pass's flips; a new
    // pass starts a new batch. Clearing here rather than in a consumer keeps
    // the events alive no matter which order the draw systems run in.
    clearDistanceCullChanges(state);

    const camEntities = mainCameraQuery(state.world);
    if (camEntities.length === 0) return;
    const camera = threeCameras.get(camEntities[0]);
    if (!camera) return;

    const camX = camera.position.x;
    const camZ = camera.position.z;

    const HYSTERESIS = 0.9;

    for (const eid of distanceCullQuery(state.world)) {
      const maxDist = DistanceCull.maxDistance[eid];
      if (maxDist <= 0) continue;

      const dx = WorldTransform.posX[eid] - camX;
      const dz = WorldTransform.posZ[eid] - camZ;
      const distSq = dx * dx + dz * dz;

      const wasCulled = DistanceCull.culled[eid] === 1;
      const maxSq = maxDist * maxDist;
      const hystSq = maxSq * HYSTERESIS * HYSTERESIS;
      const shouldCull = wasCulled ? distSq >= hystSq : distSq > maxSq;

      if (shouldCull === wasCulled) continue;

      DistanceCull.culled[eid] = shouldCull ? 1 : 0;
      // Publish the edge so the instancing pool does not have to poll for it.
      markDistanceCullChanged(state, eid);

      const gltfGroup = getGltfRootGroup(state, eid);
      if (gltfGroup) {
        gltfGroup.visible = !shouldCull;
        applyDistanceCullToGroup(gltfGroup, shouldCull);
      }

      if (state.hasComponent(eid, MeshRenderer)) {
        MeshRenderer.visible[eid] = shouldCull ? 0 : 1;
      }
    }
  },
});

interface CsmCache {
  cascades: number;
  maxFar: number;
  shadowMapSize: number;
  color: number;
}
const csmCacheByState = new WeakMap<State, CsmCache>();

/** Last cam/light pose used for `csm.update*` — skip when still. */
interface CsmUpdatePose {
  camX: number;
  camY: number;
  camZ: number;
  dirX: number;
  dirY: number;
  dirZ: number;
}
const csmUpdatePoseByState = new WeakMap<State, CsmUpdatePose>();
const CSM_CAM_EPS_SQ = 0.0025; // ~5 cm
const CSM_DIR_EPS_SQ = 1e-8;

function disposeCsm(state: State, scene: THREE.Scene): void {
  const context = getRenderingContext(state);
  if (!context.csm) return;
  // Un-patch so a later CSM instance (recreated with different settings, or
  // re-enabled after being turned off) re-runs setupMaterial instead of
  // silently staying in the disposed (non-CSM) shader state csm.dispose()
  // below leaves them in.
  // @types/three's `shaders: Map<unknown, string>` doesn't match the actual
  // `Map<Material, object|null>` from the JS source — cast to the real type.
  for (const mat of context.csm.shaders.keys() as IterableIterator<THREE.Material>) {
    clearCsmMaterialPatch(mat);
  }
  for (const light of context.csm.lights) {
    scene.remove(light.target);
    scene.remove(light);
  }
  context.csm.dispose();
  context.csm = null;
  csmCacheByState.delete(state);
  csmUpdatePoseByState.delete(state);
  // Restore the bootstrap light CSM borrowed the scene from — the plain-light
  // path's "adopt the bootstrap light" logic (below) picks it back up the
  // next time an entity wants a normal (non-CSM) directional light.
  const boot = context.lights.directional;
  if (boot && boot.parent !== scene) scene.add(boot);
}

/**
 * Cascaded shadow maps for one `DirectionalLight` entity opted in via
 * `directional-light="csm: 1"`. CSM owns its own internal directional lights
 * (one per cascade) — this entity never gets a plain `THREE.DirectionalLight`
 * while csm is active. Cascade count / max distance / map size can't change
 * on a live `CSM` instance, so those trigger a full dispose + recreate;
 * color/intensity/direction update in place every frame.
 */
function updateCsmDirectionalLight(
  state: State,
  scene: THREE.Scene,
  entity: number
): void {
  const context = getRenderingContext(state);
  const camEntities = mainCameraQuery(state.world);
  const camera =
    camEntities.length > 0 ? threeCameras.get(camEntities[0]!) : undefined;
  if (!camera) return;

  const cascades = Math.max(1, DirectionalLight.csmCascades[entity]);
  const maxFar = DirectionalLight.csmMaxFar[entity];
  const shadowMapSize = Math.max(
    1,
    DirectionalLight.shadowMapSize[entity] || 2048
  );
  const color = DirectionalLight.color[entity];
  const intensity = DirectionalLight.intensity[entity];

  let cache = csmCacheByState.get(state);
  if (
    !context.csm ||
    !cache ||
    cache.cascades !== cascades ||
    cache.maxFar !== maxFar ||
    cache.shadowMapSize !== shadowMapSize
  ) {
    disposeCsm(state, scene);
    // The bootstrap directional light (initializeContext) is always in the
    // scene, normally "adopted" as the plain-light path's THREE object. CSM
    // brings its own `cascades` lights instead — leaving the bootstrap one
    // in the scene on top of those makes three see one MORE directional
    // light than CSM_cascades has slots for (NUM_DIR_LIGHTS mismatch), which
    // fails fragment shader compilation with an out-of-range array index.
    scene.remove(context.lights.directional);
    context.csm = new CSM({
      camera,
      parent: scene,
      cascades,
      maxFar,
      shadowMapSize,
      lightIntensity: intensity,
    });
    // CSM.js leaves shadow.normalBias at three's default (0) — fine on flat
    // ground, but curved/rounded meshes (the hero, props) self-shadow into
    // banding artifacts without it. Match the plain-light path's value.
    for (const light of context.csm.lights) light.shadow.normalBias = 0.02;
    cache = { cascades, maxFar, shadowMapSize, color: NaN };
    csmCacheByState.set(state, cache);
  }

  const csm = context.csm;

  // CSM's lightDirection is the direction light TRAVELS (light → scene);
  // our directionX/Y/Z is the direction TOWARD the light source (scene →
  // light, same convention the plain-light path uses to place the light
  // behind the shadow-camera target) — negate to convert between them.
  _lightDir
    .set(
      DirectionalLight.directionX[entity],
      DirectionalLight.directionY[entity],
      DirectionalLight.directionZ[entity]
    )
    .normalize()
    .negate();
  csm.lightDirection.copy(_lightDir);

  csm.lightIntensity = intensity;
  if (cache.color !== color) {
    for (const light of csm.lights) light.color.setHex(color);
    cache.color = color;
  }
  for (const light of csm.lights) light.intensity = intensity;

  const cam = camera.position;
  const pose = csmUpdatePoseByState.get(state);
  const camMoved =
    !pose ||
    (cam.x - pose.camX) ** 2 +
      (cam.y - pose.camY) ** 2 +
      (cam.z - pose.camZ) ** 2 >
      CSM_CAM_EPS_SQ;
  const dirMoved =
    !pose ||
    (_lightDir.x - pose.dirX) ** 2 +
      (_lightDir.y - pose.dirY) ** 2 +
      (_lightDir.z - pose.dirZ) ** 2 >
      CSM_DIR_EPS_SQ;
  if (!camMoved && !dirMoved) return;

  if (!pose) {
    csmUpdatePoseByState.set(state, {
      camX: cam.x,
      camY: cam.y,
      camZ: cam.z,
      dirX: _lightDir.x,
      dirY: _lightDir.y,
      dirZ: _lightDir.z,
    });
  } else {
    pose.camX = cam.x;
    pose.camY = cam.y;
    pose.camZ = cam.z;
    pose.dirX = _lightDir.x;
    pose.dirY = _lightDir.y;
    pose.dirZ = _lightDir.z;
  }

  csm.updateFrustums();
  csm.update();
}

export const LightSyncSystem: System = defineSystem({
  name: 'LightSyncSystem',
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const entityToAmbientLight = getAmbientLightMap(state);
    const entityToDirectionalLight = getDirectionalLightMap(state);

    // --- Ambient lights (per-entity, Map-based) ---
    for (const [eid, light] of entityToAmbientLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        light.dispose();
        entityToAmbientLight.delete(eid);
      }
    }

    const ambients = ambientQuery(state.world);
    for (const entity of ambients) {
      let light = entityToAmbientLight.get(entity);
      if (!light) {
        // Adopt the bootstrap hemisphere light (already added to the scene in
        // initializeContext) for the first ambient entity so it is actually
        // synced rather than left orphaned; extra ambient entities get fresh
        // lights.
        const boot = getRenderingContext(state).lights.ambient;
        if (boot && ![...entityToAmbientLight.values()].includes(boot)) {
          light = boot;
        } else {
          light = new THREE.HemisphereLight();
          scene.add(light);
        }
        entityToAmbientLight.set(entity, light);
      }

      const sky = AmbientLight.skyColor[entity];
      const ground = AmbientLight.groundColor[entity];
      const intensity = AmbientLight.intensity[entity];
      let cache = ambientLightCache.get(light);
      if (cache === undefined) {
        cache = { skyColor: NaN, groundColor: NaN, intensity: NaN };
        ambientLightCache.set(light, cache);
      }
      if (cache.skyColor !== sky) {
        light.color.setHex(sky);
        cache.skyColor = sky;
      }
      if (cache.groundColor !== ground) {
        light.groundColor.setHex(ground);
        cache.groundColor = ground;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }
    }

    // --- Directional lights (per-entity, Map-based) ---
    for (const [eid, light] of entityToDirectionalLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        if (light.target) scene.remove(light.target);
        light.dispose();
        entityToDirectionalLight.delete(eid);
      }
    }

    const directionals = directionalQuery(state.world);
    let csmActive = false;
    for (const entity of directionals) {
      // PCSS is a global shader-chunk patch — apply it lazily the first time
      // any directional light opts in, then it stays on for the renderer's
      // lifetime (existing materials recompile on next shadow render). Must
      // run before the CSM branch below: `csm: 1; pcss: 1` on the same light
      // patches the chunk the CSM cascade lights sample through.
      if (DirectionalLight.pcss[entity] === 1) {
        applyPcssShadowPatch();
      }
      if (DirectionalLight.csm[entity] === 1) {
        csmActive = true;
        updateCsmDirectionalLight(state, scene, entity);
        continue;
      }
      let light = entityToDirectionalLight.get(entity);
      if (!light) {
        // Adopt the bootstrap directional light (already in the scene with its
        // target) for the first directional entity so it is positioned/synced
        // instead of left orphaned; extra directional entities get fresh lights.
        const boot = getRenderingContext(state).lights.directional;
        if (boot && ![...entityToDirectionalLight.values()].includes(boot)) {
          light = boot;
        } else {
          light = new THREE.DirectionalLight();
          scene.add(light);
          scene.add(light.target);
        }
        entityToDirectionalLight.set(entity, light);
      }

      const color = DirectionalLight.color[entity];
      const intensity = DirectionalLight.intensity[entity];
      let cache = directionalLightCache.get(light);
      if (cache === undefined) {
        cache = {
          color: NaN,
          intensity: NaN,
          mapSize: NaN,
          bias: NaN,
          normalBias: NaN,
          shadowRadius: NaN,
          frustumLeft: NaN,
          frustumRight: NaN,
          frustumTop: NaN,
          frustumBottom: NaN,
          frustumNear: NaN,
          frustumFar: NaN,
        };
        directionalLightCache.set(light, cache);
      }

      if (cache.color !== color) {
        light.color.setHex(color);
        cache.color = color;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }

      _lightDir
        .set(
          DirectionalLight.directionX[entity],
          DirectionalLight.directionY[entity],
          DirectionalLight.directionZ[entity]
        )
        .normalize();

      if (DirectionalLight.castShadow[entity] === 1) {
        light.castShadow = true;

        // Uint32 default 0 → WebGL "DEPTH_ATTACHMENT: Attachment has no width".
        const mapSize = Math.max(
          1,
          DirectionalLight.shadowMapSize[entity] || 2048
        );
        const bias = -0.0001;
        const normalBias = 0.02;
        // Blur radius for VSMShadowMap soft edges (texel-space, not world units).
        const shadowRadius = 1.5;
        // Coverage follows the map size at a fixed sharpness instead of being
        // a constant: a game that pays for a 4096 map wants a bigger shadowed
        // area, not the same 64 m box at twice the texel density. The divisor
        // reproduces the previous 32 m radius at the default 2048.
        const radius = mapSize / (2 * SHADOW_CONFIG.TEXELS_PER_METER);
        const near = SHADOW_CONFIG.NEAR_PLANE;
        const far = SHADOW_CONFIG.FAR_PLANE;

        // Static shadow config: apply + rebuild projection only when a value
        // changed, not every frame.
        let shadowChanged = false;
        if (cache.mapSize !== mapSize) {
          light.shadow.mapSize.width = mapSize;
          light.shadow.mapSize.height = mapSize;
          cache.mapSize = mapSize;
          shadowChanged = true;
        }
        if (cache.bias !== bias) {
          light.shadow.bias = bias;
          cache.bias = bias;
          shadowChanged = true;
        }
        if (cache.normalBias !== normalBias) {
          light.shadow.normalBias = normalBias;
          cache.normalBias = normalBias;
          shadowChanged = true;
        }
        if (cache.shadowRadius !== shadowRadius) {
          light.shadow.radius = shadowRadius;
          cache.shadowRadius = shadowRadius;
          shadowChanged = true;
        }
        const shadowCamera = light.shadow.camera as THREE.OrthographicCamera;
        if (
          cache.frustumLeft !== -radius ||
          cache.frustumRight !== radius ||
          cache.frustumTop !== radius ||
          cache.frustumBottom !== -radius ||
          cache.frustumNear !== near ||
          cache.frustumFar !== far
        ) {
          shadowCamera.left = -radius;
          shadowCamera.right = radius;
          shadowCamera.top = radius;
          shadowCamera.bottom = -radius;
          shadowCamera.near = near;
          shadowCamera.far = far;
          cache.frustumLeft = -radius;
          cache.frustumRight = radius;
          cache.frustumTop = radius;
          cache.frustumBottom = -radius;
          cache.frustumNear = near;
          cache.frustumFar = far;
          shadowChanged = true;
        }
        if (shadowChanged) shadowCamera.updateProjectionMatrix();

        // Shadow frustum follows the player — skip matrix work when still.
        const shadowCenter = snapShadowCenterToTexels(
          resolveShadowCenter(state),
          _lightDir,
          radius,
          mapSize
        );
        _lightPos
          .copy(shadowCenter)
          .add(
            _lightOffset
              .copy(_lightDir)
              .multiplyScalar(DirectionalLight.distance[entity])
          );

        // The centre is already quantised to whole texels, so any difference
        // at all is a real move — a 5 cm dead-band here would swallow entire
        // texel steps on a 4096 map and leave the frustum lagging the camera.
        const centerMoved =
          Math.abs(light.target.position.x - shadowCenter.x) > 1e-4 ||
          Math.abs(light.target.position.y - shadowCenter.y) > 1e-4 ||
          Math.abs(light.target.position.z - shadowCenter.z) > 1e-4 ||
          Math.abs(light.position.x - _lightPos.x) > 1e-4 ||
          Math.abs(light.position.y - _lightPos.y) > 1e-4 ||
          Math.abs(light.position.z - _lightPos.z) > 1e-4;
        if (centerMoved) {
          light.position.copy(_lightPos);
          light.target.position.copy(shadowCenter);
          light.target.updateMatrixWorld();
          shadowCamera.position.copy(_lightPos);
          shadowCamera.lookAt(shadowCenter);
          shadowCamera.updateMatrixWorld();
        }
      } else {
        light.castShadow = false;
        const shadowMap = light.shadow.map;
        if (shadowMap) {
          shadowMap.dispose();
          light.shadow.map = null;
        }
      }
    }
    // No entity currently wants CSM this frame — release it (it owns its own
    // internal directional lights, so leaving it around would keep shadowing
    // the scene with a sun no `DirectionalLight` entity asked for anymore).
    if (!csmActive) disposeCsm(state, scene);
  },
});

export const PointSpotLightSyncSystem: System = defineSystem({
  name: 'PointSpotLightSyncSystem',
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    const scene = getScene(state);
    if (!scene) return;

    const entityToPointLight = getPointLightMap(state);
    const entityToSpotLight = getSpotLightMap(state);

    const freePointLights = getFreePointLights(state);

    for (const [eid, light] of entityToPointLight) {
      if (!state.exists(eid)) {
        // Back to the pool instead of scene.remove + dispose: the light count
        // is baked into every compiled program, so shrinking it would stall
        // the frame recompiling the whole scene's materials.
        light.intensity = 0;
        pointLightCache.delete(light);
        entityToPointLight.delete(eid);
        freePointLights.push(light);
      }
    }

    for (const [eid, light] of entityToSpotLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        if (light.target) scene.remove(light.target);
        light.dispose();
        entityToSpotLight.delete(eid);
        const idx = context.lights.spotLights.indexOf(light);
        if (idx !== -1) context.lights.spotLights.splice(idx, 1);
      }
    }

    const pointEntities = pointLightQuery(state.world);
    const activePointEntities = selectActivePointLights(
      state,
      pointEntities,
      entityToPointLight
    );

    // Hand back the slots of entities that lost the proximity contest.
    for (const [eid, light] of entityToPointLight) {
      if (activePointEntities.has(eid)) continue;
      light.intensity = 0;
      pointLightCache.delete(light);
      entityToPointLight.delete(eid);
      freePointLights.push(light);
    }

    for (const eid of activePointEntities) {
      let light = entityToPointLight.get(eid);
      if (!light) {
        light = freePointLights.pop();
      }
      if (!light) {
        if (context.lights.pointLights.length >= MAX_POINT_LIGHTS) {
          // Unreachable while the selection above caps at MAX_POINT_LIGHTS —
          // kept as a guard, and warned once per entity so a stuck state can't
          // flood the console at frame rate.
          if (!pointLightLimitWarned.has(eid)) {
            pointLightLimitWarned.add(eid);
            logger.warn(
              `PointLight limit (${MAX_POINT_LIGHTS}) reached — skipping entity ${eid}`
            );
          }
          continue;
        }
        light = new THREE.PointLight();
        scene.add(light);
        context.lights.pointLights.push(light);
      }
      if (entityToPointLight.get(eid) !== light) {
        entityToPointLight.set(eid, light);
      }

      const color = PointLight.color[eid];
      const intensity = PointLight.intensity[eid];
      const distance = PointLight.distance[eid];
      const decay = PointLight.decay[eid];
      const castShadow = PointLight.castShadow[eid];
      let cache = pointLightCache.get(light);
      if (cache === undefined) {
        cache = {
          color: NaN,
          intensity: NaN,
          distance: NaN,
          decay: NaN,
          castShadow: NaN,
          px: NaN,
          py: NaN,
          pz: NaN,
          qx: NaN,
          qy: NaN,
          qz: NaN,
          qw: NaN,
        };
        pointLightCache.set(light, cache);
      }
      if (cache.color !== color) {
        light.color.setHex(color);
        cache.color = color;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }
      if (cache.distance !== distance) {
        light.distance = distance;
        cache.distance = distance;
      }
      if (cache.decay !== decay) {
        light.decay = decay;
        cache.decay = decay;
      }
      if (cache.castShadow !== castShadow) {
        light.castShadow = castShadow === 1;
        if (light.castShadow) {
          // Cube-map shadow (6 faces) — a torch/lantern is a small local
          // light, so a modest map keeps the per-light cost sane even with
          // several casters active at once (each author opts in per-light
          // via `cast-shadow="1"`, there's no automatic global cap here).
          light.shadow.mapSize.set(
            POINT_SHADOW_MAP_SIZE,
            POINT_SHADOW_MAP_SIZE
          );
          light.shadow.camera.near = 0.1;
          // `distance` 0 means "no falloff cutoff" in three's PointLight, not
          // "no range" — fall back to a torch-scale far plane in that case.
          light.shadow.camera.far = distance > 0 ? distance : 20;
          light.shadow.bias = -0.001;
          light.shadow.needsUpdate = true;
        }
        cache.castShadow = castShadow;
      }

      const px = WorldTransform.posX[eid];
      const py = WorldTransform.posY[eid];
      const pz = WorldTransform.posZ[eid];
      const qx = WorldTransform.rotX[eid];
      const qy = WorldTransform.rotY[eid];
      const qz = WorldTransform.rotZ[eid];
      const qw = WorldTransform.rotW[eid];
      if (
        cache.px !== px ||
        cache.py !== py ||
        cache.pz !== pz ||
        cache.qx !== qx ||
        cache.qy !== qy ||
        cache.qz !== qz ||
        cache.qw !== qw
      ) {
        light.position.set(px, py, pz);
        light.quaternion.set(qx, qy, qz, qw);
        cache.px = px;
        cache.py = py;
        cache.pz = pz;
        cache.qx = qx;
        cache.qy = qy;
        cache.qz = qz;
        cache.qw = qw;
      }
    }

    // Adaptive Quality point-light shadow throttle. Each shadow-casting
    // PointLight renders a 6-face cube map every frame when autoUpdate is on
    // (12 extra scene renders/frame with 2 casters). Static torches/lanterns
    // produce identical shadows across frames, so at higher quality tiers we
    // switch to manual updates: autoUpdate off, and `needsUpdate` fired only
    // every Nth frame. Moving lights (torches attached to entities that move)
    // are still captured because their position is written above every frame
    // and the periodic refresh re-renders the cube map.
    releaseIdlePointLight(state, context, scene, freePointLights);

    applyPointShadowThrottle(state, entityToPointLight);
    applySpotShadowThrottle(state, entityToSpotLight);

    const spotEntities = spotLightQuery(state.world);
    for (const eid of spotEntities) {
      let light = entityToSpotLight.get(eid);
      if (!light) {
        if (context.lights.spotLights.length >= MAX_SPOT_LIGHTS) {
          logger.warn(
            `SpotLight limit (${MAX_SPOT_LIGHTS}) reached — skipping entity ${eid}`
          );
          continue;
        }
        light = new THREE.SpotLight();
        scene.add(light);
        scene.add(light.target);
        entityToSpotLight.set(eid, light);
        context.lights.spotLights.push(light);
      }

      const color = SpotLight.color[eid];
      const intensity = SpotLight.intensity[eid];
      const distance = SpotLight.distance[eid];
      const decay = SpotLight.decay[eid];
      const angle = SpotLight.angle[eid];
      const penumbra = SpotLight.penumbra[eid];
      const castShadow = SpotLight.castShadow[eid];
      let cache = spotLightCache.get(light);
      if (cache === undefined) {
        cache = {
          color: NaN,
          intensity: NaN,
          distance: NaN,
          decay: NaN,
          castShadow: NaN,
          angle: NaN,
          penumbra: NaN,
          px: NaN,
          py: NaN,
          pz: NaN,
          qx: NaN,
          qy: NaN,
          qz: NaN,
          qw: NaN,
        };
        spotLightCache.set(light, cache);
      }
      if (cache.color !== color) {
        light.color.setHex(color);
        cache.color = color;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }
      if (cache.distance !== distance) {
        light.distance = distance;
        cache.distance = distance;
      }
      if (cache.decay !== decay) {
        light.decay = decay;
        cache.decay = decay;
      }
      if (cache.angle !== angle) {
        light.angle = angle;
        cache.angle = angle;
      }
      if (cache.penumbra !== penumbra) {
        light.penumbra = penumbra;
        cache.penumbra = penumbra;
      }
      if (cache.castShadow !== castShadow) {
        light.castShadow = castShadow === 1;
        if (light.castShadow) {
          // Perspective shadow camera (not a cube map) — spot lights are
          // capped at MAX_SPOT_LIGHTS=2, so a sharper map is affordable.
          light.shadow.mapSize.set(SPOT_SHADOW_MAP_SIZE, SPOT_SHADOW_MAP_SIZE);
          light.shadow.camera.near = 0.1;
          light.shadow.camera.far = distance > 0 ? distance : 30;
          light.shadow.bias = -0.001;
          light.shadow.needsUpdate = true;
        }
        cache.castShadow = castShadow;
      }

      const px = WorldTransform.posX[eid];
      const py = WorldTransform.posY[eid];
      const pz = WorldTransform.posZ[eid];
      const qx = WorldTransform.rotX[eid];
      const qy = WorldTransform.rotY[eid];
      const qz = WorldTransform.rotZ[eid];
      const qw = WorldTransform.rotW[eid];
      if (
        cache.px !== px ||
        cache.py !== py ||
        cache.pz !== pz ||
        cache.qx !== qx ||
        cache.qy !== qy ||
        cache.qz !== qz ||
        cache.qw !== qw
      ) {
        _lightPosition.set(px, py, pz);
        _lightQuaternion.set(qx, qy, qz, qw);
        light.position.copy(_lightPosition);
        light.quaternion.copy(_lightQuaternion);
        _lightForward.set(0, 0, -1).applyQuaternion(_lightQuaternion);
        light.target.position.copy(_lightPosition).add(_lightForward);
        cache.px = px;
        cache.py = py;
        cache.pz = pz;
        cache.qx = qx;
        cache.qy = qy;
        cache.qz = qz;
        cache.qw = qw;
      }
    }
  },
});

// NOTE: RendererSetupSystem was removed — its logic was identical to
// SceneRenderSystem and caused duplicate resize listeners / double-setup.
// All renderer creation is now handled by SceneRenderSystem.

export const CameraSyncSystem: System = defineSystem({
  name: 'CameraSyncSystem',
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const cameraEntities = mainCameraTransformQuery(state.world);

    for (const entity of cameraEntities) {
      let camera = threeCameras.get(entity);
      if (!camera) {
        camera = createThreeCamera(
          entity,
          state,
          MainCamera.projection[entity],
          MainCamera.fov[entity],
          MainCamera.orthoSize[entity]
        );
      }

      camera.position.set(
        WorldTransform.posX[entity],
        WorldTransform.posY[entity],
        WorldTransform.posZ[entity]
      );

      camera.quaternion.set(
        WorldTransform.rotX[entity],
        WorldTransform.rotY[entity],
        WorldTransform.rotZ[entity],
        WorldTransform.rotW[entity]
      );

      syncCameraSettings(camera, entity, state);
    }
  },
});

export const SceneRenderSystem: System = defineSystem({
  name: 'SceneRenderSystem',
  group: 'draw',
  last: true,
  async setup(state: State) {
    if (state.headless) return;
    const contextEntities = renderContextQuery(state.world);
    if (contextEntities.length === 0) return;

    const context = getRenderingContext(state);
    if (context.renderer) return;

    const entity = contextEntities[0];
    const canvas = getCanvasElement(entity);
    if (!canvas) return;

    const clearColor = RenderContext.clearColor[entity];
    const renderer = await createRenderer(canvas, clearColor);

    context.renderer = renderer;
    context.canvas = canvas;
    void detectGpuTier(
      state,
      renderer,
      renderer.getContext() as WebGL2RenderingContext
    );
    applyNeutralEnvironment(renderer, context.scene);
    // The post-processing scene pass renders scene.background (not the renderer
    // clear colour), so mirror the clear colour there or the sky goes black.
    if (clearColor !== 0)
      context.scene.background = new THREE.Color(clearColor);

    const onResize = () => handleWindowResize(state, renderer);
    context.resizeHandler = onResize;
    window.addEventListener('resize', onResize);
    // Canvas may already have CSS size — sync before first composer/warmup draw.
    onResize();
  },
  update(state: State) {
    if (state.headless) return;
  },
  dispose(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.resizeHandler) {
      window.removeEventListener('resize', context.resizeHandler);
      context.resizeHandler = undefined;
    }
    if (context.renderer) {
      context.renderer.setAnimationLoop(null);
      // Drop the GL context before dispose — soft HMR without this leaves
      // Firefox holding zombie contexts until the browser process is killed.
      try {
        context.renderer.forceContextLoss();
      } catch (e) {
        logger.warn('forceContextLoss failed', e);
      }
      context.renderer.dispose();
      context.renderer = undefined;
      context.canvas = undefined;
    }

    // Dispose entity-level lights still held by the per-entity maps.
    const entityToPointLight = getPointLightMap(state);
    const entityToSpotLight = getSpotLightMap(state);
    const entityToDirectionalLight = getDirectionalLightMap(state);
    const entityToAmbientLight = getAmbientLightMap(state);
    entityToPointLight.forEach((light) => {
      try {
        context.scene.remove(light);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose point light', e);
      }
    });
    entityToSpotLight.forEach((light) => {
      try {
        context.scene.remove(light);
        if (light.target) context.scene.remove(light.target);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose spot light', e);
      }
    });
    entityToDirectionalLight.forEach((light) => {
      try {
        context.scene.remove(light);
        if (light.target) context.scene.remove(light.target);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose directional light', e);
      }
    });
    entityToAmbientLight.forEach((light) => {
      try {
        context.scene.remove(light);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose ambient light', e);
      }
    });
    entityToPointLight.clear();
    entityToSpotLight.clear();
    entityToDirectionalLight.clear();
    entityToAmbientLight.clear();

    // Dispose bootstrap lights created in initializeContext.
    try {
      context.lights.ambient.dispose();
    } catch (e) {
      logger.warn('Failed to dispose ambient bootstrap light', e);
    }
    try {
      context.scene.remove(context.lights.directional.target);
      context.lights.directional.dispose();
    } catch (e) {
      logger.warn('Failed to dispose directional bootstrap light', e);
    }

    // Dispose InstancedMesh pools (each holds GPU instance buffers).
    try {
      context.meshPools.forEach((mesh) => mesh.dispose());
      context.meshPools.clear();
    } catch (e) {
      logger.warn('Failed to dispose mesh pools', e);
    }
    try {
      context.unlitMeshPools.forEach((mesh) => mesh.dispose());
      context.unlitMeshPools.clear();
    } catch (e) {
      logger.warn('Failed to dispose unlit mesh pools', e);
    }

    // Dispose shared bootstrap geometries + materials.
    try {
      context.geometries.forEach((g) => g.dispose());
      context.geometries.clear();
    } catch (e) {
      logger.warn('Failed to dispose geometries', e);
    }
    try {
      context.material.dispose();
    } catch (e) {
      logger.warn('Failed to dispose material', e);
    }
    try {
      context.unlitMaterial.dispose();
    } catch (e) {
      logger.warn('Failed to dispose unlit material', e);
    }

    // Dispose the PMREM environment texture applied by applyNeutralEnvironment.
    try {
      const env = context.scene.environment;
      if (env && (env as THREE.Texture).isTexture) {
        (env as THREE.Texture).dispose();
      }
      context.scene.environment = null;
    } catch (e) {
      logger.warn('Failed to dispose scene environment', e);
    }

    // Dispose remaining geometry/material/texture reachable from the scene
    // (entity GLB meshes, etc.). Dedup guards against double-dispose of the
    // shared bootstrap resources disposed above.
    try {
      disposeSceneGraph(context.scene);
    } catch (e) {
      logger.warn('Failed to dispose scene graph', e);
    }

    // Drop the camera cache so a re-init does not reuse stale cameras.
    threeCameras.clear();

    const contextEntities = renderContextQuery(state.world);
    for (const entity of contextEntities) {
      deleteCanvasElement(entity);
    }
  },
});
