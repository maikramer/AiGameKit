import * as THREE from 'three';
import type { MonoBehaviourContext } from 'vibegame';
import { Transform } from 'vibegame';
import { getTerrainHeightAt } from '../../../../src/plugins/terrain/systems.ts';
import { getBvhSurfaceHeight } from '../../../../src/plugins/bvh/utils.ts';
import { getGltfRootGroup } from '../../../../src/plugins/gltf-xml/group-registry.ts';

// Terrain-aware wander with organic motion: smooth rate-limited turns,
// walk/idle cycles, ease-in/out speed, and a procedural step bob + waddle so
// the static goblin GLB reads as alive. No physics body — pure ambience.
const WALK_SPEED = 1.7;
const WANDER_RADIUS = 14;
const WATER_LEVEL = 1.25;
const TURN_RATE = 2.8; // rad/s — max heading change while walking
const ACCEL = 4.5; // m/s² — speed ease toward target
const WALK_MIN = 2.5;
const WALK_MAX = 6.5;
const IDLE_MIN = 1.2;
const IDLE_MAX = 3.5;
const BOB_FREQ = 8.5; // step cycle rad/s at full speed
const BOB_AMP = 0.06;
const WADDLE_AMP_DEG = 7;
const RAD2DEG = 180 / Math.PI;
// BVH layer 0x0001 = terrain only — never land on tree canopies or rocks.
const TERRAIN_LAYER = 0x0001;

interface Wander {
  heading: number;
  targetHeading: number;
  speed: number;
  walking: boolean;
  stateTimer: number;
  phase: number; // step-cycle phase accumulator
  originX: number;
  originZ: number;
  // Distance from the entity origin down to the model's lowest point — the LOD
  // GLBs are centre-origin, so this grounds the visible feet.
  footOffset: number;
  ready: boolean;
}

const state = new Map<number, Wander>();
const _box = new THREE.Box3();

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function wrapAngle(a: number): number {
  return Math.atan2(Math.sin(a), Math.cos(a));
}

function groundHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  return (
    getBvhSurfaceHeight(ctx.state, x, fromY + 60, z, 2000, TERRAIN_LAYER) ??
    getTerrainHeightAt(ctx.state, x, z)
  );
}

/** Feet offset from the loaded GLB bbox, or null until the mesh is ready. */
function measureFootOffset(ctx: MonoBehaviourContext): number | null {
  const group = getGltfRootGroup(ctx.state, ctx.entity);
  if (!group) return null;
  group.updateWorldMatrix(true, true);
  _box.setFromObject(group);
  if (!Number.isFinite(_box.min.y)) return null;
  return Transform.posY[ctx.entity] - _box.min.y;
}

export function start(ctx: MonoBehaviourContext): void {
  const heading = rand(0, Math.PI * 2);
  state.set(ctx.entity, {
    heading,
    targetHeading: heading,
    speed: 0,
    walking: true,
    stateTimer: rand(WALK_MIN, WALK_MAX),
    phase: rand(0, Math.PI * 2),
    originX: 0,
    originZ: 0,
    footOffset: 0,
    ready: false,
  });
}

export function update(ctx: MonoBehaviourContext): void {
  const eid = ctx.entity;
  const w = state.get(eid);
  if (!w) return;

  const x = Transform.posX[eid];
  const z = Transform.posZ[eid];

  // Anchor home + measure the foot offset once terrain and the GLB are ready.
  if (!w.ready) {
    const gy = groundHeight(ctx, x, z, 500);
    if (!Number.isFinite(gy) || gy === 0) return; // terrain not decoded yet
    const foot = measureFootOffset(ctx);
    if (foot === null) return; // GLB not loaded yet
    w.footOffset = foot;
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
  if (w.speed < targetSpeed) w.speed = Math.min(targetSpeed, w.speed + ACCEL * dt);
  else if (w.speed > targetSpeed) w.speed = Math.max(targetSpeed, w.speed - ACCEL * dt);

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

  const groundY = groundHeight(ctx, nx, nz, Transform.posY[eid]);
  if (!Number.isFinite(groundY)) return;

  // --- Procedural gait: step bob + waddle roll scaled by speed ---
  const speedFactor = w.speed / WALK_SPEED;
  w.phase += dt * BOB_FREQ * (0.3 + 0.7 * speedFactor);
  const bob = Math.abs(Math.sin(w.phase)) * BOB_AMP * speedFactor;
  const waddle = Math.sin(w.phase) * WADDLE_AMP_DEG * speedFactor;
  // Idle breathing: subtle vertical pulse instead of step bob.
  const breathe = w.walking ? 0 : Math.sin(w.phase * 0.35) * 0.015;

  Transform.posX[eid] = nx;
  Transform.posY[eid] = groundY + w.footOffset + bob + breathe;
  Transform.posZ[eid] = nz;
  Transform.eulerY[eid] = w.heading * RAD2DEG;
  Transform.eulerZ[eid] = waddle;
  Transform.dirty[eid] = 1;
}
