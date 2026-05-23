import * as RAPIER from '@dimforge/rapier3d-simd-compat';

const GRAVITY_Y = -60;
export const DEFAULT_GRAVITY = GRAVITY_Y;
const TIMESTEP = 1 / 50;

let world: RAPIER.World | null = null;
let initialized = false;
let eventQueue: RAPIER.EventQueue | null = null;

export async function initPhysics(): Promise<void> {
  if (initialized) return;
  await RAPIER.init();
  initialized = true;
}

export function getOrCreateWorld(): RAPIER.World {
  if (!world) {
    world = new RAPIER.World(new RAPIER.Vector3(0, GRAVITY_Y, 0));
    world.timestep = TIMESTEP;
    eventQueue = new RAPIER.EventQueue(true);
  }
  return world;
}

export function getWorld(): RAPIER.World | null {
  return world;
}

export function getEventQueue(): RAPIER.EventQueue | null {
  return eventQueue;
}

export function stepWorld(): void {
  if (world && eventQueue) {
    try {
      world.step(eventQueue);
    } catch (e) {
      console.error('[physics] world.step() panic:', e);
    }
  }
}
