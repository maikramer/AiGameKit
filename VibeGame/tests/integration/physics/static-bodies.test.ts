import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import {
  Rigidbody,
  BodyType,
  Collider,
  ColliderShape,
  PhysicsPlugin,
  PhysicsWorld,
} from 'vibegame/physics';
import { Transform } from 'vibegame/transforms';

describe('Static Bodies Integration', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(PhysicsPlugin);

    await state.initializePlugins();
  });

  it('should create a static floor that does not move', () => {
    const floor = state.createEntity();
    state.addComponent(floor, Rigidbody);
    state.addComponent(floor, Collider);
    state.addComponent(floor, Transform);

    Transform.scaleX[floor] = 1;
    Transform.scaleY[floor] = 1;
    Transform.scaleZ[floor] = 1;

    Rigidbody.type[floor] = BodyType.Fixed;
    Rigidbody.posX[floor] = 0;
    Rigidbody.posY[floor] = -10;
    Rigidbody.posZ[floor] = 0;
    Rigidbody.rotW[floor] = 1;

    Collider.shape[floor] = ColliderShape.Box;
    Collider.sizeX[floor] = 100;
    Collider.sizeY[floor] = 1;
    Collider.sizeZ[floor] = 100;

    const initialY = Rigidbody.posY[floor];

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posY[floor]).toBe(initialY);
    expect(Rigidbody.posX[floor]).toBe(0);
    expect(Rigidbody.posZ[floor]).toBe(0);
  });

  it('should support dynamic bodies falling onto static bodies', () => {
    const floor = state.createEntity();
    state.addComponent(floor, Rigidbody);
    state.addComponent(floor, Collider);
    state.addComponent(floor, Transform);

    Transform.scaleX[floor] = 1;
    Transform.scaleY[floor] = 1;
    Transform.scaleZ[floor] = 1;

    Rigidbody.type[floor] = BodyType.Fixed;
    Rigidbody.posY[floor] = 0;
    Rigidbody.rotW[floor] = 1;

    Collider.shape[floor] = ColliderShape.Box;
    Collider.sizeX[floor] = 100;
    Collider.sizeY[floor] = 1;
    Collider.sizeZ[floor] = 100;

    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Dynamic;
    Rigidbody.posY[box] = 10;
    Rigidbody.rotW[box] = 1;
    Rigidbody.mass[box] = 1;
    Rigidbody.gravityScale[box] = 1;

    Collider.shape[box] = ColliderShape.Box;
    Collider.sizeX[box] = 1;
    Collider.sizeY[box] = 1;
    Collider.sizeZ[box] = 1;
    Collider.density[box] = 1;

    const worldEntities = defineQuery([PhysicsWorld])(state.world);
    if (worldEntities.length > 0) {
      PhysicsWorld.gravityY[worldEntities[0]] = -9.81;
    }

    const initialBoxY = Rigidbody.posY[box];

    for (let i = 0; i < 60; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posY[box]).toBeLessThan(initialBoxY);
    expect(Rigidbody.posY[box]).toBeGreaterThan(0);
    expect(Rigidbody.posY[floor]).toBe(0);
  });

  it('should handle multiple static bodies', () => {
    const staticBodies: number[] = [];

    for (let i = 0; i < 5; i++) {
      const wall = state.createEntity();
      state.addComponent(wall, Rigidbody);
      state.addComponent(wall, Collider);
      state.addComponent(wall, Transform);

      Transform.scaleX[wall] = 1;
      Transform.scaleY[wall] = 1;
      Transform.scaleZ[wall] = 1;

      Rigidbody.type[wall] = BodyType.Fixed;
      Rigidbody.posX[wall] = i * 10;
      Rigidbody.posY[wall] = 0;
      Rigidbody.posZ[wall] = 0;
      Rigidbody.rotW[wall] = 1;

      Collider.shape[wall] = ColliderShape.Box;
      Collider.sizeX[wall] = 2;
      Collider.sizeY[wall] = 10;
      Collider.sizeZ[wall] = 2;

      staticBodies.push(wall);
    }

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    for (let i = 0; i < staticBodies.length; i++) {
      const wall = staticBodies[i];
      expect(Rigidbody.posX[wall]).toBe(i * 10);
      expect(Rigidbody.posY[wall]).toBe(0);
      expect(Rigidbody.posZ[wall]).toBe(0);
    }
  });

  it('should not be affected by gravity', () => {
    const staticBox = state.createEntity();
    state.addComponent(staticBox, Rigidbody);
    state.addComponent(staticBox, Collider);
    state.addComponent(staticBox, Transform);

    Transform.scaleX[staticBox] = 1;
    Transform.scaleY[staticBox] = 1;
    Transform.scaleZ[staticBox] = 1;

    Rigidbody.type[staticBox] = BodyType.Fixed;
    Rigidbody.posY[staticBox] = 10;
    Rigidbody.rotW[staticBox] = 1;
    Rigidbody.gravityScale[staticBox] = 1;

    Collider.shape[staticBox] = ColliderShape.Box;
    Collider.sizeX[staticBox] = 2;
    Collider.sizeY[staticBox] = 2;
    Collider.sizeZ[staticBox] = 2;

    const worldEntities = defineQuery([PhysicsWorld])(state.world);
    if (worldEntities.length > 0) {
      PhysicsWorld.gravityY[worldEntities[0]] = -9.81;
    }

    for (let i = 0; i < 60; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posY[staticBox]).toBe(10);
  });
});
