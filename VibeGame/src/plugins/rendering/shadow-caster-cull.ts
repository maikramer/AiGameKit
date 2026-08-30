import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { DirectionalLight } from './components';
import { getMainThreeCamera, getScene } from './utils';

/**
 * Drop shadow casting from objects whose shadow cannot be resolved anyway.
 *
 * The shadow map is a second full draw of the caster set — in simple-rpg it is
 * ~250 draw calls and 3.4M triangles per frame, a fifth of all GPU time (GPU
 * timer queries: 33ms/frame total, 6.6ms of it shadows). Most of that comes
 * from small props scattered across the shadow frustum: a 0.8m rock 90m out
 * projects a shadow a couple of texels wide, which PCSS then blurs into
 * nothing. Drawing it costs the same as the rock in front of the player.
 *
 * The test is angular size, not distance: `radius / distance` is what the
 * shadow map actually resolves, so a boulder keeps its shadow where a pebble
 * at the same distance loses one. Below {@link MIN_CULL_DISTANCE} nothing is
 * ever culled, so anything near the player is untouched no matter how small.
 *
 * Instanced pools are skipped — one `castShadow` flag covers every instance in
 * the pool, and the library culls/LODs per instance on its own. Hidden
 * subtrees are skipped too: `DistanceCull` already owns their `castShadow`,
 * and stepping over them keeps this walk off the culled half of the graph.
 */

/** Below this distance (metres) an object always keeps its shadow. */
export const MIN_CULL_DISTANCE = 25;
/** Default angular size under which a caster's shadow is dropped. */
export const DEFAULT_SHADOW_CULL_RATIO = 0.01;
/** Restore multiplier — a culled caster comes back at 1.35x the cull ratio. */
export const SHADOW_CULL_HYSTERESIS = 1.35;
/** Frames between passes. Shadow pop is invisible at these sizes; 10 keeps the
 *  walk at ~6Hz. */
const CULL_INTERVAL_FRAMES = 10;

/**
 * Should this caster still cast, given its angular size?
 *
 * `wasCulled` carries the hysteresis: a caster that lost its shadow needs to
 * grow past `ratio * SHADOW_CULL_HYSTERESIS` to get it back, so an object
 * hovering exactly on the threshold cannot flicker frame to frame.
 */
export function shouldKeepShadow(
  worldRadius: number,
  distance: number,
  cullRatio: number,
  wasCulled: boolean
): boolean {
  if (cullRatio <= 0) return true;
  if (distance <= MIN_CULL_DISTANCE) return true;
  if (worldRadius <= 0) return true;
  const angular = worldRadius / distance;
  const threshold = wasCulled ? cullRatio * SHADOW_CULL_HYSTERESIS : cullRatio;
  return angular >= threshold;
}

/** Casters we switched off, with the flag they had before. */
const shadowCasterSaved = new WeakMap<THREE.Object3D, boolean>();
const lastPassFrame = new WeakMap<State, number>();

/** True when this pass turned the object's shadow off (test hook). */
export function isShadowCasterCulled(object: THREE.Object3D): boolean {
  return shadowCasterSaved.has(object);
}

/** Give every caster this pass switched off its shadow back. */
export function restoreCulledShadowCasters(root: THREE.Object3D): void {
  root.traverse((obj) => {
    const saved = shadowCasterSaved.get(obj);
    if (saved === undefined) return;
    obj.castShadow = saved;
    shadowCasterSaved.delete(obj);
  });
}

const _camPos = new THREE.Vector3();

/** Largest axis scale in a world matrix — bounds scale up with the object. */
function maxScaleOf(matrix: THREE.Matrix4): number {
  const e = matrix.elements;
  const sx = Math.hypot(e[0]!, e[1]!, e[2]!);
  const sy = Math.hypot(e[4]!, e[5]!, e[6]!);
  const sz = Math.hypot(e[8]!, e[9]!, e[10]!);
  return Math.max(sx, sy, sz);
}

function applyToMesh(
  mesh: THREE.Mesh,
  camPos: THREE.Vector3,
  cullRatio: number
): void {
  const geometry = mesh.geometry;
  if (!geometry) return;
  if (!geometry.boundingSphere) geometry.computeBoundingSphere();
  const sphere = geometry.boundingSphere;
  if (!sphere) return;

  const e = mesh.matrixWorld.elements;
  const dx = e[12]! - camPos.x;
  const dy = e[13]! - camPos.y;
  const dz = e[14]! - camPos.z;
  const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
  const radius = sphere.radius * maxScaleOf(mesh.matrixWorld);

  const wasCulled = shadowCasterSaved.has(mesh);
  const keep = shouldKeepShadow(radius, distance, cullRatio, wasCulled);

  if (keep) {
    if (!wasCulled) return;
    mesh.castShadow = shadowCasterSaved.get(mesh) ?? true;
    shadowCasterSaved.delete(mesh);
    return;
  }
  if (wasCulled) return;
  if (mesh.castShadow !== true) return; // nothing to drop
  shadowCasterSaved.set(mesh, mesh.castShadow);
  mesh.castShadow = false;
}

/**
 * Apply the angular-size cull to every visible caster under `root`.
 *
 * Walks the visible graph only. `traverse` would also descend the hidden half
 * (culled props and the parked LOD children of every rigged prop — the bulk of
 * the node count), where `castShadow` belongs to `DistanceCull`, not to us.
 */
export function cullShadowCastersInSubtree(
  object: THREE.Object3D,
  camPos: THREE.Vector3,
  cullRatio: number
): void {
  if (object.visible === false) return;
  const mesh = object as THREE.Mesh;
  // InstancedMesh / InstancedMesh2: one flag for the whole pool — the library's
  // per-instance culling and LOD already own this decision.
  const instanced = (mesh as unknown as { isInstancedMesh?: boolean })
    .isInstancedMesh;
  if (mesh.isMesh === true && instanced !== true) {
    applyToMesh(mesh, camPos, cullRatio);
  }
  const children = object.children;
  for (let i = 0; i < children.length; i++) {
    cullShadowCastersInSubtree(children[i]!, camPos, cullRatio);
  }
}

const directionalQuery = defineQuery([DirectionalLight]);

/** Cull ratio in force: the first directional light that sets one wins. */
function resolveCullRatio(state: State): number {
  const lights = directionalQuery(state.world);
  for (const eid of lights) {
    if (DirectionalLight.castShadow[eid] !== 1) continue;
    return DirectionalLight.shadowCullRatio[eid];
  }
  return 0;
}

export const ShadowCasterCullSystem: System = defineSystem({
  name: 'ShadowCasterCullSystem',
  group: 'draw',
  update(state: State): void {
    if (state.headless) return;

    const frame = state.time.frameCount;
    const last = lastPassFrame.get(state);
    if (last !== undefined && frame - last < CULL_INTERVAL_FRAMES) return;
    lastPassFrame.set(state, frame);

    const scene = getScene(state);
    if (!scene) return;

    const cullRatio = resolveCullRatio(state);
    if (cullRatio <= 0) {
      // Turned off at runtime: hand every caster we took back before idling.
      restoreCulledShadowCasters(scene);
      return;
    }

    const camera = getMainThreeCamera(state);
    if (!camera) return;
    camera.getWorldPosition(_camPos);

    cullShadowCastersInSubtree(scene, _camPos, cullRatio);
  },
});
