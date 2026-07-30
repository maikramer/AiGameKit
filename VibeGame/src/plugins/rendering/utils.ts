import type { State } from '../../core';
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { MainCamera } from './components';

const INITIAL_INSTANCES = 1000;
const MAX_TOTAL_INSTANCES = 50000;
const PERFORMANCE_WARNING_THRESHOLD = 10000;
const DEFAULT_COLOR = 0xffffff;

export const RendererShape = {
  BOX: 0,
  SPHERE: 1,
} as const;

export const CameraProjection = {
  PERSPECTIVE: 0,
  ORTHOGRAPHIC: 1,
} as const;

export const threeCameras = new Map<number, THREE.Camera>();
const canvasElements = new Map<number, HTMLCanvasElement>();

function getCanvasAspect(state: State): {
  width: number;
  height: number;
  aspect: number;
} {
  const context = stateToRenderingContext.get(state);
  const canvas = context?.canvas;

  let width = 16;
  let height = 9;

  if (canvas && canvas.clientWidth && canvas.clientHeight) {
    width = canvas.clientWidth;
    height = canvas.clientHeight;
  } else if (typeof window !== 'undefined') {
    width = window.innerWidth;
    height = window.innerHeight;
  }

  return { width, height, aspect: width / height };
}

function cameraClipPlanes(entity: number): { near: number; far: number } {
  const near = MainCamera.near[entity];
  const far = MainCamera.far[entity];
  const n = Number.isFinite(near) && near > 0 ? near : 0.1;
  const f = Number.isFinite(far) && far > n ? far : 1000;
  return { near: n, far: f };
}

function createThreeCamera(
  entity: number,
  state: State,
  projection: number,
  fov: number,
  orthoSize: number
): THREE.Camera {
  const { aspect } = getCanvasAspect(state);
  const { near, far } = cameraClipPlanes(entity);

  let camera: THREE.Camera;

  if (projection === CameraProjection.ORTHOGRAPHIC) {
    const halfHeight = orthoSize / 2;
    const halfWidth = halfHeight * aspect;
    camera = new THREE.OrthographicCamera(
      -halfWidth,
      halfWidth,
      halfHeight,
      -halfHeight,
      near,
      far
    );
  } else {
    camera = new THREE.PerspectiveCamera(fov, aspect, near, far);
  }

  threeCameras.set(entity, camera);
  // `threeCameras` is keyed by entity id and outlives the entity otherwise:
  // a destroyed camera kept its THREE.Camera alive, and once the id was
  // recycled the next camera entity inherited that stale object (including its
  // projection type, which `syncCameraSettings` cannot switch).
  state.onDestroy(entity, () => {
    threeCameras.delete(entity);
  });
  return camera;
}

function syncCameraSettings(
  camera: THREE.Camera,
  entity: number,
  state: State
): void {
  const { aspect } = getCanvasAspect(state);
  const { near, far } = cameraClipPlanes(entity);

  if (camera instanceof THREE.OrthographicCamera) {
    const orthoSize = MainCamera.orthoSize[entity];
    const halfHeight = orthoSize / 2;
    const halfWidth = halfHeight * aspect;
    let dirty = false;

    if (camera.top !== halfHeight || camera.right !== halfWidth) {
      camera.left = -halfWidth;
      camera.right = halfWidth;
      camera.top = halfHeight;
      camera.bottom = -halfHeight;
      dirty = true;
    }
    if (camera.near !== near || camera.far !== far) {
      camera.near = near;
      camera.far = far;
      dirty = true;
    }
    if (dirty) camera.updateProjectionMatrix();
  } else if (camera instanceof THREE.PerspectiveCamera) {
    const fov = MainCamera.fov[entity];
    let dirty = false;
    if (camera.fov !== fov) {
      camera.fov = fov;
      dirty = true;
    }
    if (camera.near !== near || camera.far !== far) {
      camera.near = near;
      camera.far = far;
      dirty = true;
    }
    if (dirty) camera.updateProjectionMatrix();
  }
}

export { createThreeCamera, getCanvasAspect, syncCameraSettings };

const instanceFreeLists = new WeakMap<THREE.InstancedMesh, number[]>();

/**
 * Pools whose instance bounds need a recompute before the next frustum-cull
 * check. Marked dirty when an instance is added or removed (the events that
 * actually change the world-space extent of the pool). Moves are not marked —
 * recomputing on every move would dwarf the savings; instead a recompute is
 * throttled in MeshInstanceSystem (a few times per second). When dirty, the
 * pool's frustum culling is temporarily disabled so no pool "vanishes" between
 * the dirty mark and the recompute (the safe default is "always draw").
 */
const boundsDirtyPools = new WeakSet<THREE.InstancedMesh>();

export function markInstanceBoundsDirty(mesh: THREE.InstancedMesh): void {
  boundsDirtyPools.add(mesh);
  // Until the bounds are recomputed we can't trust the sphere, so opt out of
  // frustum culling for this pool (draw everything). The recompute flips it
  // back on. This matches the historical safe behaviour.
  mesh.frustumCulled = false;
}

export function releaseInstanceSlot(
  mesh: THREE.InstancedMesh,
  index: number
): void {
  const freeList = instanceFreeLists.get(mesh);
  if (freeList) {
    freeList.push(index);
  }
  // A removed instance may shrink the pool's extent — recompute on next tick.
  markInstanceBoundsDirty(mesh);
}

export function findAvailableInstanceSlot(
  mesh: THREE.InstancedMesh,
  _matrix: THREE.Matrix4
): number | null {
  const freeList = instanceFreeLists.get(mesh);
  if (freeList && freeList.length > 0) {
    // A newly-activated slot extends the pool's extent — recompute on next tick.
    markInstanceBoundsDirty(mesh);
    return freeList.pop()!;
  }
  return null;
}

/**
 * Recompute the bounding sphere of an instanced pool from its live instance
 * matrices and re-enable frustum culling. Called periodically (throttled) by
 * the render system, NOT per frame — `computeBoundingSphere` is O(instances).
 * Returns true if the bounds were actually recomputed this call.
 */
export function recomputeInstanceBounds(mesh: THREE.InstancedMesh): boolean {
  mesh.computeBoundingSphere();
  mesh.computeBoundingBox();
  mesh.frustumCulled = true;
  boundsDirtyPools.delete(mesh);
  return true;
}

/** True when the pool's bounds are stale and should be recomputed. */
export function instanceBoundsDirty(mesh: THREE.InstancedMesh): boolean {
  return boundsDirtyPools.has(mesh);
}

export function initializeInstancedMesh(
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  count: number = INITIAL_INSTANCES
): THREE.InstancedMesh {
  const mesh = new THREE.InstancedMesh(geometry, material, count);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  // Instances are scattered across the world but the mesh's bounding sphere is
  // the unit geometry's (centred at the origin) and is never recomputed as
  // instances are added/moved. With frustum culling on, the whole pool would
  // vanish whenever the world origin left the view ("only visible from certain
  // angles"). Disable per-mesh culling — it's a single instanced draw call and
  // these pools (boxes/spheres) hold few instances.
  mesh.frustumCulled = false;

  const zeroMatrix = new THREE.Matrix4();
  zeroMatrix.makeScale(0, 0, 0);
  const defaultColor = new THREE.Color(DEFAULT_COLOR);

  for (let i = 0; i < count; i++) {
    mesh.setMatrixAt(i, zeroMatrix);
    mesh.setColorAt(i, defaultColor);
  }

  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) {
    mesh.instanceColor.needsUpdate = true;
  }

  const freeList: number[] = [];
  for (let i = count - 1; i >= 0; i--) {
    freeList.push(i);
  }
  instanceFreeLists.set(mesh, freeList);

  return mesh;
}

export function resizeInstancedMesh(
  oldMesh: THREE.InstancedMesh,
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  scene: THREE.Scene
): THREE.InstancedMesh {
  const oldCount = oldMesh.count;
  const newCount = oldCount * 2;

  const newMesh = initializeInstancedMesh(geometry, material, newCount);

  const matrix = new THREE.Matrix4();
  const color = new THREE.Color();

  for (let i = 0; i < oldCount; i++) {
    oldMesh.getMatrixAt(i, matrix);
    newMesh.setMatrixAt(i, matrix);

    if (oldMesh.instanceColor) {
      oldMesh.getColorAt(i, color);
      newMesh.setColorAt(i, color);
    }
  }

  newMesh.instanceMatrix.needsUpdate = true;
  if (newMesh.instanceColor) {
    newMesh.instanceColor.needsUpdate = true;
  }

  const freeList = instanceFreeLists.get(newMesh);
  if (freeList) {
    freeList.length = 0;
    for (let i = newCount - 1; i >= oldCount; i--) {
      freeList.push(i);
    }
  }

  scene.remove(oldMesh);
  oldMesh.dispose();
  scene.add(newMesh);

  return newMesh;
}

/**
 * Per-entity instance slot plus a cache of the transform inputs and color last
 * written to the GPU buffer. The render loop compares against this cache so it
 * only rewrites `setMatrixAt`/`setColorAt` and flags `needsUpdate` when an
 * instance actually changed — static instances (terrain props, vegetation)
 * then cost zero GPU buffer uploads per frame instead of a full re-upload.
 */
export interface InstanceInfo {
  poolId: number;
  instanceId: number;
  unlit: boolean;
  /** False until the slot has been written once (or after it was hidden). */
  initialized: boolean;
  /** Last composed transform inputs (position, rotation quat, final scale). */
  px: number;
  py: number;
  pz: number;
  rx: number;
  ry: number;
  rz: number;
  rw: number;
  sx: number;
  sy: number;
  sz: number;
  /** Last color written to the instance color buffer (-1 = never written). */
  color: number;
}

export interface RenderingContext {
  scene: THREE.Scene;
  meshPools: Map<number, THREE.InstancedMesh>;
  unlitMeshPools: Map<number, THREE.InstancedMesh>;
  geometries: Map<number, THREE.BufferGeometry>;
  material: THREE.MeshStandardMaterial;
  unlitMaterial: THREE.MeshBasicMaterial;
  entityInstances: Map<number, InstanceInfo>;
  lights: {
    ambient: THREE.HemisphereLight;
    directional: THREE.DirectionalLight;
    pointLights: THREE.PointLight[];
    spotLights: THREE.SpotLight[];
  };
  renderer?: THREE.WebGLRenderer;
  postProcessing?: {
    render(delta?: number): void;
    dispose(): void;
    setSize(width: number, height: number): void;
  } | null;
  canvas?: HTMLCanvasElement;
  /** Window 'resize' listener, kept so dispose() can remove it (avoids
   * accumulating dead listeners across hot-reload / multi-runtime teardown). */
  resizeHandler?: () => void;
  totalInstanceCount: number;
  hasShownPerformanceWarning: boolean;
  /** Populated asynchronously by `detectGpuTier` once the renderer exists. */
  gpuTier?: import('detect-gpu').TierResult | null;
  /** Owns cascaded shadow maps when `directional-light="csm: 1"` is set —
   * see `LightSyncSystem` (systems.ts). Mesh creation sites call
   * `setupCsmMaterial`/`setupCsmMaterials` below (alongside setting
   * `receiveShadow`) so the material picks up the CSM shader patch. */
  csm?: import('three/examples/jsm/csm/CSM.js').CSM | null;
}

export function getCsm(
  state: State
): import('three/examples/jsm/csm/CSM.js').CSM | null {
  return stateToRenderingContext.get(state)?.csm ?? null;
}

/** Materials patched via {@link setupCsmMaterial} so far, so re-loading the
 * same shared/instanced material doesn't re-run `csm.setupMaterial` (cheap
 * but not idempotent-free — it reassigns `onBeforeCompile`/`defines` each
 * call). Cleared per-material when CSM is disposed/recreated (see
 * `disposeCsm` in systems.ts), since a stale entry would skip re-patching
 * against the new CSM instance. */
const csmPatchedMaterials = new WeakSet<THREE.Material>();

export function isCsmMaterialPatched(material: THREE.Material): boolean {
  return csmPatchedMaterials.has(material);
}

export function clearCsmMaterialPatch(material: THREE.Material): void {
  csmPatchedMaterials.delete(material);
}

/**
 * Patches one material so its lighting is gated per-cascade instead of
 * getting the full contribution of *every* cascade's internal directional
 * light (CSM has no distance falloff to stop that on its own — an unpatched
 * material would render far too bright with more than one cascade active).
 * No-op when CSM isn't active for `state`, the material was already patched,
 * or it's a `CustomShaderMaterial` (water/terrain): that library also owns
 * `onBeforeCompile` for its own shader injection, and CSM's setupMaterial()
 * would clobber it — breaking the custom shader, not just its shadows. Its
 * `__csm` internal field (an unrelated naming coincidence with *this*
 * cascaded-shadow-maps CSM) is the only reliable way to tell it apart from a
 * real `MeshStandardMaterial`.
 */
export function setupCsmMaterial(
  state: State,
  material: THREE.Material | null | undefined
): void {
  if (!material) return;
  const csm = getCsm(state);
  if (!csm || csmPatchedMaterials.has(material)) return;
  if ('__csm' in material) return;
  const standard = material as THREE.MeshStandardMaterial;
  if (standard.isMeshStandardMaterial !== true) return;
  csm.setupMaterial(standard);
  csmPatchedMaterials.add(material);
}

/** {@link setupCsmMaterial} for every mesh material under `root` — call once
 * right after adding a newly loaded/created subtree to the scene. */
export function setupCsmMaterials(state: State, root: THREE.Object3D): void {
  if (!getCsm(state)) return;
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const materials = Array.isArray(mesh.material)
      ? mesh.material
      : [mesh.material];
    for (const mat of materials) setupCsmMaterial(state, mat);
  });
}

const stateToRenderingContext = new WeakMap<State, RenderingContext>();

export function createGeometries(): Map<number, THREE.BufferGeometry> {
  const geometries = new Map<number, THREE.BufferGeometry>();
  geometries.set(RendererShape.BOX, new THREE.BoxGeometry());
  geometries.set(RendererShape.SPHERE, new THREE.SphereGeometry(1));
  return geometries;
}

export function initializeContext(): RenderingContext {
  const scene = new THREE.Scene();

  const ambient = new THREE.HemisphereLight(0xb1e1ff, 0xb97a20, 1.0);
  scene.add(ambient);

  const directional = new THREE.DirectionalLight(0xffffff, 1.8);
  // Shadows stay off until LightSync sees cast-shadow:1 — avoids a 0×0 /
  // unused shadow FBO during boot/warmup when the scene opts out.
  directional.castShadow = false;
  directional.shadow.mapSize.width = 2048;
  directional.shadow.mapSize.height = 2048;
  scene.add(directional);
  scene.add(directional.target);

  return {
    scene,
    meshPools: new Map(),
    unlitMeshPools: new Map(),
    geometries: createGeometries(),
    material: new THREE.MeshStandardMaterial({
      metalness: 0.0,
      roughness: 1.0,
    }),
    unlitMaterial: new THREE.MeshBasicMaterial(),
    entityInstances: new Map(),
    lights: {
      ambient: ambient,
      directional: directional,
      pointLights: [],
      spotLights: [],
    },
    totalInstanceCount: 0,
    hasShownPerformanceWarning: false,
  };
}

export function getRenderingContext(state: State): RenderingContext {
  let context = stateToRenderingContext.get(state);
  if (!context) {
    context = initializeContext();
    stateToRenderingContext.set(state, context);
  }
  return context;
}

export function getScene(state: State): THREE.Scene | null {
  const context = stateToRenderingContext.get(state);
  return context?.scene || null;
}

/** GPU tier resolved by `detectGpuTier`, or `null` before it resolves / on SSR. */
export function getGpuTier(
  state: State
): import('detect-gpu').TierResult | null {
  return stateToRenderingContext.get(state)?.gpuTier ?? null;
}

/**
 * Also keyed by renderer instance (not just ECS `State`): effect factories in
 * `postprocessing/builtin-effects.ts` only receive the `WebGLRenderer`, not
 * the `State`, so they consult this map instead of `getGpuTier`.
 */
const gpuTierByRenderer = new WeakMap<
  THREE.WebGLRenderer,
  import('detect-gpu').TierResult | null
>();

export function getGpuTierForRenderer(
  renderer: THREE.WebGLRenderer
): import('detect-gpu').TierResult | null {
  return gpuTierByRenderer.get(renderer) ?? null;
}

/**
 * Benchmarks a GPU tier (0-3) via `detect-gpu` and caches it on the rendering
 * context for quality-sensitive subsystems (postprocessing, particle counts,
 * terrain resolution) to consult. Fetches benchmark data from unpkg by
 * default — failures (offline, blocked CDN) are swallowed and leave the tier
 * `null`, so callers must treat "unknown" as "use the current defaults".
 */
export async function detectGpuTier(
  state: State,
  renderer: THREE.WebGLRenderer,
  glContext?: WebGL2RenderingContext
): Promise<void> {
  const context = getRenderingContext(state);
  if (context.gpuTier !== undefined) return;
  try {
    const { getGPUTier } = await import('detect-gpu');
    context.gpuTier = await getGPUTier(glContext ? { glContext } : {});
  } catch {
    context.gpuTier = null;
  }
  gpuTierByRenderer.set(renderer, context.gpuTier);
}

export function setCanvasElement(
  entity: number,
  canvas: HTMLCanvasElement
): void {
  canvasElements.set(entity, canvas);
}

export function getCanvasElement(
  entity: number
): HTMLCanvasElement | undefined {
  return canvasElements.get(entity);
}

export function deleteCanvasElement(entity: number): void {
  canvasElements.delete(entity);
}

export function setRenderingCanvas(
  state: State,
  canvas: HTMLCanvasElement
): void {
  const context = getRenderingContext(state);
  context.canvas = canvas;
}

export async function createRenderer(
  canvas: HTMLCanvasElement,
  clearColor: number,
  options?: { antialias?: boolean }
): Promise<THREE.WebGLRenderer> {
  // MSAA on the default framebuffer is wasted GPU when the post-processing
  // composer is active: it renders into HalfFloat FBOs and runs its own AA pass
  // (SMAA preset HIGH by default, see PostprocessingPlugin config `aa: 2`).
  // Callers that detect a `postprocessing="..."` attribute in the scene pass
  // `antialias: false` here so the multisampled default buffer is never
  // allocated. Defaults to `true` so non-composer scenes still get hardware AA.
  const antialias = options?.antialias ?? true;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias,
    powerPreference: 'high-performance',
    alpha: false,
    premultipliedAlpha: false,
    preserveDrawingBuffer: false,
  });

  const width = canvas.clientWidth || window.innerWidth;
  const height = canvas.clientHeight || window.innerHeight;
  const pixelRatio = Math.min(
    window.devicePixelRatio,
    /Mobi|Android/i.test(navigator.userAgent) ? 1.25 : 1.5
  );
  renderer.setPixelRatio(pixelRatio);
  renderer.setSize(width, height, false);
  // Per-material clipping planes (e.g. destructible tree-fall trunk split).
  renderer.localClippingEnabled = true;

  renderer.shadowMap.enabled = true;
  // VSMShadowMap gives the softest edges but only supports directional/spot
  // shadow maps — WebGL logs a warning per frame per light and silently
  // fails for PointLight cube shadows (torches/lanterns). PCFShadowMap works
  // uniformly across every light type; `shadow.radius` still gives a (less
  // precise) soft-edge multi-tap blur under PCF, so directional shadows stay
  // reasonably soft despite the switch. PCFSoftShadowMap is deprecated
  // upstream (silently falls back to this same PCFShadowMap anyway).
  renderer.shadowMap.type = THREE.PCFShadowMap;

  if (clearColor !== 0) {
    renderer.setClearColor(clearColor);
  }

  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.toneMappingExposure = 1;

  return renderer;
}

/**
 * Give the scene an image-based lighting environment. Without it, glTF PBR
 * materials with non-zero metalness (very common in exported characters) have
 * nothing to reflect and render black. A prefiltered neutral room is the
 * standard fix and lights every metallic/rough surface plausibly.
 */
export function applyNeutralEnvironment(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene
): void {
  const pmrem = new THREE.PMREMGenerator(renderer);
  try {
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    // The IBL is there to give PBR materials something to reflect — the scene
    // is already lit by the hemisphere + directional lights, so keep it subtle
    // or everything washes out.
    scene.environmentIntensity = 0.45;
  } finally {
    pmrem.dispose();
  }
}

/**
 * Resolve the effective pixel ratio given the renderer's cap and the active
 * Adaptive Quality tier (if any). When adaptive quality is inactive or at the
 * Max tier, this returns `cap` unchanged. At higher tiers it scales `cap` down
 * by the tier's preset factor, clamped to the user-configured floor.
 *
 * Kept here (next to the resize handler) so both the initial `createRenderer`
 * path and every resize consult the same source of truth — a resize must never
 * silently reset an active downscale.
 */
function computeAdaptivePixelRatio(state: State, cap: number): number {
  // Look up the Adaptive Quality component directly (avoids a hard import cycle
  // into the adaptive-quality plugin). If the plugin isn't registered, behave
  // as if the tier is Max (no scaling).
  const aq = state.getComponent('adaptive-quality') as
    | {
        enabled: Uint8Array;
        minPixelRatio: Float32Array;
        maxPixelRatio: Float32Array;
        currentTier: Uint8Array;
      }
    | undefined;
  if (!aq) return cap;
  // Find the active AdaptiveQuality entity (at most one per scene).
  let tier = 0;
  let floor = 0.5;
  let ceiling = cap;
  let found = false;
  for (let i = 0; i < aq.enabled.length; i++) {
    if (aq.enabled[i]) {
      tier = aq.currentTier[i];
      floor = aq.minPixelRatio[i] || 0.5;
      ceiling = aq.maxPixelRatio[i] || cap;
      found = true;
      break;
    }
  }
  if (!found) return cap;
  // Tier scale table mirrors TIER_PRESETS.pixelRatioScale in adaptive-quality.
  const scale = [1.0, 1.0, 0.85, 0.75][tier] ?? 1.0;
  const effectiveCap = Math.min(cap, ceiling);
  return Math.max(floor, Math.min(effectiveCap, effectiveCap * scale));
}

export function handleWindowResize(
  state: State,
  renderer: THREE.WebGLRenderer
): void {
  const context = getRenderingContext(state);
  const canvas = context.canvas;

  const width = canvas?.clientWidth || window.innerWidth;
  const height = canvas?.clientHeight || window.innerHeight;
  const aspect = width / height;

  // Pixel-ratio cap (the renderer's ceiling). The Adaptive Quality scaler may
  // reduce the effective ratio below this cap; honor the current applied ratio
  // so a window resize doesn't clobber an active downscale. If adaptive
  // quality is inactive, this equals the cap (no change in behavior).
  const cap = Math.min(
    window.devicePixelRatio,
    /Mobi|Android/i.test(navigator.userAgent) ? 1.25 : 1.5
  );
  const appliedRatio = computeAdaptivePixelRatio(state, cap);
  renderer.setPixelRatio(appliedRatio);
  renderer.setSize(width, height, false);

  if (
    context.postProcessing &&
    typeof context.postProcessing.setSize === 'function'
  ) {
    context.postProcessing.setSize(width, height);
  }

  for (const [, camera] of threeCameras) {
    if (camera instanceof THREE.PerspectiveCamera) {
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
    } else if (camera instanceof THREE.OrthographicCamera) {
      const halfHeight = (camera.top - camera.bottom) / 2;
      const halfWidth = halfHeight * aspect;
      camera.left = -halfWidth;
      camera.right = halfWidth;
      camera.updateProjectionMatrix();
    }
  }
}

export const SHADOW_CONFIG = {
  LIGHT_DIRECTION: new THREE.Vector3(5, 10, 2).normalize(),
  LIGHT_DISTANCE: 25,
  /** Raio ortográfico da câmara de sombras (metade da largura do frustum). Maior = mais cobertura ao redor do alvo.
   * A frustum SEGUE o alvo da câmara (ver `resolveShadowCenter`), por isso só
   * precisa de cobrir a distância de visão à volta do jogador — não o mapa todo.
   * 140 (=280 m) era exagerado: desperdiça resolução do shadow map e mete
   * centenas de casters distantes no shadow pass. 90 (=180 m) cobria a vista
   * com folga mas a 2048px isso é só ~11 texels/m — sombra de personagem/árvore
   * (~1m) vira ruído invisível. 32 (=64 m) dá ~32 texels/m — sombra nítida perto
   * do jogador — à custa de objetos bem distantes perderem sombra mais cedo. */
  CAMERA_RADIUS: 32,
  NEAR_PLANE: 0.5,
  FAR_PLANE: 250,
  /**
   * Centro da frustum ortográfica do shadow map em espaço de mundo (Y≈altura média do chão).
   * Centrar na câmara/jogador faz o limite do mapa “seguir” o ecrã (parece um quadrado que anda);
   * âncora fixa cobre o mapa centrado na origem (ex.: terrain pos 0,0,0).
   */
  FIXED_FRUSTUM_CENTER: new THREE.Vector3(0, 0, 0),
} as const;

export { MAX_TOTAL_INSTANCES, PERFORMANCE_WARNING_THRESHOLD };
