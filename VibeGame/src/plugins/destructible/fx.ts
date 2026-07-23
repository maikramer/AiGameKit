import * as THREE from 'three';
import CustomShaderMaterial from 'three-custom-shader-material/vanilla';
import { defineSystem, type State, type System } from '../../core';
import { getScene, setupCsmMaterial } from '../rendering';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import { spawnParticleBurst } from '../particles/utils';

/**
 * Visual feedback for destructible props beyond the particle burst:
 *
 * - `applyCrackAmount` — procedural crack overlay that darkens/deepens with
 *   each hit (rocks). Built with `three-custom-shader-material` on per-entity
 *   cloned materials so other props sharing the master GLB stay pristine, and
 *   the overlay survives Three.js upgrades that reshuffle internal chunks.
 * - `startHitShake` — short decaying wobble of the visual (trees).
 * - `startTreeFall` — the trunk is cut at `cutHeight`: a stump stays behind
 *   while the top half tips over away from the player (clipping-plane split),
 *   raises dust on impact, then both halves fade out.
 * - `startRockShatter` — the rock breaks into angular debris chunks that fly
 *   ballistically, tumble, land and fade.
 *
 * All effects are cosmetic scene-graph clones: geometries stay shared with the
 * GLB master cache (never disposed here); only per-effect cloned materials and
 * locally created debris geometry are disposed when an effect ends.
 */

interface ShakeFx {
  targets: { obj: THREE.Object3D; rx: number; rz: number }[];
  elapsed: number;
  duration: number;
  amp: number;
}

interface FallFx {
  pivot: THREE.Object3D;
  stump: THREE.Object3D;
  /** Null when using pre-split Stump/Top meshes (no shader clipping). */
  topPlane: THREE.Plane | null;
  materials: THREE.Material[];
  axis: THREE.Vector3;
  dirX: number;
  dirZ: number;
  cutPoint: THREE.Vector3;
  topLength: number;
  groundY: number;
  elapsed: number;
  impactDone: boolean;
}

interface ShatterPiece {
  mesh: THREE.Mesh;
  vel: THREE.Vector3;
  angVel: THREE.Vector3;
  landed: boolean;
}

interface ShatterFx {
  pieces: ShatterPiece[];
  material: THREE.Material;
  geometry: THREE.BufferGeometry;
  groundY: number;
  elapsed: number;
}

interface CrackFx {
  uniform: { value: number };
  materials: THREE.Material[];
  style: number;
}

interface SplitHalf {
  obj: THREE.Object3D;
  pivot: THREE.Object3D;
  plane: THREE.Plane;
  /** Signed base normal (per-half, set once at creation); the plane normal is
   * this rotated by the pivot quaternion each frame. */
  baseSignedN: THREE.Vector3;
  materials: THREE.Material[];
  sign: number; // +1 / -1 rotation direction
}

interface SplitFx {
  halves: SplitHalf[];
  axis: THREE.Vector3;
  cutPoint: THREE.Vector3;
  groundY: number;
  halfHeight: number;
  elapsed: number;
  impactDone: boolean;
}

interface FxStore {
  shakes: Map<number, ShakeFx>;
  falls: FallFx[];
  shatters: ShatterFx[];
  splits: SplitFx[];
  cracks: Map<number, CrackFx>;
}

const fxByState = new WeakMap<State, FxStore>();

function getFxStore(state: State): FxStore {
  let store = fxByState.get(state);
  if (!store) {
    store = {
      shakes: new Map(),
      falls: [],
      shatters: [],
      splits: [],
      cracks: new Map(),
    };
    fxByState.set(state, store);
  }
  return store;
}

/** The prop's visual GLB root: on the entity itself or a child GLTFLoader. */
function findVisualGroup(
  state: State,
  entity: number
): THREE.Group | undefined {
  const own = getGltfRootGroup(state, entity);
  if (own) return own;
  for (const child of state.getDescendants(entity)) {
    const group = getGltfRootGroup(state, child);
    if (group) return group;
  }
  return undefined;
}

// --- Crack overlay ---------------------------------------------------------

/**
 * Crack overlay styles. `customProgramCacheKey` MUST embed the style so THREE's
 * program cache doesn't hand back the wrong compiled shader across styles.
 */
export const CRACK_STYLE_VORONOI = 0; // jagged cell-edge lines (rocks)
export const CRACK_STYLE_VERTICAL = 1; // long vertical grain splits (wood)

const CRACK_VERTEX_SHADER = `
varying vec3 vCrackPos;
void main() {
  vCrackPos = position;
}
`;

const CRACK_GLSL_COMMON = `
uniform float uCrackAmount;
varying vec3 vCrackPos;
vec2 crackHash2(vec2 p) {
  return fract(sin(vec2(
    dot(p, vec2(127.1, 311.7)),
    dot(p, vec2(269.5, 183.3))
  )) * 43758.5453);
}
// Voronoi F2-F1 border distance: zero along cell edges = crack lines.
float crackEdgeDist(vec2 p) {
  vec2 n = floor(p);
  vec2 f = fract(p);
  float f1 = 8.0;
  float f2 = 8.0;
  for (int j = -1; j <= 1; j++) {
    for (int i = -1; i <= 1; i++) {
      vec2 g = vec2(float(i), float(j));
      float d = length(g + crackHash2(n + g) - f);
      if (d < f1) { f2 = f1; f1 = d; } else if (d < f2) { f2 = d; }
    }
  }
  return f2 - f1;
}
`;

const CRACK_GLSL_APPLY = `
void main() {
  if (uCrackAmount > 0.001) {
    vec2 cp = vCrackPos.xz * 3.5 + vCrackPos.y * 2.1;
    float edge = crackEdgeDist(cp);
    float width = 0.04 + 0.12 * uCrackAmount;
    float crack = 1.0 - smoothstep(width * 0.25, width, edge);
    csm_DiffuseColor.rgb *= 1.0 - crack * (0.4 + 0.45 * uCrackAmount);
  }
}
`;

// Vertical grain-split style for wood: a few long splits running up the trunk,
// offset by a low-freq hash in X so the cracks aren't perfectly periodic.
const CRACK_VERT_APPLY = `
void main() {
  if (uCrackAmount > 0.001) {
    // Base position along the trunk circumference; small high-freq jitter so
    // each split wobbles instead of reading as a perfectly straight line.
    float xc = vCrackPos.x + vCrackPos.z * 0.35;
    float jitter = (crackHash2(vec2(floor(vCrackPos.y * 1.8), 7.0)).x - 0.5) * 0.18;
    float p = xc * 5.2 + jitter;
    // Distance to the nearest periodic seam: 0 on the line, 0.5 between.
    float nearest = abs(fract(p + 0.5) - 0.5);
    // Fade the splits toward the top/bottom so they look like stresses, not a
    // full-length saw cut.
    float yFade = 1.0 - smoothstep(0.42, 0.5, abs(vCrackPos.y - 0.0));
    float width = 0.022 + 0.10 * uCrackAmount;
    float split = 1.0 - smoothstep(width * 0.3, width, nearest);
    split *= yFade;
    csm_DiffuseColor.rgb *= 1.0 - split * (0.42 + 0.5 * uCrackAmount);
  }
}
`;

/**
 * Clone `source` with a crack overlay shader for `style`. Exported for tests
 * that verify the program-cache key varies by style (otherwise the cached GL
 * program would be reused across voronoi/vertical props).
 */
export function makeCrackMaterial(
  source: THREE.Material,
  uniform: { value: number },
  style: number
): THREE.Material {
  const fragmentShader =
    CRACK_GLSL_COMMON +
    (style === CRACK_STYLE_VERTICAL ? CRACK_VERT_APPLY : CRACK_GLSL_APPLY);

  return new CustomShaderMaterial({
    baseMaterial: source.clone(),
    vertexShader: CRACK_VERTEX_SHADER,
    fragmentShader,
    uniforms: { uCrackAmount: uniform },
    cacheKey: () => 'destructible-crack-' + style,
  }) as unknown as THREE.Material;
}

/**
 * Set the crack overlay intensity (0..1) on the prop's visual. Materials are
 * cloned per entity on first use (picking the crack `style` once) and disposed
 * when the entity is destroyed. Later calls with a different style are ignored
 * — the material program is fixed for the entity's lifetime.
 */
export function applyCrackAmount(
  state: State,
  entity: number,
  amount: number,
  style: number = CRACK_STYLE_VORONOI
): boolean {
  const group = findVisualGroup(state, entity);
  if (!group) return false;

  const store = getFxStore(state);
  let crack = store.cracks.get(entity);
  if (!crack) {
    const uniform = { value: 0 };
    const materials: THREE.Material[] = [];
    group.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (mesh.isMesh !== true) return;
      const mats = Array.isArray(mesh.material)
        ? mesh.material
        : [mesh.material];
      const cloned = mats.map((m) => {
        const c = makeCrackMaterial(m, uniform, style);
        materials.push(c);
        return c;
      });
      mesh.material = Array.isArray(mesh.material) ? cloned : cloned[0];
    });
    crack = { uniform, materials, style };
    store.cracks.set(entity, crack);
    state.onDestroy(entity, () => {
      const rec = store.cracks.get(entity);
      if (!rec) return;
      for (const m of rec.materials) m.dispose();
      store.cracks.delete(entity);
    });
  }
  crack.uniform.value = Math.min(Math.max(amount, 0), 1);
  return true;
}

// --- Hit shake ---------------------------------------------------------------

/** Short decaying wobble on the prop's visual (feedback for a landed blow). */
export function startHitShake(state: State, entity: number): boolean {
  const group = findVisualGroup(state, entity);
  if (!group) return false;
  const store = getFxStore(state);
  const existing = store.shakes.get(entity);
  if (existing) {
    existing.elapsed = 0;
    return true;
  }
  // Wobble the group's children, not the group itself: the transform-sync
  // system rewrites the root every frame from WorldTransform.
  const targets = group.children.map((obj) => ({
    obj,
    rx: obj.rotation.x,
    rz: obj.rotation.z,
  }));
  if (targets.length === 0) return false;
  store.shakes.set(entity, {
    targets,
    elapsed: 0,
    duration: 0.4,
    amp: 0.045,
  });
  return true;
}

// --- Tree fall ---------------------------------------------------------------

const FALL_DURATION = 1.15;
const FALL_MAX_ANGLE = Math.PI * 0.47;
const FALL_HOLD = 1.1;
const FALL_FADE = 0.7;

/** Clone a copy's materials with a shared clipping plane; returns the clones. */
function applyClipPlane(
  root: THREE.Object3D,
  plane: THREE.Plane
): THREE.Material[] {
  const out: THREE.Material[] = [];
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const cloned = mats.map((m) => {
      const c = m.clone();
      c.clippingPlanes = [plane];
      c.clipShadows = true;
      out.push(c);
      return c;
    });
    mesh.material = Array.isArray(mesh.material) ? cloned : cloned[0];
  });
  return out;
}

function cloneVisualAtWorld(source: THREE.Object3D): THREE.Object3D {
  source.updateWorldMatrix(true, false);
  const copy = source.clone(true);
  source.matrixWorld.decompose(copy.position, copy.quaternion, copy.scale);
  return copy;
}

function clonePartAtWorld(part: THREE.Object3D): THREE.Object3D {
  part.updateWorldMatrix(true, true);
  const copy = part.clone(true);
  part.matrixWorld.decompose(copy.position, copy.quaternion, copy.scale);
  return copy;
}

function cloneMaterialsForFade(root: THREE.Object3D): THREE.Material[] {
  const out: THREE.Material[] = [];
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const cloned = mats.map((m) => {
      const c = m.clone();
      out.push(c);
      return c;
    });
    mesh.material = Array.isArray(mesh.material) ? cloned : cloned[0];
  });
  return out;
}

function nameLooksLikeStump(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n === 'stump' || n.endsWith('_stump') || /(^|[^a-z])stump([^a-z]|$)/.test(n)
  );
}

function nameLooksLikeTop(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n === 'top' ||
    n.endsWith('_top') ||
    n === 'canopy' ||
    n.includes('canopy') ||
    n.includes('crown') ||
    /(^|[^a-z])top([^a-z]|$)/.test(n)
  );
}

/**
 * Locate pre-baked stump/top meshes from `text3d split-at-height`
 * (names `Stump` / `Top`, also `*_stump`, canopy/crown).
 */
export function findTreeSplitParts(
  root: THREE.Object3D
): { stump: THREE.Object3D; top: THREE.Object3D } | null {
  let stump: THREE.Object3D | undefined;
  let top: THREE.Object3D | undefined;
  root.traverse((obj) => {
    if (obj === root) return;
    if (!stump && nameLooksLikeStump(obj.name)) stump = obj;
    if (!top && nameLooksLikeTop(obj.name)) top = obj;
  });
  if (stump && top && stump !== top) return { stump, top };
  return null;
}

/** Build stump/top clones for a tree fall (mesh split or clipping fallback). */
export function prepareTreeFallHalves(
  source: THREE.Object3D,
  cutHeight: number
): {
  stump: THREE.Object3D;
  top: THREE.Object3D;
  cutPoint: THREE.Vector3;
  groundY: number;
  topLength: number;
  topPlane: THREE.Plane | null;
  materials: THREE.Material[];
} | null {
  const parts = findTreeSplitParts(source);
  if (parts) {
    const stump = clonePartAtWorld(parts.stump);
    const top = clonePartAtWorld(parts.top);
    const materials = [
      ...cloneMaterialsForFade(stump),
      ...cloneMaterialsForFade(top),
    ];
    const stumpBox = new THREE.Box3().setFromObject(stump);
    const topBox = new THREE.Box3().setFromObject(top);
    if (stumpBox.isEmpty() || topBox.isEmpty()) return null;
    const groundY = stumpBox.min.y;
    const cutY = stumpBox.max.y;
    const cutPoint = new THREE.Vector3(
      (stumpBox.min.x + stumpBox.max.x) / 2,
      cutY,
      (stumpBox.min.z + stumpBox.max.z) / 2
    );
    return {
      stump,
      top,
      cutPoint,
      groundY,
      topLength: Math.max(topBox.max.y - cutY, 0.5),
      topPlane: null,
      materials,
    };
  }

  const top = cloneVisualAtWorld(source);
  const stump = cloneVisualAtWorld(source);
  const box = new THREE.Box3().setFromObject(top);
  if (box.isEmpty()) return null;
  const height = box.max.y - box.min.y;
  const groundY = box.min.y;
  const cutY = groundY + Math.min(Math.max(cutHeight, 0.2), height * 0.4);
  const cutPoint = new THREE.Vector3(
    (box.min.x + box.max.x) / 2,
    cutY,
    (box.min.z + box.max.z) / 2
  );
  const stumpPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), cutY);
  applyClipPlane(stump, stumpPlane);
  const topPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -cutY);
  const topMats = applyClipPlane(top, topPlane);
  const stumpMats: THREE.Material[] = [];
  stump.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    stumpMats.push(...mats);
  });
  return {
    stump,
    top,
    cutPoint,
    groundY,
    topLength: Math.max(height - (cutY - groundY), 0.5),
    topPlane,
    materials: [...topMats, ...stumpMats],
  };
}

/**
 * Felled-tree effect: split the visual at `cutHeight`, leave a stump and tip
 * the top half over in direction (dirX, dirZ), with dust on impact and a
 * fade-out. Prefers pre-split `Stump`/`Top` meshes from the GLB; falls back to
 * clipping planes for legacy single-mesh trees. Returns false (caller falls
 * back to the plain burst) when the entity has no visual group or no scene.
 */
export function startTreeFall(
  state: State,
  entity: number,
  dirX: number,
  dirZ: number,
  cutHeight: number
): boolean {
  const scene = getScene(state);
  const source = findVisualGroup(state, entity);
  if (!scene || !source) return false;

  const len = Math.hypot(dirX, dirZ);
  if (len < 1e-4) {
    const a = Math.random() * Math.PI * 2;
    dirX = Math.sin(a);
    dirZ = Math.cos(a);
  } else {
    dirX /= len;
    dirZ /= len;
  }

  const halves = prepareTreeFallHalves(source, cutHeight);
  if (!halves) return false;

  const { stump, top, cutPoint, groundY, topLength, topPlane, materials } =
    halves;

  const pivot = new THREE.Object3D();
  pivot.position.copy(cutPoint);
  top.position.sub(cutPoint);
  pivot.add(top);
  scene.add(pivot);
  scene.add(stump);

  getFxStore(state).falls.push({
    pivot,
    stump,
    topPlane,
    materials,
    axis: new THREE.Vector3(dirZ, 0, -dirX).normalize(),
    dirX,
    dirZ,
    cutPoint,
    topLength,
    groundY,
    elapsed: 0,
    impactDone: false,
  });
  return true;
}

// --- Tree split (vertical half-split) -----------------------------------------

const SPLIT_DURATION = 1.0;
const SPLIT_MAX_ANGLE = Math.PI * 0.46;
const SPLIT_HOLD = 1.0;
const SPLIT_FADE = 0.65;

/**
 * Felled-tree vertical split: the trunk splits in half along a vertical plane
 * perpendicular to the swing direction, and each half tips outward in opposite
 * directions (away from the split), raises dust/leaves on impact, then both
 * fade out. Returns false (caller falls back to the plain burst) when the
 * entity has no visual group or no scene.
 */
export function startTreeSplit(
  state: State,
  entity: number,
  dirX: number,
  dirZ: number,
  cutHeight: number
): boolean {
  const scene = getScene(state);
  const source = findVisualGroup(state, entity);
  if (!scene || !source) return false;

  // Swing direction (player → prop) projected on XZ; the split plane is
  // perpendicular to it so the two halves fall left/right of the swing.
  const len = Math.hypot(dirX, dirZ);
  if (len < 1e-4) {
    const a = Math.random() * Math.PI * 2;
    dirX = Math.sin(a);
    dirZ = Math.cos(a);
  } else {
    dirX /= len;
    dirZ /= len;
  }
  // Split-plane normal in XZ (rotate swing dir by 90°): this is the axis the
  // halves separate along. Each half keeps the side where normal·pos >= 0 / <=0.
  const splitN = new THREE.Vector3(dirZ, 0, -dirX).normalize();

  const halfA = cloneVisualAtWorld(source);
  const halfB = cloneVisualAtWorld(source);

  const box = new THREE.Box3().setFromObject(halfA);
  if (box.isEmpty()) return false;
  const height = box.max.y - box.min.y;
  const groundY = box.min.y;
  const cutY = groundY + Math.min(Math.max(cutHeight, 0.2), height * 0.4);
  const cutPoint = new THREE.Vector3(
    (box.min.x + box.max.x) / 2,
    cutY,
    (box.min.z + box.max.z) / 2
  );

  // Half A keeps splitN·(p − cutPoint) <= 0; half B keeps >= 0.
  const planeA = new THREE.Plane(splitN.clone(), -splitN.dot(cutPoint));
  const planeB = new THREE.Plane(splitN.clone().negate(), splitN.dot(cutPoint));
  const matsA = applyClipPlane(halfA, planeA);
  const matsB = applyClipPlane(halfB, planeB);

  const pivotA = new THREE.Object3D();
  pivotA.position.copy(cutPoint);
  halfA.position.sub(cutPoint);
  pivotA.add(halfA);
  const pivotB = new THREE.Object3D();
  pivotB.position.copy(cutPoint);
  halfB.position.sub(cutPoint);
  pivotB.add(halfB);
  scene.add(pivotA);
  scene.add(pivotB);

  // Each half rotates around the axis pointing along the swing direction
  // (lying on the ground perpendicular to the split), in opposite signs.
  const axis = new THREE.Vector3(dirX, 0, dirZ).normalize();

  const halves: SplitHalf[] = [
    {
      obj: halfA,
      pivot: pivotA,
      plane: planeA,
      baseSignedN: splitN.clone(),
      materials: matsA,
      sign: -1,
    },
    {
      obj: halfB,
      pivot: pivotB,
      plane: planeB,
      baseSignedN: splitN.clone().negate(),
      materials: matsB,
      sign: 1,
    },
  ];

  getFxStore(state).splits.push({
    halves,
    axis,
    cutPoint,
    groundY,
    halfHeight: Math.max(height - (cutY - groundY), 0.5),
    elapsed: 0,
    impactDone: false,
  });
  return true;
}

// --- Rock shatter --------------------------------------------------------------

const SHATTER_PIECES = 9;
const SHATTER_HOLD = 1.2;
const SHATTER_FADE = 0.6;
const SHATTER_GRAVITY = 20;

/**
 * Break the rock into tumbling debris chunks that land and fade. Piece color
 * samples the prop's first material; falls back to grey.
 */
export function startRockShatter(
  state: State,
  entity: number,
  x: number,
  y: number,
  z: number,
  scale: number
): boolean {
  const scene = getScene(state);
  if (!scene) return false;

  const color = new THREE.Color(0.42, 0.42, 0.45);
  const group = findVisualGroup(state, entity);
  group?.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const mat = (
      Array.isArray(mesh.material) ? mesh.material[0] : mesh.material
    ) as THREE.MeshStandardMaterial;
    if (mat?.color) color.copy(mat.color).multiplyScalar(0.85);
  });

  const geometry = new THREE.IcosahedronGeometry(1, 0);
  const material = new THREE.MeshStandardMaterial({
    color,
    roughness: 0.95,
    metalness: 0,
    flatShading: true,
  });
  setupCsmMaterial(state, material);

  const pieces: ShatterPiece[] = [];
  for (let i = 0; i < SHATTER_PIECES; i++) {
    const mesh = new THREE.Mesh(geometry, material);
    const s = (0.1 + Math.random() * 0.16) * Math.max(scale, 0.4);
    mesh.scale.set(
      s * (0.6 + Math.random() * 0.8),
      s * (0.6 + Math.random() * 0.8),
      s * (0.6 + Math.random() * 0.8)
    );
    mesh.position.set(
      x + (Math.random() - 0.5) * 0.5 * scale,
      y + 0.4 + Math.random() * 0.6 * scale,
      z + (Math.random() - 0.5) * 0.5 * scale
    );
    mesh.rotation.set(
      Math.random() * Math.PI,
      Math.random() * Math.PI,
      Math.random() * Math.PI
    );
    mesh.castShadow = true;
    scene.add(mesh);
    const angle = Math.random() * Math.PI * 2;
    const speed = 1.5 + Math.random() * 3.5;
    pieces.push({
      mesh,
      vel: new THREE.Vector3(
        Math.sin(angle) * speed,
        2.5 + Math.random() * 3.5,
        Math.cos(angle) * speed
      ),
      angVel: new THREE.Vector3(
        (Math.random() - 0.5) * 10,
        (Math.random() - 0.5) * 10,
        (Math.random() - 0.5) * 10
      ),
      landed: false,
    });
  }

  getFxStore(state).shatters.push({
    pieces,
    material,
    geometry,
    groundY: y,
    elapsed: 0,
  });
  return true;
}

// --- Update system --------------------------------------------------------------

const _q = new THREE.Quaternion();
const _n = new THREE.Vector3();
const _n2 = new THREE.Vector3();

function setOpacity(materials: THREE.Material[], opacity: number): void {
  for (const m of materials) {
    m.transparent = true;
    m.opacity = opacity;
  }
}

function updateFall(state: State, fx: FallFx, dt: number): boolean {
  fx.elapsed += dt;
  const t = fx.elapsed;

  if (t < FALL_DURATION) {
    // gravity-ish ease-in: slow lean, fast crash
    const k = Math.pow(t / FALL_DURATION, 2.4);
    fx.pivot.quaternion.setFromAxisAngle(fx.axis, FALL_MAX_ANGLE * k);
  } else if (!fx.impactDone) {
    fx.impactDone = true;
    fx.pivot.quaternion.setFromAxisAngle(fx.axis, FALL_MAX_ANGLE);
    spawnParticleBurst(state, {
      x: fx.cutPoint.x + fx.dirX * fx.topLength * 0.7,
      y: fx.groundY + 0.15,
      z: fx.cutPoint.z + fx.dirZ * fx.topLength * 0.7,
      preset: 'dust',
      count: 30,
      duration: 0.6,
    });
    spawnParticleBurst(state, {
      x: fx.cutPoint.x + fx.dirX * fx.topLength * 0.8,
      y: fx.groundY + 0.5,
      z: fx.cutPoint.z + fx.dirZ * fx.topLength * 0.8,
      preset: 'leaves',
      count: 25,
      duration: 0.4,
    });
  }

  // keep the cut plane glued to the rotating top half (clipping fallback only)
  if (fx.topPlane) {
    _q.copy(fx.pivot.quaternion);
    _n.set(0, 1, 0).applyQuaternion(_q);
    fx.topPlane.normal.copy(_n);
    fx.topPlane.constant = -_n.dot(fx.cutPoint);
  }

  const fadeStart = FALL_DURATION + FALL_HOLD;
  if (t > fadeStart) {
    const k = (t - fadeStart) / FALL_FADE;
    if (k >= 1) {
      fx.pivot.removeFromParent();
      fx.stump.removeFromParent();
      for (const m of fx.materials) m.dispose();
      return false;
    }
    setOpacity(fx.materials, 1 - k);
  }
  return true;
}

function updateSplit(state: State, fx: SplitFx, dt: number): boolean {
  fx.elapsed += dt;
  const t = fx.elapsed;

  // Original (untransformed) split-plane normal — perpendicular to swing dir.
  // swing dir = (axis.x, 0, axis.z); perpendicular = (axis.z, 0, -axis.x).
  const baseN = _n2.set(fx.axis.z, 0, -fx.axis.x);

  if (t < SPLIT_DURATION) {
    const k = Math.pow(t / SPLIT_DURATION, 2.4);
    for (const half of fx.halves) {
      half.pivot.quaternion.setFromAxisAngle(
        fx.axis,
        half.sign * SPLIT_MAX_ANGLE * k
      );
    }
  } else if (!fx.impactDone) {
    fx.impactDone = true;
    for (const half of fx.halves) {
      half.pivot.quaternion.setFromAxisAngle(
        fx.axis,
        half.sign * SPLIT_MAX_ANGLE
      );
    }
    // Leaves where each half's canopy meets the ground (offset along the split
    // normal on each side), plus a woodchip burst at the split seam.
    spawnParticleBurst(state, {
      x: fx.cutPoint.x + baseN.x * fx.halfHeight * 0.6,
      y: fx.groundY + 0.2,
      z: fx.cutPoint.z + baseN.z * fx.halfHeight * 0.6,
      preset: 'leaves',
      count: 18,
      duration: 0.45,
    });
    spawnParticleBurst(state, {
      x: fx.cutPoint.x - baseN.x * fx.halfHeight * 0.6,
      y: fx.groundY + 0.2,
      z: fx.cutPoint.z - baseN.z * fx.halfHeight * 0.6,
      preset: 'leaves',
      count: 18,
      duration: 0.45,
    });
    spawnParticleBurst(state, {
      x: fx.cutPoint.x,
      y: fx.cutPoint.y,
      z: fx.cutPoint.z,
      preset: 'woodchips',
      count: 24,
      duration: 0.5,
    });
  }

  // Keep each half's clip plane glued to its rotation so the split seam stays
  // sharp as the halves rotate apart. Rotate the half's signed base normal
  // (captured at creation) by the current pivot quaternion each frame. Using
  // the stored baseSignedN avoids accumulating rotation across frames.
  for (const half of fx.halves) {
    _q.copy(half.pivot.quaternion);
    _n.copy(half.baseSignedN).applyQuaternion(_q).normalize();
    half.plane.normal.copy(_n);
    half.plane.constant = -_n.dot(fx.cutPoint);
  }

  const fadeStart = SPLIT_DURATION + SPLIT_HOLD;
  if (t > fadeStart) {
    const k = (t - fadeStart) / SPLIT_FADE;
    const allMats = fx.halves.flatMap((h) => h.materials);
    if (k >= 1) {
      for (const half of fx.halves) half.pivot.removeFromParent();
      for (const m of allMats) m.dispose();
      return false;
    }
    setOpacity(allMats, 1 - k);
  }
  return true;
}

function updateShatter(fx: ShatterFx, dt: number): boolean {
  fx.elapsed += dt;
  for (const piece of fx.pieces) {
    if (piece.landed) continue;
    piece.vel.y -= SHATTER_GRAVITY * dt;
    piece.mesh.position.addScaledVector(piece.vel, dt);
    piece.mesh.rotation.x += piece.angVel.x * dt;
    piece.mesh.rotation.y += piece.angVel.y * dt;
    piece.mesh.rotation.z += piece.angVel.z * dt;
    const restY = fx.groundY + piece.mesh.scale.y * 0.5;
    if (piece.vel.y < 0 && piece.mesh.position.y <= restY) {
      piece.mesh.position.y = restY;
      piece.landed = true;
    }
  }

  if (fx.elapsed > SHATTER_HOLD) {
    const k = (fx.elapsed - SHATTER_HOLD) / SHATTER_FADE;
    if (k >= 1) {
      for (const piece of fx.pieces) piece.mesh.removeFromParent();
      fx.material.dispose();
      fx.geometry.dispose();
      return false;
    }
    setOpacity([fx.material], 1 - k);
  }
  return true;
}

export const DestructibleFxSystem: System = defineSystem({
  name: 'DestructibleFxSystem',
  group: 'draw',

  update(state: State) {
    const store = fxByState.get(state);
    if (!store) return;
    const dt = state.time.deltaTime;

    for (const [entity, shake] of store.shakes) {
      shake.elapsed += dt;
      const k = shake.elapsed / shake.duration;
      if (k >= 1) {
        for (const t of shake.targets) {
          t.obj.rotation.x = t.rx;
          t.obj.rotation.z = t.rz;
        }
        store.shakes.delete(entity);
        continue;
      }
      const decay = (1 - k) * shake.amp;
      const phase = shake.elapsed * 34;
      for (const t of shake.targets) {
        t.obj.rotation.x = t.rx + Math.sin(phase) * decay;
        t.obj.rotation.z = t.rz + Math.cos(phase * 1.3) * decay;
      }
    }

    for (let i = store.falls.length - 1; i >= 0; i--) {
      if (!updateFall(state, store.falls[i], dt)) store.falls.splice(i, 1);
    }
    for (let i = store.splits.length - 1; i >= 0; i--) {
      if (!updateSplit(state, store.splits[i], dt)) store.splits.splice(i, 1);
    }
    for (let i = store.shatters.length - 1; i >= 0; i--) {
      if (!updateShatter(store.shatters[i], dt)) store.shatters.splice(i, 1);
    }
  },
});
