import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { HeldItem, ItemBox, ItemKind, Track, Vehicle } from './components';
import { getItemBoxes, getTrackSpline } from './data';
import { ROULETTE_S } from './items';
import { WorldTransform } from '../transforms';
import { pushRacingFx } from './fx-events';

/**
 * Item boxes on the track — rune-marked chests, in the flavour of the example.
 * Collecting one starts the roulette (see `items.ts`); a kart already holding
 * an item (or still rolling one) passes straight through, so a full slot never
 * wastes a box somebody else could have cracked.
 */

const BOX_RANGE = 4.5;
/** Vertical offset (m) above the road surface so the chest floats. */
const BOX_HOVER = 1.0;

const _pos = { x: 0, y: 0, z: 0 };

const boxQuery = defineQuery([ItemBox]);
const vehicleQuery = defineQuery([Vehicle]);
const trackQuery = defineQuery([Track]);

interface BoxVisual {
  group: THREE.Group;
  rune: THREE.Sprite;
}

const visuals: BoxVisual[] = [];

let runeTexture: THREE.CanvasTexture | null = null;

function buildRuneTexture(): THREE.CanvasTexture | null {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.clearRect(0, 0, 64, 64);
  ctx.font = '900 52px Georgia, serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.shadowColor = 'rgba(255, 209, 102, 0.9)';
  ctx.shadowBlur = 14;
  ctx.fillStyle = '#ffd166';
  ctx.fillText('?', 32, 36);
  return new THREE.CanvasTexture(canvas);
}

function buildChest(): BoxVisual {
  const group = new THREE.Group();
  group.name = 'ItemChest';

  const wood = new THREE.MeshStandardMaterial({
    color: 0x6b4a2b,
    roughness: 0.8,
    metalness: 0.05,
  });
  const band = new THREE.MeshStandardMaterial({
    color: 0x2c2418,
    roughness: 0.4,
    metalness: 0.7,
  });
  const glow = new THREE.MeshStandardMaterial({
    color: 0xffd166,
    emissive: 0xffd166,
    emissiveIntensity: 1.6,
  });

  const body = new THREE.Mesh(new THREE.BoxGeometry(1.15, 0.55, 0.8), wood);
  body.position.y = 0.1;
  group.add(body);
  const lid = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.3, 0.85), wood);
  lid.position.y = 0.52;
  group.add(lid);
  for (const x of [-0.38, 0.38]) {
    const strap = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.9, 0.86), band);
    strap.position.set(x, 0.3, 0);
    group.add(strap);
  }
  const lock = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.2, 0.1), glow);
  lock.position.set(0, 0.42, 0.42);
  group.add(lock);

  if (!runeTexture) runeTexture = buildRuneTexture();
  let rune: THREE.Sprite;
  if (runeTexture) {
    rune = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: runeTexture,
        transparent: true,
        depthWrite: false,
      })
    );
  } else {
    rune = new THREE.Sprite(new THREE.SpriteMaterial({ color: 0xffd166 }));
  }
  rune.scale.setScalar(0.6);
  rune.position.y = 1.15;
  group.add(rune);

  return { group, rune };
}

/**
 * ItemBoxSystem — proximity test and respawn, in the same shape as the old
 * pickup orbs. Every vehicle can collect (the AI cracks chests too); only
 * karts with an empty slot do.
 */
export const ItemBoxSystem: System = defineSystem({
  name: 'ItemBoxSystem',
  group: 'fixed',

  update(state: State) {
    syncBoxEntities(state);
    const boxes = boxQuery(state.world);
    if (boxes.length === 0) return;
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const vehicles = vehicleQuery(state.world);
    if (vehicles.length === 0) return;
    const dt = state.time.fixedDeltaTime;

    for (const eid of boxes) {
      const ttl = ItemBox.ttl[eid] ?? 0;
      if (ttl > 0) {
        spline.positionAt(
          ItemBox.s[eid] ?? 0,
          ItemBox.lateral[eid] ?? 0,
          BOX_HOVER,
          _pos
        );
        for (const veh of vehicles) {
          const slotFree =
            (HeldItem.item[veh] ?? ItemKind.None) === ItemKind.None &&
            (HeldItem.rouletteTimer[veh] ?? 0) <= 0;
          if (!slotFree) continue;
          const dx = _pos.x - WorldTransform.posX[veh];
          const dy = _pos.y - WorldTransform.posY[veh];
          const dz = _pos.z - WorldTransform.posZ[veh];
          if (Math.hypot(dx, dy, dz) < BOX_RANGE) {
            HeldItem.rouletteTimer[veh] = ROULETTE_S;
            ItemBox.ttl[eid] = -Math.max(0.1, ItemBox.respawnAfter[eid] || 0);
            pushRacingFx({
              kind: 'box',
              x: _pos.x,
              y: _pos.y,
              z: _pos.z,
              severity: 0.5,
              eid: veh,
            });
            break;
          }
        }
      } else if (ttl < 0) {
        ItemBox.ttl[eid] = Math.min(0, ttl + dt);
        if (ItemBox.ttl[eid]! >= 0) {
          ItemBox.ttl[eid] = 1;
        }
      }
    }
  },
});

/** ItemBoxVisualSystem — chest meshes that hover, spin and bob over the road. */
export const ItemBoxVisualSystem: System = defineSystem({
  name: 'ItemBoxVisualSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state) as THREE.Scene | null;
    if (!scene) return;
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const t = state.time.elapsed;
    const boxes = getItemBoxes();

    syncVisuals(scene, boxes.length);
    for (let i = 0; i < boxes.length; i++) {
      const def = boxes[i]!;
      const v = visuals[i];
      if (!v) continue;
      const ttl = ItemBox.ttl[def.eid] ?? 0;
      const visible = ttl > 0;
      v.group.visible = visible;
      if (!visible) continue;
      const pulse = 1 + Math.sin(t * 4 + i) * 0.06;
      v.group.scale.setScalar(pulse);
      v.group.rotation.y = t * 0.8 + i;
      const hover = Math.sin(t * 2.2 + i * 0.7) * 0.15;
      spline.positionAt(def.s, def.lateral, BOX_HOVER + hover, _pos);
      v.group.position.set(_pos.x, _pos.y, _pos.z);
      v.rune.material.opacity = 0.75 + Math.sin(t * 5 + i) * 0.25;
    }
  },

  dispose() {
    for (const v of visuals) {
      v.group.parent?.remove(v.group);
      v.group.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.geometry?.dispose();
          (mesh.material as THREE.Material)?.dispose();
        }
      });
      (v.rune.material as THREE.Material).dispose();
    }
    visuals.length = 0;
    runeTexture?.dispose();
    runeTexture = null;
  },
});

function syncVisuals(scene: THREE.Scene, count: number): void {
  while (visuals.length > count) {
    const v = visuals.pop()!;
    v.group.parent?.remove(v.group);
    v.group.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (mesh.isMesh) {
        mesh.geometry?.dispose();
        (mesh.material as THREE.Material)?.dispose();
      }
    });
    (v.rune.material as THREE.Material).dispose();
  }
  while (visuals.length < count) {
    const v = buildChest();
    scene.add(v.group);
    visuals.push(v);
  }
}

/** Create ItemBox entities for any registered boxes that lack one. */
function syncBoxEntities(state: State): void {
  for (const def of getItemBoxes()) {
    if (def.eid >= 0) continue;
    const eid = state.createEntity();
    state.addComponent(eid, ItemBox);
    ItemBox.s[eid] = def.s;
    ItemBox.lateral[eid] = def.lateral;
    ItemBox.ttl[eid] = 1;
    ItemBox.respawnAfter[eid] = def.respawnAfter;
    def.eid = eid;
  }
}
