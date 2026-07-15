import * as THREE from 'three';
import type { State, System } from '../../core';
import { defineQuery } from '../../core';
import {
  getGpuTier,
  getRenderingContext,
  MainCamera,
  threeCameras,
} from '../rendering';
import { CameraSyncSystem } from '../rendering/systems';
import {
  getAdaptiveQualityTier,
  TIER_PRESETS,
} from '../adaptive-quality/quality-tiers';
import { distanceToPath } from './path-utils';
import { bodySurfaceYAt, getRiverFlatPath } from './registry';
import { waterEmptyReflectionTexture, waterSideCars } from './systems';
import type { WaterSideCar } from './water-shape';

const mainCameraQuery = defineQuery([MainCamera]);

/** Distance from `(px, pz)` to the nearest point of a water body — a lake
 * centre or a river polyline. Used both to pick the nearest body and (for
 * lakes) as a stand-in reflector position; rivers use {@link bodySurfaceYAt}
 * instead since they have no single centre. */
function distanceToBody(
  body: WaterSideCar['body'],
  px: number,
  pz: number
): number {
  if (body.kind === 'lake') {
    return Math.hypot(body.x - px, body.z - pz);
  }
  return distanceToPath(getRiverFlatPath(body.path), px, pz);
}

/** Only the water body (lake or river) nearest the camera gets a real mirror
 * — beyond this it's not worth doubling a scene render for a reflection
 * nobody's looking at closely. */
const MAX_REFLECT_DISTANCE = 40;
/** Square render target size. Water is stylized/blurred by ripples anyway, so
 * this stays modest — it's a mirror, not a hero render target. */
const REFLECTION_SIZE = 256;
/** GPU tiers 0-1 (from `detect-gpu`) skip the extra scene render entirely. */
const MIN_GPU_TIER = 2;

let renderTarget: THREE.WebGLRenderTarget | null = null;
let reflectionCamera: THREE.PerspectiveCamera | null = null;
/** Water entity currently owning the shared render target (its material has
 * `uHasReflection = 1`); reset to fallback sky-tint when it stops being the
 * nearest body or goes out of range. */
let activeEntity: number | null = null;

const _reflectorPos = new THREE.Vector3();
const _cameraPos = new THREE.Vector3();
const _normal = new THREE.Vector3(0, 1, 0);
const _view = new THREE.Vector3();
const _target = new THREE.Vector3();
const _lookAt = new THREE.Vector3();
const _rotationMatrix = new THREE.Matrix4();
const _reflectorPlane = new THREE.Plane();
const _clipPlane = new THREE.Vector4();
const _q = new THREE.Vector4();
const _textureMatrix = new THREE.Matrix4();
const _lastReflectCam = new THREE.Vector3(Number.NaN, Number.NaN, Number.NaN);
let _lastReflectEntity: number | null = null;
const REFLECT_CAM_STILL_EPS_SQ = 0.01; // ~0.1 m — reuse prior mirror RT

function getRenderTarget(): THREE.WebGLRenderTarget {
  if (!renderTarget) {
    renderTarget = new THREE.WebGLRenderTarget(
      REFLECTION_SIZE,
      REFLECTION_SIZE
    );
  } else if (
    renderTarget.width !== REFLECTION_SIZE ||
    renderTarget.height !== REFLECTION_SIZE
  ) {
    renderTarget.setSize(REFLECTION_SIZE, REFLECTION_SIZE);
  }
  return renderTarget;
}

function clearActiveReflection(cars: Map<number, WaterSideCar>): void {
  if (activeEntity === null) return;
  const car = cars.get(activeEntity);
  if (car) {
    car.material.uniforms.uHasReflection.value = 0;
    // Must also unbind the render target texture here, not just flip the
    // flag — a stale reference left on this (now unhidden) mesh's sampler
    // makes WebGL flag a framebuffer/texture feedback loop the next time
    // some *other* body's reflection pass renders this mesh into the very
    // target its sampler still points at.
    car.material.uniforms.uReflectionMap.value = waterEmptyReflectionTexture;
  }
  activeEntity = null;
}

/**
 * Renders the scene mirrored across a water body's local surface plane into a
 * shared render target, then points its material's `uReflectionMap` at it.
 * Math adapted from `three/examples/jsm/objects/Reflector.js` (mirror camera
 * + oblique near-plane clip so geometry below the waterline doesn't leak into
 * the reflection), specialised for an always-horizontal plane. Works for both
 * a lake's flat disc and a river's sloped surface — see {@link distanceToBody}.
 */
function renderReflection(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.PerspectiveCamera,
  entity: number,
  car: WaterSideCar
): void {
  _cameraPos.copy(camera.position);
  // The plane is always horizontal, so any point at the local water height
  // lies on it — using the camera's own XZ means this works for a lake's flat
  // disc *and* a river's sloped surface without needing a "centre" for either.
  const waterY = bodySurfaceYAt(car.body, _cameraPos.x, _cameraPos.z);
  _reflectorPos.set(_cameraPos.x, waterY, _cameraPos.z);

  _view.subVectors(_reflectorPos, _cameraPos);
  // Camera under the water plane can't see a surface reflection (would need
  // an upward mirror instead) — skip this frame rather than render garbage.
  if (_view.dot(_normal) > 0) return;

  _view.reflect(_normal).negate().add(_reflectorPos);

  _rotationMatrix.extractRotation(camera.matrixWorld);
  _lookAt.set(0, 0, -1).applyMatrix4(_rotationMatrix).add(_cameraPos);

  _target.subVectors(_reflectorPos, _lookAt);
  _target.reflect(_normal).negate().add(_reflectorPos);

  if (!reflectionCamera) reflectionCamera = camera.clone();
  const rc = reflectionCamera;
  rc.copy(camera);
  rc.position.copy(_view);
  rc.up.set(0, 1, 0).applyMatrix4(_rotationMatrix).reflect(_normal);
  rc.lookAt(_target);
  rc.updateMatrixWorld();

  _textureMatrix.set(
    0.5,
    0.0,
    0.0,
    0.5,
    0.0,
    0.5,
    0.0,
    0.5,
    0.0,
    0.0,
    0.5,
    0.5,
    0.0,
    0.0,
    0.0,
    1.0
  );
  _textureMatrix.multiply(rc.projectionMatrix);
  _textureMatrix.multiply(rc.matrixWorldInverse);

  // Oblique near-plane clip (Lengyel's technique) so anything below the water
  // plane — the lakebed, fish, etc. — is clipped out of the mirror instead of
  // doubling up under the reflected geometry.
  _reflectorPlane.setFromNormalAndCoplanarPoint(_normal, _reflectorPos);
  _reflectorPlane.applyMatrix4(rc.matrixWorldInverse);
  _clipPlane.set(
    _reflectorPlane.normal.x,
    _reflectorPlane.normal.y,
    _reflectorPlane.normal.z,
    _reflectorPlane.constant
  );
  const pm = rc.projectionMatrix;
  _q.x = (Math.sign(_clipPlane.x) + pm.elements[8]!) / pm.elements[0]!;
  _q.y = (Math.sign(_clipPlane.y) + pm.elements[9]!) / pm.elements[5]!;
  _q.z = -1.0;
  _q.w = (1.0 + pm.elements[10]!) / pm.elements[14]!;
  _clipPlane.multiplyScalar(2.0 / _clipPlane.dot(_q));
  pm.elements[2] = _clipPlane.x;
  pm.elements[6] = _clipPlane.y;
  pm.elements[10] = _clipPlane.z + 1.0;
  pm.elements[14] = _clipPlane.w;

  const rt = getRenderTarget();
  const prevTarget = renderer.getRenderTarget();
  const prevShadowAutoUpdate = renderer.shadowMap.autoUpdate;
  const wasVisible = car.mesh.visible;

  car.mesh.visible = false;
  renderer.shadowMap.autoUpdate = false;
  renderer.setRenderTarget(rt);
  renderer.render(scene, rc);
  renderer.setRenderTarget(prevTarget);
  renderer.shadowMap.autoUpdate = prevShadowAutoUpdate;
  car.mesh.visible = wasVisible;

  const u = car.material.uniforms;
  u.uReflectionMap.value = rt.texture;
  (u.uReflectionMatrix.value as THREE.Matrix4).copy(_textureMatrix);
  u.uHasReflection.value = 1;
  activeEntity = entity;
}

/**
 * Real planar mirror reflection for the water body (lake or river) nearest
 * the camera (see `renderReflection`). Farther water keeps the flat sky-tint
 * fallback baked into the water shader — rendering the whole scene twice per
 * visible body would not pay for itself. Gated off on low GPU tiers
 * (`detectGpuTier`).
 */
export const WaterReflectionSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    const ctx = getRenderingContext(state);
    const renderer = ctx.renderer;
    const scene = ctx.scene;
    if (!renderer || !scene) return;

    const cars = waterSideCars(state);

    const tier = getGpuTier(state);
    if (tier && tier.tier < MIN_GPU_TIER) {
      clearActiveReflection(cars);
      return;
    }

    // Adaptive Quality: the planar mirror is a full extra scene render per
    // frame. At Medium tier (2) and below the scaler has decided the GPU is
    // under sustained pressure, so drop the mirror — the water shader still
    // has its sky-tint flat fallback (see water/systems.ts). The mirror
    // resumes automatically when headroom returns and the tier drops back.
    const qualityTier = getAdaptiveQualityTier(state);
    const mirrorAllowed = TIER_PRESETS[qualityTier]?.waterMirror ?? true;
    if (!mirrorAllowed) {
      clearActiveReflection(cars);
      return;
    }

    const camEntities = mainCameraQuery(state.world);
    const camera =
      camEntities.length > 0 ? threeCameras.get(camEntities[0]!) : undefined;
    if (!camera || !(camera instanceof THREE.PerspectiveCamera)) {
      clearActiveReflection(cars);
      return;
    }

    let bestEntity: number | null = null;
    let bestCar: WaterSideCar | null = null;
    let bestDist = MAX_REFLECT_DISTANCE;
    for (const [eid, car] of cars) {
      const dist = distanceToBody(
        car.body,
        camera.position.x,
        camera.position.z
      );
      if (dist < bestDist) {
        bestDist = dist;
        bestEntity = eid;
        bestCar = car;
      }
    }

    if (bestEntity === null || bestCar === null) {
      clearActiveReflection(cars);
      return;
    }
    if (activeEntity !== null && activeEntity !== bestEntity) {
      clearActiveReflection(cars);
    }

    // Reuse last mirror RT when camera barely moved on the same body — saves a
    // full extra scene render on idle look / tiny footsteps.
    const camStill =
      _lastReflectEntity === bestEntity &&
      camera.position.distanceToSquared(_lastReflectCam) <
        REFLECT_CAM_STILL_EPS_SQ;
    if (camStill && activeEntity === bestEntity) {
      return;
    }
    _lastReflectCam.copy(camera.position);
    _lastReflectEntity = bestEntity;

    renderReflection(renderer, scene, camera, bestEntity, bestCar);
  },
  dispose() {
    renderTarget?.dispose();
    renderTarget = null;
    reflectionCamera = null;
    activeEntity = null;
    _lastReflectEntity = null;
    _lastReflectCam.set(Number.NaN, Number.NaN, Number.NaN);
  },
};
