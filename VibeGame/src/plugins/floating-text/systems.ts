import * as THREE from 'three';
import { Text } from 'troika-three-text';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getScene, threeCameras, MainCamera } from '../rendering';
import { CameraSyncSystem } from '../rendering/systems';
import { Transform } from '../transforms';
import { FloatingText } from './components';
import {
  disposeScreenFloatPool,
  getScreenFloatPool,
} from './screen-pool';
import {
  deleteFloatingTextString,
  getFloatingTextString,
} from './utils';

const textQuery = defineQuery([FloatingText]);
const cameraQuery = defineQuery([MainCamera]);

function getActiveCamera(state: State): THREE.Camera | undefined {
  const cams = cameraQuery(state.world);
  return cams.length > 0 ? threeCameras.get(cams[0]) : undefined;
}

const textObjectsByState = new WeakMap<State, Map<number, Text>>();

function getTextObjects(state: State): Map<number, Text> {
  let m = textObjectsByState.get(state);
  if (!m) {
    m = new Map();
    textObjectsByState.set(state, m);
  }
  return m;
}

const _color = new THREE.Color();
const _camQuat = new THREE.Quaternion();

function createTextObject(state: State, entity: number): Text {
  const obj = new Text();
  obj.text = getFloatingTextString(state, entity) ?? '';
  obj.fontSize = FloatingText.size[entity] || 0.35;
  _color.setRGB(
    FloatingText.colorR[entity],
    FloatingText.colorG[entity],
    FloatingText.colorB[entity]
  );
  obj.color = _color.getHex();
  obj.anchorX = 'center';
  obj.anchorY = 'middle';
  obj.textAlign = 'center';
  obj.outlineWidth = '6%';
  obj.outlineColor = 0x000000;
  obj.renderOrder = 999;
  obj.depthOffset = -4;
  obj.sync();
  return obj;
}

function disposeTextObject(state: State, entity: number, obj: Text): void {
  const scene = getScene(state);
  if (scene) scene.remove(obj);
  obj.dispose();
  deleteFloatingTextString(state, entity);
}

/**
 * World-space floating text (space === 0): troika SDF glyphs billboards to
 * the active camera, drifts upward, fades the second half of its lifetime
 * and is destroyed at `duration`. Screen-space entries are skipped (handled
 * by FloatingTextScreenUpdateSystem).
 */
export const FloatingTextUpdateSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const objects = getTextObjects(state);
    const dt = state.time.deltaTime;
    const camera = getActiveCamera(state);

    for (const entity of textQuery(state.world)) {
      if (FloatingText.space[entity] === 1) continue;

      FloatingText.elapsed[entity] += dt;
      const elapsed = FloatingText.elapsed[entity];
      const duration = FloatingText.duration[entity] || 1.4;

      if (elapsed >= duration) {
        state.destroyEntity(entity);
        continue;
      }

      let obj = objects.get(entity);
      if (!obj) {
        obj = createTextObject(state, entity);
        scene.add(obj);
        objects.set(entity, obj);
      }

      obj.position.set(
        Transform.posX[entity],
        Transform.posY[entity] + FloatingText.riseSpeed[entity] * elapsed,
        Transform.posZ[entity]
      );
      if (camera) {
        camera.getWorldQuaternion(_camQuat);
        obj.quaternion.copy(_camQuat);
      }

      const opacity = Math.min(1, Math.max(0, 2 * (1 - elapsed / duration)));
      obj.fillOpacity = opacity;
      obj.outlineOpacity = opacity;
      obj.sync();
    }

    for (const [entity, obj] of objects) {
      if (state.exists(entity)) continue;
      disposeTextObject(state, entity, obj);
      objects.delete(entity);
    }
  },

  dispose(state: State) {
    const objects = getTextObjects(state);
    for (const [entity, obj] of objects) {
      disposeTextObject(state, entity, obj);
    }
    objects.clear();
    textObjectsByState.delete(state);
  },
};

/**
 * Screen-space floating text (space === 1): DOM spans recycled via the
 * ScreenFloatPool, mounted inside the HudScreenLayer. Lazy-creates the pool
 * on first use, animates rise + drift + scale-pop + fade, then releases the
 * span back to the pool when the entity is destroyed.
 */
export const FloatingTextScreenUpdateSystem: System = {
  group: 'late',

  update(state: State) {
    if (state.headless) return;
    if (typeof document === 'undefined') return;

    let pool = null;
    const dt = state.time.deltaTime;

    for (const entity of textQuery(state.world)) {
      if (FloatingText.space[entity] !== 1) continue;

      if (pool === null) {
        pool = getScreenFloatPool(state);
      }

      if (!pool.getEntry(entity)) {
        pool.applySpawn(state, entity);
      }

      FloatingText.elapsed[entity] += dt;
      pool.updateEntity(entity, FloatingText.elapsed[entity]);

      if (!pool.getEntry(entity)) {
        if (state.exists(entity)) state.destroyEntity(entity);
        deleteFloatingTextString(state, entity);
      }
    }
  },

  dispose(state: State) {
    disposeScreenFloatPool(state);
  },
};
