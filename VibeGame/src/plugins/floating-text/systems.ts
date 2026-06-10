import * as THREE from 'three';
import { Text } from 'troika-three-text';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getScene, threeCameras, MainCamera } from '../rendering';
import { CameraSyncSystem } from '../rendering/systems';
import { Transform } from '../transforms';
import { FloatingText } from './components';
import { deleteFloatingTextString, getFloatingTextString } from './utils';

const textQuery = defineQuery([FloatingText]);
const cameraQuery = defineQuery([MainCamera]);

/** The camera the renderer actually draws with — same pick as SceneRenderSystem. */
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
  // Black outline keeps the text readable against any backdrop.
  obj.outlineWidth = '6%';
  obj.outlineColor = 0x000000;
  // Render late and bias depth so nearby props don't slice the glyphs.
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
 * Creates/updates troika `Text` meshes for `FloatingText` entities: billboards
 * them to the active camera, drifts them upward, fades the second half of
 * their lifetime, then destroys the entity.
 */
export const FloatingTextUpdateSystem: System = {
  group: 'draw',
  // Billboard with THIS frame's camera pose, not last frame's.
  after: [CameraSyncSystem],

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const objects = getTextObjects(state);
    const dt = state.time.deltaTime;
    const camera = getActiveCamera(state);

    for (const entity of textQuery(state.world)) {
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
      // Billboard via the WORLD quaternion — the engine drives the camera
      // through its matrix, so the local .quaternion can be stale/identity
      // and the text would face a fixed direction instead of the viewer.
      if (camera) {
        camera.getWorldQuaternion(_camQuat);
        obj.quaternion.copy(_camQuat);
      }

      // Hold full opacity for the first half, then fade linearly.
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
