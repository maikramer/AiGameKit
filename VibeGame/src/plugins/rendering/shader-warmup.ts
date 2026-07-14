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

/** Yaw samples during silent warmup renders — covers a full turn so materials
 *  that only enter the frustum after the player looks around compile behind
 *  the loading screen instead of hitching on the first mouse flick. */
const WARMUP_YAW_SAMPLES = 8;

/**
 * Compile WebGL programs for the current scene and force a few off-axis draws
 * so first-look shader/shadow compiles don't freeze gameplay.
 *
 * Returns `true` once warmup has completed (or is a no-op in headless).
 * Returns `false` when the renderer/camera are not ready yet — caller should
 * retry next frame (typically while the loading overlay is still up).
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

  try {
    renderer.compile(scene, camera);
  } catch (err) {
    logger.warn('[warmup] renderer.compile failed', err);
  }

  _savedQuat.copy(camera.quaternion);
  _savedPos.copy(camera.position);
  _euler.setFromQuaternion(_savedQuat, 'YXZ');
  const baseYaw = _euler.y;

  for (let i = 0; i < WARMUP_YAW_SAMPLES; i++) {
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
    } catch (err) {
      logger.warn('[warmup] warmup render failed', err);
    }
  }

  camera.quaternion.copy(_savedQuat);
  camera.position.copy(_savedPos);
  camera.updateMatrixWorld(true);

  warmedStates.set(state, true);
  logger.info(
    `[warmup] Scene shaders warmed (${WARMUP_YAW_SAMPLES} yaw samples)`
  );
  return true;
}

/** Test helper — clear the warmup latch for a state. */
export function resetShaderWarmup(state: State): void {
  warmedStates.delete(state);
}
