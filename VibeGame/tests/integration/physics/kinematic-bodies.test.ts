import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS } from 'vibegame';
import {
  Rigidbody,
  BodyType,
  Collider,
  ColliderShape,
  KinematicMove,
  KinematicRotate,
  PhysicsPlugin,
} from 'vibegame/physics';
import { Transform } from 'vibegame/transforms';

describe('Kinematic Bodies Integration', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(PhysicsPlugin);

    await state.initializePlugins();
  });

  it('should move kinematic bodies to target positions', () => {
    const platform = state.createEntity();
    state.addComponent(platform, Rigidbody);
    state.addComponent(platform, Collider);
    state.addComponent(platform, Transform);

    Transform.scaleX[platform] = 1;
    Transform.scaleY[platform] = 1;
    Transform.scaleZ[platform] = 1;

    Rigidbody.type[platform] = BodyType.KinematicPositionBased;
    Rigidbody.posX[platform] = 0;
    Rigidbody.posY[platform] = 0;
    Rigidbody.posZ[platform] = 0;
    Rigidbody.rotW[platform] = 1;

    Collider.shape[platform] = ColliderShape.Box;
    Collider.sizeX[platform] = 4;
    Collider.sizeY[platform] = 1;
    Collider.sizeZ[platform] = 4;

    state.addComponent(platform, KinematicMove);
    KinematicMove.x[platform] = 5;
    KinematicMove.y[platform] = 3;
    KinematicMove.z[platform] = -2;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(Rigidbody.posX[platform]).toBeCloseTo(5);
    expect(Rigidbody.posY[platform]).toBeCloseTo(3);
    expect(Rigidbody.posZ[platform]).toBeCloseTo(-2);

    state.removeComponent(platform, KinematicMove);
  });

  it('should rotate kinematic bodies', () => {
    const platform = state.createEntity();
    state.addComponent(platform, Rigidbody);
    state.addComponent(platform, Collider);
    state.addComponent(platform, Transform);

    Transform.scaleX[platform] = 1;
    Transform.scaleY[platform] = 1;
    Transform.scaleZ[platform] = 1;

    Rigidbody.type[platform] = BodyType.KinematicPositionBased;
    Rigidbody.posX[platform] = 0;
    Rigidbody.posY[platform] = 0;
    Rigidbody.posZ[platform] = 0;
    Rigidbody.rotX[platform] = 0;
    Rigidbody.rotY[platform] = 0;
    Rigidbody.rotZ[platform] = 0;
    Rigidbody.rotW[platform] = 1;

    Collider.shape[platform] = ColliderShape.Box;
    Collider.sizeX[platform] = 4;
    Collider.sizeY[platform] = 1;
    Collider.sizeZ[platform] = 4;

    state.addComponent(platform, KinematicRotate);
    KinematicRotate.x[platform] = 0;
    KinematicRotate.y[platform] = 0.707;
    KinematicRotate.z[platform] = 0;
    KinematicRotate.w[platform] = 0.707;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(Rigidbody.rotY[platform]).toBeCloseTo(0.707, 2);
    expect(Rigidbody.rotW[platform]).toBeCloseTo(0.707, 2);

    state.removeComponent(platform, KinematicRotate);
  });

  it('should push dynamic bodies with kinematic movement', () => {
    const platform = state.createEntity();
    state.addComponent(platform, Rigidbody);
    state.addComponent(platform, Collider);
    state.addComponent(platform, Transform);

    Transform.scaleX[platform] = 1;
    Transform.scaleY[platform] = 1;
    Transform.scaleZ[platform] = 1;

    Rigidbody.type[platform] = BodyType.KinematicPositionBased;
    Rigidbody.posX[platform] = 0;
    Rigidbody.posY[platform] = 0;
    Rigidbody.posZ[platform] = 0;
    Rigidbody.rotW[platform] = 1;

    Collider.shape[platform] = ColliderShape.Box;
    Collider.sizeX[platform] = 4;
    Collider.sizeY[platform] = 1;
    Collider.sizeZ[platform] = 4;

    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Dynamic;
    Rigidbody.posX[box] = 0;
    Rigidbody.posY[box] = 1;
    Rigidbody.posZ[box] = 0;
    Rigidbody.rotW[box] = 1;
    Rigidbody.mass[box] = 1;
    Rigidbody.gravityScale[box] = 0;

    Collider.shape[box] = ColliderShape.Box;
    Collider.sizeX[box] = 1;
    Collider.sizeY[box] = 1;
    Collider.sizeZ[box] = 1;
    Collider.density[box] = 1;

    const initialBoxY = Rigidbody.posY[box];

    state.addComponent(platform, KinematicMove);
    KinematicMove.x[platform] = 0;
    KinematicMove.y[platform] = 2;
    KinematicMove.z[platform] = 0;

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posY[platform]).toBeCloseTo(2);
    expect(Rigidbody.posY[box]).toBeGreaterThan(initialBoxY);
  });

  it('should handle velocity-based kinematic bodies', () => {
    const platform = state.createEntity();
    state.addComponent(platform, Rigidbody);
    state.addComponent(platform, Collider);
    state.addComponent(platform, Transform);

    Transform.scaleX[platform] = 1;
    Transform.scaleY[platform] = 1;
    Transform.scaleZ[platform] = 1;

    Rigidbody.type[platform] = BodyType.KinematicVelocityBased;
    Rigidbody.posX[platform] = 0;
    Rigidbody.posY[platform] = 0;
    Rigidbody.posZ[platform] = 0;
    Rigidbody.rotW[platform] = 1;
    Rigidbody.velX[platform] = 5;
    Rigidbody.velY[platform] = 0;
    Rigidbody.velZ[platform] = 0;

    Collider.shape[platform] = ColliderShape.Box;
    Collider.sizeX[platform] = 4;
    Collider.sizeY[platform] = 1;
    Collider.sizeZ[platform] = 4;

    const initialX = Rigidbody.posX[platform];

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posX[platform]).toBeGreaterThan(initialX);
    expect(Rigidbody.velX[platform]).toBe(5);
  });

  it('should maintain position when no movement is applied', () => {
    const platform = state.createEntity();
    state.addComponent(platform, Rigidbody);
    state.addComponent(platform, Collider);
    state.addComponent(platform, Transform);

    Transform.scaleX[platform] = 1;
    Transform.scaleY[platform] = 1;
    Transform.scaleZ[platform] = 1;

    Rigidbody.type[platform] = BodyType.KinematicPositionBased;
    Rigidbody.posX[platform] = 5;
    Rigidbody.posY[platform] = 10;
    Rigidbody.posZ[platform] = -3;
    Rigidbody.rotW[platform] = 1;

    Collider.shape[platform] = ColliderShape.Box;
    Collider.sizeX[platform] = 4;
    Collider.sizeY[platform] = 1;
    Collider.sizeZ[platform] = 4;

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Rigidbody.posX[platform]).toBe(5);
    expect(Rigidbody.posY[platform]).toBe(10);
    expect(Rigidbody.posZ[platform]).toBe(-3);
  });
});
