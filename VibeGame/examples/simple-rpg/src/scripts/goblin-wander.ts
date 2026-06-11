import * as THREE from 'three';
import { loadGltfToSceneWithAnimator } from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext } from 'vibegame';
import { Transform } from 'vibegame';
import { getTerrainHeightAt } from '../../../../src/plugins/terrain/systems.ts';
import { castRapierRay } from '../../../../src/plugins/raycast/utils.ts';

// Terrain-aware wander with skeletal animation: each goblin loads the rigged
// GLB and crossfades between the Animator3D walk/idle clips, with smooth
// rate-limited turns and ease-in/out speed. No physics body — pure ambience.
const MODEL_URL = '/assets/meshes/goblin_rigged_animated.glb';
const WALK_CLIP = 'Animator3D_Walk';
const IDLE_CLIP = 'Animator3D_BreatheIdle';
const WALK_SPEED = 1.7;
const WANDER_RADIUS = 14;
const WATER_LEVEL = 1.25;
const TURN_RATE = 2.8;
const ACCEL = 4.5;
const WALK_MIN = 2.5;
const WALK_MAX = 6.5;
const IDLE_MIN = 1.2;
const IDLE_MAX = 3.5;

interface Wander {
  heading: number;
  targetHeading: number;
  speed: number;
  walking: boolean;
  stateTimer: number;
  originX: number;
  originZ: number;
  // Distance from the entity origin down to the model's lowest point — grounds
  // the visible feet.
  footOffset: number;
  ready: boolean;
  group: THREE.Group | null;
  animator: GltfAnimator | null;
  playing: string;
}

const state = new Map<number, Wander>();
const _box = new THREE.Box3();

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function wrapAngle(a: number): number {
  return Math.atan2(Math.sin(a), Math.cos(a));
}

const _downDir = { x: 0, y: -1, z: 0 };

function groundHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  const origin = { x, y: fromY + 60, z };
  const hit = castRapierRay(ctx.state, origin, _downDir, 2000, 0xffff);
  if (hit) return hit.point.y;
  const hm = getTerrainHeightAt(ctx.state, x, z);
  if (Number.isFinite(hm)) return hm;
  return 0;
}

// Feet spread sideways from the entity centre, so on slopes a single centre
// sample buries the downhill foot. Sample a small footprint and stand on the
// highest point.
const FOOT_RADIUS = 0.22;

function footprintHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  let best = groundHeight(ctx, x, z, fromY);
  if (!Number.isFinite(best)) return best;
  for (const [ox, oz] of [
    [FOOT_RADIUS, 0],
    [-FOOT_RADIUS, 0],
    [0, FOOT_RADIUS],
    [0, -FOOT_RADIUS],
  ]) {
    const h = groundHeight(ctx, x + ox, z + oz, fromY);
    if (Number.isFinite(h) && h > best) best = h;
  }
  return best;
}

export function start(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const heading = rand(0, Math.PI * 2);
  const w: Wander = {
    heading,
    targetHeading: heading,
    speed: 0,
    walking: true,
    stateTimer: rand(WALK_MIN, WALK_MAX),
    originX: 0,
    originZ: 0,
    footOffset: 0,
    ready: false,
    group: null,
    animator: null,
    playing: '',
  };
  state.set(eid, w);

  void loadGltfToSceneWithAnimator(ctx.state, MODEL_URL, {
    crossfadeDuration: 0.25,
  }).then((result) => {
    // Entity may have been destroyed while the GLB was loading.
    if (state.get(eid) !== w) {
      result.group.removeFromParent();
      return;
    }
    w.group = result.group;
    w.animator = result.animator;
    w.group.updateWorldMatrix(true, true);
    _box.setFromObject(w.group);
    w.footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
  });
}

export function onDestroy(ctx: MonoBehaviourContext): void {
  const w = state.get(ctx.entity);
  w?.group?.removeFromParent();
  state.delete(ctx.entity);
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const w = state.get(eid);
  if (!w || !w.group) return;

  w.animator?.update(ctx.deltaTime);

  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];

  // Anchor home once the terrain under the spawn point is ready.
  if (!w.ready) {
    const gy = groundHeight(ctx, x, z, 500);
    if (!Number.isFinite(gy) || gy === 0) return; // terrain not decoded yet
    w.originX = x;
    w.originZ = z;
    w.ready = true;
  }

  const dt = ctx.deltaTime;
  w.stateTimer -= dt;

  // --- Behaviour state: alternate walking with idle look-around pauses ---
  if (w.stateTimer <= 0) {
    w.walking = !w.walking;
    if (w.walking) {
      w.stateTimer = rand(WALK_MIN, WALK_MAX);
      w.targetHeading = w.heading + rand(-Math.PI * 0.7, Math.PI * 0.7);
    } else {
      w.stateTimer = rand(IDLE_MIN, IDLE_MAX);
    }
  }
  // While idling, occasionally glance to a new direction.
  if (!w.walking && Math.random() < dt * 0.8) {
    w.targetHeading = w.heading + rand(-Math.PI * 0.4, Math.PI * 0.4);
  }

  // Pull the heading back toward home when straying too far.
  const homeDx = w.originX - x;
  const homeDz = w.originZ - z;
  if (homeDx * homeDx + homeDz * homeDz > WANDER_RADIUS * WANDER_RADIUS) {
    w.targetHeading = Math.atan2(homeDx, homeDz);
  }

  // --- Smooth, rate-limited turning (idle glances turn slower) ---
  const turnRate = w.walking ? TURN_RATE : TURN_RATE * 0.5;
  const err = wrapAngle(w.targetHeading - w.heading);
  const maxTurn = turnRate * dt;
  w.heading = wrapAngle(w.heading + Math.min(maxTurn, Math.max(-maxTurn, err)));

  // --- Speed ease-in/out ---
  const targetSpeed = w.walking ? WALK_SPEED : 0;
  if (w.speed < targetSpeed)
    w.speed = Math.min(targetSpeed, w.speed + ACCEL * dt);
  else if (w.speed > targetSpeed)
    w.speed = Math.max(targetSpeed, w.speed - ACCEL * dt);

  let nx = x;
  let nz = z;
  if (w.speed > 0.001) {
    nx = x + Math.sin(w.heading) * w.speed * dt;
    nz = z + Math.cos(w.heading) * w.speed * dt;
    const aheadY = groundHeight(ctx, nx, nz, Transform.posY[eid]);
    // Don't wade into the sea — turn around at the shoreline.
    if (!Number.isFinite(aheadY) || aheadY < WATER_LEVEL) {
      w.targetHeading = wrapAngle(w.heading + Math.PI);
      nx = x;
      nz = z;
    }
  }

  const groundY = footprintHeight(ctx, nx, nz, Transform.posY[eid]);
  if (!Number.isFinite(groundY)) return;

  // --- Skeletal animation: walk while moving, breathe-idle while paused ---
  const clip = w.speed > 0.15 ? WALK_CLIP : IDLE_CLIP;
  if (w.animator && w.playing !== clip) {
    w.animator.play(clip);
    w.playing = clip;
  }

  Transform.posX[eid] = nx;
  Transform.posY[eid] = groundY + w.footOffset;
  Transform.posZ[eid] = nz;
  Transform.eulerY[eid] = w.heading * (180 / Math.PI);
  Transform.dirty[eid] = 1;

  w.group.position.set(nx, groundY + w.footOffset, nz);
  w.group.rotation.set(0, w.heading, 0);
}
