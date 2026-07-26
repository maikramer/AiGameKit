import * as THREE from 'three';
import {
  defineQuery,
  defineSystem,
  getLoadingProgress,
  type State,
  type System,
} from '../../core';
import { logger } from '../../core/utils/logger';
import { MainCamera } from './components';
import { applyPcssShadowPatch } from './pcss-shadow';
import { getRenderingContext, threeCameras } from './utils';

const mainCameraQuery = defineQuery([MainCamera]);

const warmedStates = new WeakMap<State, boolean>();
const waitAttempts = new WeakMap<State, number>();
const startWaits = new WeakMap<State, number>();

const _euler = new THREE.Euler();
const _drawSize = new THREE.Vector2();

/** Full yaw turn so materials that only enter the side/rear frustum compile
 *  behind the loading screen instead of hitching on the first mouse flick. */
const WARMUP_YAW_SAMPLES = 8;
/** Pitch samples (look down / level / look up) — covers ground + sky materials. */
const WARMUP_PITCHES = [-0.55, 0, 0.4] as const;
/** Per-frame slice for orbit draws. A normal draw is ~13ms on simple-rpg, so
 *  this lets one through per frame and stops before a second would overrun. */
const ORBIT_BUDGET_MS = 10;
/** Hard ceiling for the whole warmup. Beyond this the remaining draws cost
 *  more than the hitches they prevent. */
const TOTAL_BUDGET_MS = 3000;
/** Frames to wait for renderer/camera before latching the gate anyway. */
const MAX_WAIT_FRAMES = 120;
/** Frames to wait for the sibling gates before warming regardless. */
const MAX_START_WAIT_FRAMES = 900;

interface OrbitJob {
  phase: 'compile' | 'orbit' | 'done';
  yawIndex: number;
  pitchIndex: number;
  baseYaw: number;
  draws: number;
  spentMs: number;
  savedQuat: THREE.Quaternion;
  savedPos: THREE.Vector3;
}

const pendingOrbit = new WeakMap<State, OrbitJob>();

interface CulledRestore {
  obj: THREE.Object3D;
  frustumCulled: boolean;
  visible: boolean;
}

/**
 * Open the loading `shaders` gate and schedule the warmup work.
 *
 * The gate latches on the first successful call: warming must never be able to
 * hold the loading overlay up, because the overlay is what the player stares at
 * while it happens. The actual compile and the orbit draws are spread over the
 * following frames by {@link ShaderWarmupSystem}.
 *
 * Returns `false` only while the renderer/camera are missing (retry next frame).
 */
export function warmupSceneShaders(state: State): boolean {
  if (warmedStates.get(state)) return true;
  if (state.headless) {
    warmedStates.set(state, true);
    return true;
  }

  const context = getRenderingContext(state);
  const renderer = context.renderer;
  const scene = context.scene;
  if (!renderer || !scene) return waitOrForceLatch(state, 'no renderer/scene');

  const cams = mainCameraQuery(state.world);
  if (cams.length === 0) return waitOrForceLatch(state, 'no MainCamera entity');
  const camera = threeCameras.get(cams[0]!);
  if (!camera) {
    return waitOrForceLatch(state, 'MainCamera not in threeCameras yet');
  }

  // Firefox reports a 0×0 drawing buffer until the first present; size from CSS
  // so the warmup draws (and any FBO they touch) have real dimensions.
  const canvas = renderer.domElement;
  const cssW = canvas?.clientWidth || 0;
  const cssH = canvas?.clientHeight || 0;
  renderer.getDrawingBufferSize(_drawSize);
  if ((_drawSize.x <= 0 || _drawSize.y <= 0) && cssW > 0 && cssH > 0) {
    renderer.setSize(cssW, cssH, false);
  }

  warmedStates.set(state, true);
  waitAttempts.delete(state);
  pendingOrbit.set(state, {
    phase: 'compile',
    yawIndex: 0,
    pitchIndex: 0,
    baseYaw: 0,
    draws: 0,
    spentMs: 0,
    savedQuat: camera.quaternion.clone(),
    savedPos: camera.position.clone(),
  });
  return true;
}

function waitOrForceLatch(state: State, reason: string): boolean {
  const n = (waitAttempts.get(state) ?? 0) + 1;
  waitAttempts.set(state, n);
  if (n < MAX_WAIT_FRAMES) return false;
  logger.warn(`[warmup] ${reason} after ${n} frames — opening gate anyway`);
  warmedStates.set(state, true);
  waitAttempts.delete(state);
  return true;
}

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

/**
 * Advance the scheduled warmup by at most {@link ORBIT_BUDGET_MS}.
 *
 * Two rules keep this off the "frozen tab" path:
 * - meshes are forced visible only around `renderer.compile` (program creation,
 *   no rasterization: ~70ms on simple-rpg);
 * - the draws use the scene's own visibility and frustum culling. Drawing all
 *   9k meshes unculled measured **4.8s per frame** — 24 of those behind the
 *   loading screen is the freeze this replaced.
 */
export function pumpShaderWarmup(state: State): void {
  const job = pendingOrbit.get(state);
  if (!job || job.phase === 'done') return;

  const context = getRenderingContext(state);
  const renderer = context.renderer;
  const scene = context.scene;
  if (!renderer || !scene) return;

  const cams = mainCameraQuery(state.world);
  const camera = cams.length > 0 ? threeCameras.get(cams[0]!) : undefined;
  if (!camera) return;

  renderer.getDrawingBufferSize(_drawSize);
  if (_drawSize.x <= 0 || _drawSize.y <= 0) return;

  const started = now();

  if (job.phase === 'compile') {
    // Patch PCSS before compile so shadow programs include the soft-shadow path
    // (simple-rpg opts in via directional-light pcss:1).
    applyPcssShadowPatch();
    const restores = forceSceneDrawables(scene);
    try {
      renderer.compile(scene, camera);
    } catch (err) {
      logger.warn('[warmup] renderer.compile failed', err);
    } finally {
      restoreSceneDrawables(restores);
    }
    // Compile creates programs; don't charge it against the orbit budget —
    // otherwise a cold scene (simple-rpg ~900 mats) burns TOTAL_BUDGET_MS
    // before a single yaw sample runs.
    job.phase = 'orbit';
    job.savedQuat.copy(camera.quaternion);
    job.savedPos.copy(camera.position);
    _euler.setFromQuaternion(job.savedQuat, 'YXZ');
    job.baseYaw = _euler.y;
    return;
  }

  while (now() - started < ORBIT_BUDGET_MS) {
    if (job.pitchIndex >= WARMUP_PITCHES.length) break;
    _euler.set(
      WARMUP_PITCHES[job.pitchIndex]!,
      job.baseYaw + (job.yawIndex / WARMUP_YAW_SAMPLES) * Math.PI * 2,
      0,
      'YXZ'
    );
    camera.quaternion.setFromEuler(_euler);
    camera.position.copy(job.savedPos);
    camera.updateMatrixWorld(true);
    try {
      renderer.render(scene, camera);
      job.draws++;
    } catch (err) {
      logger.warn('[warmup] orbit draw failed', err);
      job.pitchIndex = WARMUP_PITCHES.length;
      break;
    }
    job.yawIndex++;
    if (job.yawIndex >= WARMUP_YAW_SAMPLES) {
      job.yawIndex = 0;
      job.pitchIndex++;
    }
  }

  job.spentMs += now() - started;
  const exhausted = job.spentMs >= TOTAL_BUDGET_MS;
  if (job.pitchIndex >= WARMUP_PITCHES.length || exhausted) {
    finishOrbit(state, camera, job, exhausted);
  }
}

function finishOrbit(
  state: State,
  camera: THREE.Camera,
  job: OrbitJob,
  exhausted: boolean
): void {
  camera.quaternion.copy(job.savedQuat);
  camera.position.copy(job.savedPos);
  camera.updateMatrixWorld(true);
  job.phase = 'done';
  pendingOrbit.delete(state);

  const context = getRenderingContext(state);
  if (context.renderer && context.scene) {
    try {
      // Programs created mid-orbit (shadow/skinning variants) latch here.
      context.renderer.compile(context.scene, camera);
    } catch (err) {
      logger.warn('[warmup] post-orbit compile failed', err);
    }
  }

  const suffix = exhausted ? ' — stopped on time budget' : '';
  logger.info(
    `[warmup] Scene shaders warmed (${job.draws} draws, ${Math.round(job.spentMs)}ms)${suffix}`
  );
}

/** Force every drawable into the compile path; return restore list. */
function forceSceneDrawables(scene: THREE.Scene): CulledRestore[] {
  const restores: CulledRestore[] = [];
  scene.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    const line = obj as THREE.Line;
    const points = obj as THREE.Points;
    const sprite = obj as THREE.Sprite;
    const warmable =
      mesh.isMesh ||
      line.isLine ||
      points.isPoints ||
      sprite.isSprite ||
      obj.type === 'Group';
    if (!warmable) return;
    restores.push({
      obj,
      frustumCulled: obj.frustumCulled,
      visible: obj.visible,
    });
    obj.frustumCulled = false;
    obj.visible = true;
  });
  return restores;
}

function restoreSceneDrawables(restores: CulledRestore[]): void {
  for (const r of restores) {
    r.obj.frustumCulled = r.frustumCulled;
    r.obj.visible = r.visible;
  }
}

/**
 * Drives the warmup independently of the loading overlay.
 *
 * It used to be pumped from `updateLoadingScreen`, which stops running the
 * moment the overlay fades — so whatever had not compiled yet never did.
 */
export const ShaderWarmupSystem: System = defineSystem({
  name: 'ShaderWarmupSystem',
  group: 'draw',
  // String form — avoid importing CameraSyncSystem (cycles with plugin/systems).
  after: ['CameraSyncSystem'],
  update(state) {
    if (state.headless) return;
    if (warmedStates.get(state)) {
      pumpShaderWarmup(state);
      return;
    }
    // Warm the populated scene, not an empty boot frame: hold until the sibling
    // gates (assets/terrain/spawn) pass. Bounded, so a gate that never opens
    // costs a late warmup instead of no warmup.
    const waited = (startWaits.get(state) ?? 0) + 1;
    startWaits.set(state, waited);
    const pending = getLoadingProgress(state).pending.filter(
      (p) => p !== 'shaders'
    );
    if (pending.length > 0 && waited < MAX_START_WAIT_FRAMES) return;
    warmupSceneShaders(state);
  },
});

/** Test helper — clear the warmup latch for a state. */
export function resetShaderWarmup(state: State): void {
  pendingOrbit.delete(state);
  warmedStates.delete(state);
  waitAttempts.delete(state);
  startWaits.delete(state);
}

/** True once {@link warmupSceneShaders} has latched for this state. */
export function isSceneShadersWarmed(state: State): boolean {
  return warmedStates.get(state) === true;
}
