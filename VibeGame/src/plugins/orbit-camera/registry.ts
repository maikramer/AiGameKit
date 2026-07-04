import type CameraControls from 'camera-controls';
import type { State } from '../../core';

/**
 * Per-State map of orbit-camera entity id → its CameraControls instance.
 *
 * The instances are constructed lazily by {@link getOrCreateCameraControls}
 * once the rendering plugin has built the THREE.Camera for the entity, and
 * disposed (via {@link removeCameraControls}) when the entity is destroyed.
 */
const controlsByState = new WeakMap<State, Map<number, CameraControls>>();

function mapFor(state: State): Map<number, CameraControls> {
  let m = controlsByState.get(state);
  if (!m) {
    m = new Map();
    controlsByState.set(state, m);
  }
  return m;
}

export function getCameraControls(
  state: State,
  eid: number
): CameraControls | undefined {
  return controlsByState.get(state)?.get(eid);
}

export function setCameraControls(
  state: State,
  eid: number,
  controls: CameraControls
): void {
  mapFor(state).set(eid, controls);
}

export function removeCameraControls(state: State, eid: number): void {
  const m = controlsByState.get(state);
  if (!m) return;
  const controls = m.get(eid);
  if (controls) {
    controls.dispose();
    m.delete(eid);
  }
}
