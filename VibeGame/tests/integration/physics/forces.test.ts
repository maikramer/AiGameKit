import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import {
  ApplyAngularImpulse,
  ApplyForce,
  ApplyImpulse,
  ApplyTorque,
  Rigidbody,
  BodyType,
  Collider,
  ColliderShape,
  KinematicMove,
  PhysicsPlugin,
  PhysicsWorld,
  SetAngularVelocity,
  SetLinearVelocity,
} from 'vibegame/physics';
import { Transform } from 'vibegame/transforms';

describe('Physics Forces and Impulses', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(PhysicsPlugin);

    await state.initializePlugins();
  });

  describe('Linear Forces', () => {
    it('should apply continuous force to dynamic body', () => {
      const box = state.createEntity();
      state.addComponent(box, Rigidbody);
      state.addComponent(box, Collider);
      state.addComponent(box, Transform);
      state.addComponent(box, ApplyForce);

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

      ApplyForce.x[box] = 10;
      ApplyForce.y[box] = 0;
      ApplyForce.z[box] = 0;

      const initialX = Rigidbody.posX[box];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.posX[box]).toBeGreaterThan(initialX);
      expect(Rigidbody.velX[box]).toBeGreaterThan(0);
    });

    it('should apply impulse for instant velocity change', () => {
      const box = state.createEntity();
      state.addComponent(box, Rigidbody);
      state.addComponent(box, Collider);
      state.addComponent(box, Transform);
      state.addComponent(box, ApplyImpulse);

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

      ApplyImpulse.x[box] = 5;
      ApplyImpulse.y[box] = 0;
      ApplyImpulse.z[box] = 0;

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.velX[box]).toBeGreaterThan(0);
      expect(Rigidbody.velX[box]).toBeCloseTo(5, 1);

      state.removeComponent(box, ApplyImpulse);
      const velocityAfterImpulse = Rigidbody.velX[box];

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.velX[box]).toBeCloseTo(velocityAfterImpulse, 2);
    });
  });

  describe('Angular Forces', () => {
    it('should apply torque for continuous rotation', () => {
      const box = state.createEntity();
      state.addComponent(box, Rigidbody);
      state.addComponent(box, Collider);
      state.addComponent(box, Transform);
      state.addComponent(box, ApplyTorque);

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

      ApplyTorque.x[box] = 0;
      ApplyTorque.y[box] = 10;
      ApplyTorque.z[box] = 0;

      const initialRotY = Rigidbody.rotY[box];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.rotVelY[box]).toBeGreaterThan(0);
      expect(Rigidbody.rotY[box]).not.toBeCloseTo(initialRotY, 2);
    });

    it('should apply angular impulse for instant rotation', () => {
      const box = state.createEntity();
      state.addComponent(box, Rigidbody);
      state.addComponent(box, Collider);
      state.addComponent(box, Transform);
      state.addComponent(box, ApplyAngularImpulse);

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

      ApplyAngularImpulse.x[box] = 0;
      ApplyAngularImpulse.y[box] = 5;
      ApplyAngularImpulse.z[box] = 0;

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.rotVelY[box]).toBeGreaterThan(0);

      state.removeComponent(box, ApplyAngularImpulse);
      const angularVelAfterImpulse = Rigidbody.rotVelY[box];

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.rotVelY[box]).toBeCloseTo(angularVelAfterImpulse, 2);
    });
  });

  describe('Velocity Setting', () => {
    it('should set linear velocity directly', () => {
      const box = state.createEntity();
      state.addComponent(box, Rigidbody);
      state.addComponent(box, Collider);
      state.addComponent(box, Transform);
      state.addComponent(box, SetLinearVelocity);

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

      SetLinearVelocity.x[box] = 10;
      SetLinearVelocity.y[box] = 5;
      SetLinearVelocity.z[box] = -3;

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.velX[box]).toBeCloseTo(10, 1);
      expect(Rigidbody.velY[box]).toBeCloseTo(5, 1);
      expect(Rigidbody.velZ[box]).toBeCloseTo(-3, 1);
    });

    it('should set angular velocity directly', () => {
      const box = state.createEntity();
      state.addComponent(box, Rigidbody);
      state.addComponent(box, Collider);
      state.addComponent(box, Transform);
      state.addComponent(box, SetAngularVelocity);

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

      SetAngularVelocity.x[box] = 2;
      SetAngularVelocity.y[box] = 4;
      SetAngularVelocity.z[box] = 1;

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.rotVelX[box]).toBeCloseTo(2, 1);
      expect(Rigidbody.rotVelY[box]).toBeCloseTo(4, 1);
      expect(Rigidbody.rotVelZ[box]).toBeCloseTo(1, 1);
    });
  });

  describe('Kinematic Movement', () => {
    it('should move kinematic body to target position', () => {
      const platform = state.createEntity();
      state.addComponent(platform, Rigidbody);
      state.addComponent(platform, Collider);
      state.addComponent(platform, Transform);
      state.addComponent(platform, KinematicMove);

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

      KinematicMove.x[platform] = 5;
      KinematicMove.y[platform] = 2;
      KinematicMove.z[platform] = -3;

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.posX[platform]).toBeCloseTo(5, 1);
      expect(Rigidbody.posY[platform]).toBeCloseTo(2, 1);
      expect(Rigidbody.posZ[platform]).toBeCloseTo(-3, 1);
    });

    it('should push dynamic bodies with kinematic movement', () => {
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

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      const boxInitialY = Rigidbody.posY[box];

      state.addComponent(platform, KinematicMove);
      KinematicMove.x[platform] = 0;
      KinematicMove.y[platform] = 3;
      KinematicMove.z[platform] = 0;

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Rigidbody.posY[platform]).toBeGreaterThan(0);
      expect(Rigidbody.posY[box]).toBeGreaterThan(boxInitialY);
    });
  });

  describe('Force Accumulation', () => {
    it('should combine multiple forces correctly', () => {
      const box = state.createEntity();
      state.addComponent(box, Rigidbody);
      state.addComponent(box, Collider);
      state.addComponent(box, Transform);
      state.addComponent(box, ApplyForce);

      Transform.scaleX[box] = 1;
      Transform.scaleY[box] = 1;
      Transform.scaleZ[box] = 1;

      Rigidbody.type[box] = BodyType.Dynamic;
      Rigidbody.posX[box] = 0;
      Rigidbody.posY[box] = 5;
      Rigidbody.posZ[box] = 0;
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

      ApplyForce.x[box] = 10;
      ApplyForce.y[box] = 20;
      ApplyForce.z[box] = 0;

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.velX[box]).toBeGreaterThan(0);
      expect(Rigidbody.posX[box]).toBeGreaterThan(0);
    });
  });
});
