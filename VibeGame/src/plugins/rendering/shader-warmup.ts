import * as THREE from 'three';
import { defineQuery, type State } from '../../core';
import { logger } from '../../core/utils/logger';
import { MainCamera } from './components';
import { applyPcssShadowPatch } from './pcss-shadow';
import { getRenderingContext, threeCameras } from './utils';

const mainCameraQuery = defineQuery([MainCamera]);

const warmedStates = new WeakMap<State, boolean>();

const _savedQuat = new THREE.Quaternion();
const _savedPos = new THREE.Vector3();
const _euler = new THREE.Euler();

/** Full yaw turn so materials that only enter the side/rear frustum compile
 *  behind the loading screen instead of hitching on the first mouse flick. */
const WARMUP_YAW_SAMPLES = 8;
/** Pitch samples (look down / level / look up) — covers ground + sky materials. */
const WARMUP_PITCHES = [-0.55, 0, 0.4] as const;

interface CulledRestore {
  obj: THREE.Object3D;
  frustumCulled: boolean;
  visible: boolean;
}

/**
 * Compile WebGL programs for the current scene and force silent draws across
 * yaw + pitch so first-look shader/shadow compiles don't freeze gameplay.
 *
 * While warming, every mesh/group is forced visible and frustum-culling is
 * disabled so DistanceCull / off-axis props still compile even if hidden.
 *
 * Returns `true` once warmup has completed (or is a no-op in headless).
 * Returns `false` when the renderer/camera are not ready yet — caller should
 * retry next frame (typically while the loading overlay is still up).
 *
 * Call only after assets/terrain/spawn gates pass — the latch is one-shot.
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
  if (!renderer || !scene) return false;

  const cams = mainCameraQuery(state.world);
  if (cams.length === 0) return false;
  const camera = threeCameras.get(cams[0]!);
  if (!camera) return false;

  // Patch PCSS before compile so shadow programs include the soft-shadow path
  // (simple-rpg opts in via directional-light pcss:1).
  applyPcssShadowPatch();

  const restores = forceSceneDrawables(scene);

  try {
    renderer.compile(scene, camera);
  } catch (err) {
    logger.warn('[warmup] renderer.compile failed', err);
  }

  _savedQuat.copy(camera.quaternion);
  _savedPos.copy(camera.position);
  _euler.setFromQuaternion(_savedQuat, 'YXZ');
  const baseYaw = _euler.y;

  let draws = 0;
  for (const pitch of WARMUP_PITCHES) {
    for (let i = 0; i < WARMUP_YAW_SAMPLES; i++) {
      _euler.x = pitch;
      _euler.y = baseYaw + (i / WARMUP_YAW_SAMPLES) * Math.PI * 2;
      camera.quaternion.setFromEuler(_euler);
      camera.position.copy(_savedPos);
      camera.updateMatrixWorld(true);
      try {
        if (context.postProcessing) {
          context.postProcessing.render();
        } else {
          renderer.render(scene, camera);
        }
        draws++;
      } catch (err) {
        logger.warn('[warmup] warmup render failed', err);
      }
    }
  }

  // Programs created mid-orbit (shadow/skinning variants) latch here.
  try {
    renderer.compile(scene, camera);
  } catch (err) {
    logger.warn('[warmup] post-orbit compile failed', err);
  }

  camera.quaternion.copy(_savedQuat);
  camera.position.copy(_savedPos);
  camera.updateMatrixWorld(true);
  restoreSceneDrawables(restores);

  warmedStates.set(state, true);
  logger.info(
    `[warmup] Scene shaders warmed (${draws} draws: ${WARMUP_YAW_SAMPLES} yaw × ${WARMUP_PITCHES.length} pitch)`
  );
  return true;
}

/** Force every drawable into the compile/render path; return restore list. */
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

/** Test helper — clear the warmup latch for a state. */
export function resetShaderWarmup(state: State): void {
  warmedStates.delete(state);
}

/** True once {@link warmupSceneShaders} has latched for this state. */
export function isSceneShadersWarmed(state: State): boolean {
  return warmedStates.get(state) === true;
}
