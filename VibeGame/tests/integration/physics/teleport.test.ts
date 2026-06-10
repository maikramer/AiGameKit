import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS } from 'vibegame';
import {
  BodyType,
  Collider,
  ColliderShape,
  PhysicsPlugin,
  Rigidbody,
} from 'vibegame/physics';
import { Transform, WorldTransform } from 'vibegame/transforms';

function makeBox(state: State, type: BodyType, y: number): number {
  const box = state.createEntity();
  state.addComponent(box, Rigidbody);
  state.addComponent(box, Collider);
  state.addComponent(box, Transform);

  Transform.scaleX[box] = 1;
  Transform.scaleY[box] = 1;
  Transform.scaleZ[box] = 1;

  Rigidbody.type[box] = type;
  Rigidbody.posY[box] = y;
  Rigidbody.rotW[box] = 1;
  Rigidbody.gravityScale[box] = type === BodyType.Dynamic ? 1 : 0;
  Rigidbody.mass[box] = 1;

  Collider.shape[box] = ColliderShape.Box;
  Collider.sizeX[box] = 1;
  Collider.sizeY[box] = 1;
  Collider.sizeZ[box] = 1;
  Collider.density[box] = 1;

  return box;
}

describe('TeleportationSystem', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(PhysicsPlugin);
    await state.initializePlugins();
  });

  it('teleports a dynamic body when Rigidbody.pos is written', () => {
    const box = makeBox(state, BodyType.Dynamic, 5);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    Rigidbody.posX[box] = 50;
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(Rigidbody.posX[box]).toBeCloseTo(50, 1);
    expect(Transform.posX[box]).toBeCloseTo(50, 1);
  });

  it('keeps Transform in sync for a teleported fixed body', () => {
    const box = makeBox(state, BodyType.Fixed, 0);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    Rigidbody.posZ[box] = -12;
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    // Fixed bodies may stay asleep (the post-step sync skips sleeping
    // bodies), so the teleport itself must propagate to the transforms.
    expect(Transform.posZ[box]).toBeCloseTo(-12, 4);
    expect(WorldTransform.posZ[box]).toBeCloseTo(-12, 4);
  });

  it('does not disturb a free-falling body (no false teleports)', () => {
    const box = makeBox(state, BodyType.Dynamic, 100);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const samples: number[] = [];
    for (let i = 0; i < 20; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      samples.push(Rigidbody.velY[box]);
    }

    // Velocity must decrease monotonically under gravity; a false teleport
    // that reset state every step would show up as a stall.
    for (let i = 1; i < samples.length; i++) {
      expect(samples[i]).toBeLessThan(samples[i - 1]);
    }
  });
});
