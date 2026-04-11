import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import {
  ApplyForce,
  ApplyImpulse,
  Rigidbody,
  BodyType,
  Collider,
  ColliderShape,
  PhysicsPlugin,
  PhysicsWorld,
  SetLinearVelocity,
} from 'vibegame/physics';
import { Transform } from 'vibegame/transforms';

describe('Dynamic Bodies Integration', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(PhysicsPlugin);

    await state.initializePlugins();
  });

  it('should fall under gravity', () => {
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

    const initialY = Rigidbody.posY[box];

    for (let i = 0; i < 30; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posY[box]).toBeLessThan(initialY);
    expect(Rigidbody.velY[box]).toBeLessThan(0);
  });

  it('should respond to applied forces', () => {
    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Dynamic;
    Rigidbody.posX[box] = 0;
    Rigidbody.posY[box] = 5;
    Rigidbody.posZ[box] = 0;
    Rigidbody.rotW[box] = 1;
    Rigidbody.mass[box] = 1;
    Rigidbody.gravityScale[box] = 0;

    Collider.shape[box] = ColliderShape.Box;
    Collider.sizeX[box] = 1;
    Collider.sizeY[box] = 1;
    Collider.sizeZ[box] = 1;
    Collider.density[box] = 1;

    state.addComponent(box, ApplyForce);
    ApplyForce.x[box] = 10;
    ApplyForce.y[box] = 0;
    ApplyForce.z[box] = 0;

    for (let i = 0; i < 30; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posX[box]).toBeGreaterThan(0);
    expect(Rigidbody.velX[box]).toBeGreaterThan(0);
  });

  it('should respond to impulses', () => {
    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Dynamic;
    Rigidbody.posX[box] = 0;
    Rigidbody.posY[box] = 5;
    Rigidbody.posZ[box] = 0;
    Rigidbody.rotW[box] = 1;
    Rigidbody.mass[box] = 1;
    Rigidbody.gravityScale[box] = 0;

    Collider.shape[box] = ColliderShape.Box;
    Collider.sizeX[box] = 1;
    Collider.sizeY[box] = 1;
    Collider.sizeZ[box] = 1;
    Collider.density[box] = 1;

    state.addComponent(box, ApplyImpulse);
    ApplyImpulse.x[box] = 5;
    ApplyImpulse.y[box] = 10;
    ApplyImpulse.z[box] = 0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    state.removeComponent(box, ApplyImpulse);

    expect(Rigidbody.velX[box]).toBeGreaterThan(0);
    expect(Rigidbody.velY[box]).toBeGreaterThan(0);

    for (let i = 0; i < 30; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posX[box]).toBeGreaterThan(0);
    expect(Rigidbody.posY[box]).toBeGreaterThan(5);
  });

  it('should handle velocity changes', () => {
    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Dynamic;
    Rigidbody.posX[box] = 0;
    Rigidbody.posY[box] = 5;
    Rigidbody.posZ[box] = 0;
    Rigidbody.rotW[box] = 1;
    Rigidbody.mass[box] = 1;
    Rigidbody.gravityScale[box] = 0;

    Collider.shape[box] = ColliderShape.Box;
    Collider.sizeX[box] = 1;
    Collider.sizeY[box] = 1;
    Collider.sizeZ[box] = 1;
    Collider.density[box] = 1;

    state.addComponent(box, SetLinearVelocity);
    SetLinearVelocity.x[box] = 5;
    SetLinearVelocity.y[box] = 0;
    SetLinearVelocity.z[box] = 0;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(Rigidbody.velX[box]).toBe(5);
    expect(Rigidbody.velY[box]).toBe(0);
    expect(Rigidbody.velZ[box]).toBe(0);

    state.removeComponent(box, SetLinearVelocity);

    for (let i = 0; i < 30; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posX[box]).toBeGreaterThan(0);
  });

  it('should bounce with restitution', () => {
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
    Collider.restitution[floor] = 0.8;

    const ball = state.createEntity();
    state.addComponent(ball, Rigidbody);
    state.addComponent(ball, Collider);
    state.addComponent(ball, Transform);

    Transform.scaleX[ball] = 1;
    Transform.scaleY[ball] = 1;
    Transform.scaleZ[ball] = 1;

    Rigidbody.type[ball] = BodyType.Dynamic;
    Rigidbody.posY[ball] = 10;
    Rigidbody.rotW[ball] = 1;
    Rigidbody.mass[ball] = 1;
    Rigidbody.gravityScale[ball] = 1;

    Collider.shape[ball] = ColliderShape.Sphere;
    Collider.sizeX[ball] = 1;
    Collider.radius[ball] = 0.5;
    Collider.density[ball] = 1;
    Collider.restitution[ball] = 0.8;

    const worldEntities = defineQuery([PhysicsWorld])(state.world);
    if (worldEntities.length > 0) {
      PhysicsWorld.gravityY[worldEntities[0]] = -9.81;
    }

    let bounceDetected = false;
    let previousVelY = 0;

    for (let i = 0; i < 120; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      if (i > 30 && Rigidbody.velY[ball] > 0 && previousVelY < 0) {
        bounceDetected = true;
        break;
      }
      previousVelY = Rigidbody.velY[ball];
    }

    expect(bounceDetected).toBe(true);
  });

  it('should handle linear damping', () => {
    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Dynamic;
    Rigidbody.posY[box] = 5;
    Rigidbody.rotW[box] = 1;
    Rigidbody.mass[box] = 1;
    Rigidbody.gravityScale[box] = 0;
    Rigidbody.linearDamping[box] = 2;
    Rigidbody.velX[box] = 10;

    Collider.shape[box] = ColliderShape.Box;
    Collider.sizeX[box] = 1;
    Collider.sizeY[box] = 1;
    Collider.sizeZ[box] = 1;
    Collider.density[box] = 1;

    const initialVel = Rigidbody.velX[box];

    for (let i = 0; i < 60; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.velX[box]).toBeLessThan(initialVel);
    expect(Rigidbody.velX[box]).toBeGreaterThan(0);
  });
});
