import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import { CSM } from 'three/examples/jsm/csm/CSM.js';
import type { State } from '../../core';
import { defineQuery, type System } from '../../core';
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
  SHADOW_CONFIG,
  syncCameraSettings,
  threeCameras,
} from './utils';
import { applyPcssShadowPatch } from './pcss-shadow';

const rendererQuery = defineQuery([MeshRenderer]);
const distanceCullQuery = defineQuery([DistanceCull, WorldTransform]);
const ambientQuery = defineQuery([AmbientLight]);
const directionalQuery = defineQuery([DirectionalLight]);
const thirdPersonCameraQuery = defineQuery([ThirdPersonCamera]);
const mainCameraTransformQuery = defineQuery([MainCamera, WorldTransform]);
const mainCameraQuery = defineQuery([MainCamera]);
const renderContextQuery = defineQuery([RenderContext]);
const _lightDir = new THREE.Vector3();
const _lightOffset = new THREE.Vector3();
const _lightPos = new THREE.Vector3();
const _shadowCenter = new THREE.Vector3();
const _lightPosition = new THREE.Vector3();
const _lightQuaternion = new THREE.Quaternion();
const _lightForward = new THREE.Vector3(0, 0, -1);

const pointLightQuery = defineQuery([PointLight, WorldTransform]);
const spotLightQuery = defineQuery([SpotLight, WorldTransform]);
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
}
interface SpotLightCache {
  color: number;
  intensity: number;
  distance: number;
  decay: number;
  castShadow: number;
  angle: number;
  penumbra: number;
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
const MAX_SPOT_LIGHTS = 2;
/** Small on purpose — point-light shadows render 6 faces per caster per
 * frame, so this is the per-face size, not a single 2D map like the sun. */
const POINT_SHADOW_MAP_SIZE = 512;
const SPOT_SHADOW_MAP_SIZE = 1024;

function resolveShadowCenter(state: State): THREE.Vector3 {
  _shadowCenter.copy(SHADOW_CONFIG.FIXED_FRUSTUM_CENTER);

  const thirdPersonCams = thirdPersonCameraQuery(state.world);
  if (thirdPersonCams.length > 0) {
    const targetEid = ThirdPersonCamera.target[thirdPersonCams[0]];
    if (targetEid > 0 && state.hasComponent(targetEid, WorldTransform)) {
      _shadowCenter.set(
        WorldTransform.posX[targetEid],
        WorldTransform.posY[targetEid],
        WorldTransform.posZ[targetEid]
      );
    }
  }

  return _shadowCenter;
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

export const MeshInstanceSystem: System = {
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
      let mesh = getOrCreateMesh(context, MeshRenderer.shape[entity], unlit);
      if (!mesh) continue;

      if (MeshRenderer.visible[entity] !== 1) {
        hideInstance(mesh, entity, context);
        continue;
      }

      mesh = updateInstance(mesh, entity, context, state, unlit);
    }
  },
};

export const DistanceCullSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;

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
      const dist = Math.sqrt(dx * dx + dz * dz);

      const wasCulled = DistanceCull.culled[eid] === 1;
      const shouldCull = wasCulled
        ? dist >= maxDist * HYSTERESIS
        : dist > maxDist;

      if (shouldCull === wasCulled) continue;

      DistanceCull.culled[eid] = shouldCull ? 1 : 0;

      const gltfGroup = getGltfRootGroup(state, eid);
      if (gltfGroup) {
        gltfGroup.visible = !shouldCull;
      }

      if (state.hasComponent(eid, MeshRenderer)) {
        MeshRenderer.visible[eid] = shouldCull ? 0 : 1;
      }
    }
  },
};

interface CsmCache {
  cascades: number;
  maxFar: number;
  shadowMapSize: number;
  color: number;
}
const csmCacheByState = new WeakMap<State, CsmCache>();

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
  const shadowMapSize = DirectionalLight.shadowMapSize[entity];
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

  csm.updateFrustums();
  csm.update();
}

export const LightSyncSystem: System = {
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
        light.castShadow = true;
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

        const mapSize = DirectionalLight.shadowMapSize[entity];
        const bias = -0.0001;
        const normalBias = 0.02;
        // Blur radius for VSMShadowMap soft edges (texel-space, not world units).
        const shadowRadius = 1.5;
        const radius = SHADOW_CONFIG.CAMERA_RADIUS;
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

        // Shadow frustum follows the player — keep this tracking per-frame.
        const shadowCenter = resolveShadowCenter(state);
        _lightPos
          .copy(shadowCenter)
          .add(
            _lightOffset
              .copy(_lightDir)
              .multiplyScalar(DirectionalLight.distance[entity])
          );

        light.position.copy(_lightPos);
        light.target.position.copy(shadowCenter);
        light.target.updateMatrixWorld();
        shadowCamera.position.copy(_lightPos);
        shadowCamera.lookAt(shadowCenter);
        shadowCamera.updateMatrixWorld();
      } else {
        light.castShadow = false;
      }
    }
    // No entity currently wants CSM this frame — release it (it owns its own
    // internal directional lights, so leaving it around would keep shadowing
    // the scene with a sun no `DirectionalLight` entity asked for anymore).
    if (!csmActive) disposeCsm(state, scene);
  },
};

export const PointSpotLightSyncSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    const scene = getScene(state);
    if (!scene) return;

    const entityToPointLight = getPointLightMap(state);
    const entityToSpotLight = getSpotLightMap(state);

    for (const [eid, light] of entityToPointLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        light.dispose();
        entityToPointLight.delete(eid);
        const idx = context.lights.pointLights.indexOf(light);
        if (idx !== -1) context.lights.pointLights.splice(idx, 1);
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
    for (const eid of pointEntities) {
      let light = entityToPointLight.get(eid);
      if (!light) {
        if (context.lights.pointLights.length >= MAX_POINT_LIGHTS) {
          logger.warn(
            `PointLight limit (${MAX_POINT_LIGHTS}) reached — skipping entity ${eid}`
          );
          continue;
        }
        light = new THREE.PointLight();
        scene.add(light);
        entityToPointLight.set(eid, light);
        context.lights.pointLights.push(light);
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

      _lightPosition.set(
        WorldTransform.posX[eid],
        WorldTransform.posY[eid],
        WorldTransform.posZ[eid]
      );
      light.position.copy(_lightPosition);

      _lightQuaternion.set(
        WorldTransform.rotX[eid],
        WorldTransform.rotY[eid],
        WorldTransform.rotZ[eid],
        WorldTransform.rotW[eid]
      );
      light.quaternion.copy(_lightQuaternion);
    }

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

      _lightPosition.set(
        WorldTransform.posX[eid],
        WorldTransform.posY[eid],
        WorldTransform.posZ[eid]
      );
      light.position.copy(_lightPosition);

      _lightQuaternion.set(
        WorldTransform.rotX[eid],
        WorldTransform.rotY[eid],
        WorldTransform.rotZ[eid],
        WorldTransform.rotW[eid]
      );
      light.quaternion.copy(_lightQuaternion);
      light.target.position.copy(_lightPosition);
      light.target.quaternion.copy(_lightQuaternion);
      _lightForward.set(0, 0, -1).applyQuaternion(_lightQuaternion);
      light.target.position.copy(_lightPosition).add(_lightForward);
    }
  },
};

// NOTE: RendererSetupSystem was removed — its logic was identical to
// SceneRenderSystem and caused duplicate resize listeners / double-setup.
// All renderer creation is now handled by SceneRenderSystem.

export const CameraSyncSystem: System = {
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
};

export const SceneRenderSystem: System = {
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
};
