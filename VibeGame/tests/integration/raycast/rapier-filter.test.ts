import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS } from 'vibegame';
import {
  BodyType,
  Collider,
  ColliderShape,
  PhysicsPlugin,
  Rigidbody,
} from 'vibegame/physics';
import { Transform } from 'vibegame/transforms';
import { castRapierRay } from '../../../src/plugins/raycast/utils';

describe('castRapierRay layer filtering', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(PhysicsPlugin);
    await state.initializePlugins();
  });

  function makeFixedBox(y: number, membership = 0): number {
    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Fixed;
    Rigidbody.posY[box] = y;
    Rigidbody.rotW[box] = 1;

    Collider.shape[box] = ColliderShape.Box;
    Collider.sizeX[box] = 1;
    Collider.sizeY[box] = 1;
    Collider.sizeZ[box] = 1;
    Collider.density[box] = 1;
    Collider.membershipGroups[box] = membership;

    return box;
  }

  const origin = { x: 0, y: 5, z: 0 };
  const down = { x: 0, y: -1, z: 0 };

  it('hits a collider whose membership overlaps the layer mask', () => {
    const box = makeFixedBox(0, 0x0002);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const hit = castRapierRay(state, origin, down, 50, 0x0002);
    expect(hit).not.toBeNull();
    expect(hit!.entity).toBe(box);
    // Box top sits at y = 0.5, origin at y = 5.
    expect(hit!.toi).toBeCloseTo(4.5, 3);
    expect(hit!.normal.y).toBeCloseTo(1, 3);
  });

  it('skips a collider whose membership does not overlap the layer mask', () => {
    makeFixedBox(0, 0x0002);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const hit = castRapierRay(state, origin, down, 50, 0x0004);
    expect(hit).toBeNull();
  });

  it('treats unset membership (0) as all layers', () => {
    const box = makeFixedBox(0, 0);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const hit = castRapierRay(state, origin, down, 50, 0xffff);
    expect(hit).not.toBeNull();
    expect(hit!.entity).toBe(box);
  });

  it('returns the closest of several layered colliders', () => {
    makeFixedBox(-3, 0x0001);
    const upper = makeFixedBox(0, 0x0001);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const hit = castRapierRay(state, origin, down, 50, 0x0001);
    expect(hit).not.toBeNull();
    expect(hit!.entity).toBe(upper);
  });
});
