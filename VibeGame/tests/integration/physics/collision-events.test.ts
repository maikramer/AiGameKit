import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import {
  Rigidbody,
  BodyType,
  Collider,
  ColliderShape,
  CollisionEvents,
  PhysicsPlugin,
  PhysicsWorld,
  TouchedEvent,
  TouchEndedEvent,
} from 'vibegame/physics';
import { Transform } from 'vibegame/transforms';

describe('Collision Events Integration', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(PhysicsPlugin);

    await state.initializePlugins();
  });

  it('should detect collision between dynamic bodies', () => {
    const box1 = state.createEntity();
    state.addComponent(box1, Rigidbody);
    state.addComponent(box1, Collider);
    state.addComponent(box1, CollisionEvents);
    state.addComponent(box1, Transform);

    Transform.scaleX[box1] = 1;
    Transform.scaleY[box1] = 1;
    Transform.scaleZ[box1] = 1;

    Rigidbody.type[box1] = BodyType.Dynamic;
    Rigidbody.posX[box1] = 0;
    Rigidbody.posY[box1] = 10;
    Rigidbody.posZ[box1] = 0;
    Rigidbody.rotW[box1] = 1;
    Rigidbody.mass[box1] = 1;
    Rigidbody.gravityScale[box1] = 1;

    Collider.shape[box1] = ColliderShape.Box;
    Collider.sizeX[box1] = 1;
    Collider.sizeY[box1] = 1;
    Collider.sizeZ[box1] = 1;
    Collider.density[box1] = 1;

    const box2 = state.createEntity();
    state.addComponent(box2, Rigidbody);
    state.addComponent(box2, Collider);
    state.addComponent(box2, CollisionEvents);
    state.addComponent(box2, Transform);

    Transform.scaleX[box2] = 1;
    Transform.scaleY[box2] = 1;
    Transform.scaleZ[box2] = 1;

    Rigidbody.type[box2] = BodyType.Dynamic;
    Rigidbody.posX[box2] = 0;
    Rigidbody.posY[box2] = 5;
    Rigidbody.posZ[box2] = 0;
    Rigidbody.rotW[box2] = 1;
    Rigidbody.mass[box2] = 1;
    Rigidbody.gravityScale[box2] = 0;

    Collider.shape[box2] = ColliderShape.Box;
    Collider.sizeX[box2] = 1;
    Collider.sizeY[box2] = 1;
    Collider.sizeZ[box2] = 1;
    Collider.density[box2] = 1;

    const worldEntities = defineQuery([PhysicsWorld])(state.world);
    if (worldEntities.length > 0) {
      PhysicsWorld.gravityY[worldEntities[0]] = -9.81;
    }

    let collisionDetected = false;

    for (let i = 0; i < 60; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      const touchedEvents1 = defineQuery([TouchedEvent])(state.world);
      const touchedEvents2 = defineQuery([TouchedEvent])(state.world);

      if (touchedEvents1.length > 0 || touchedEvents2.length > 0) {
        collisionDetected = true;
        break;
      }
    }

    expect(collisionDetected).toBe(true);
  });

  it('should detect collision with static bodies', () => {
    const floor = state.createEntity();
    state.addComponent(floor, Rigidbody);
    state.addComponent(floor, Collider);
    state.addComponent(floor, CollisionEvents);
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
    state.addComponent(box, CollisionEvents);
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

    let collisionDetected = false;

    for (let i = 0; i < 60; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      const touchedEvents = defineQuery([TouchedEvent])(state.world);

      if (touchedEvents.length > 0) {
        collisionDetected = true;
        break;
      }
    }

    expect(collisionDetected).toBe(true);
  });

  it('should detect sensor overlaps without physical collision', () => {
    const trigger = state.createEntity();
    state.addComponent(trigger, Rigidbody);
    state.addComponent(trigger, Collider);
    state.addComponent(trigger, CollisionEvents);
    state.addComponent(trigger, Transform);

    Transform.scaleX[trigger] = 1;
    Transform.scaleY[trigger] = 1;
    Transform.scaleZ[trigger] = 1;

    Rigidbody.type[trigger] = BodyType.Fixed;
    Rigidbody.posX[trigger] = 0;
    Rigidbody.posY[trigger] = 5;
    Rigidbody.posZ[trigger] = 0;
    Rigidbody.rotW[trigger] = 1;

    Collider.shape[trigger] = ColliderShape.Box;
    Collider.sizeX[trigger] = 2;
    Collider.sizeY[trigger] = 2;
    Collider.sizeZ[trigger] = 2;
    Collider.isSensor[trigger] = 1;

    const box = state.createEntity();
    state.addComponent(box, Rigidbody);
    state.addComponent(box, Collider);
    state.addComponent(box, CollisionEvents);
    state.addComponent(box, Transform);

    Transform.scaleX[box] = 1;
    Transform.scaleY[box] = 1;
    Transform.scaleZ[box] = 1;

    Rigidbody.type[box] = BodyType.Dynamic;
    Rigidbody.posX[box] = 0;
    Rigidbody.posY[box] = 10;
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

    let overlapDetected = false;
    const initialY = Rigidbody.posY[box];

    for (let i = 0; i < 60; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      const touchedEvents = defineQuery([TouchedEvent])(state.world);

      if (touchedEvents.length > 0 && !overlapDetected) {
        overlapDetected = true;
      }
    }

    expect(overlapDetected).toBe(true);
    expect(Rigidbody.posY[box]).toBeLessThan(initialY - 5);
  });

  it('should detect collision end events', () => {
    const box1 = state.createEntity();
    state.addComponent(box1, Rigidbody);
    state.addComponent(box1, Collider);
    state.addComponent(box1, CollisionEvents);
    state.addComponent(box1, Transform);

    Transform.scaleX[box1] = 1;
    Transform.scaleY[box1] = 1;
    Transform.scaleZ[box1] = 1;

    Rigidbody.type[box1] = BodyType.Dynamic;
    Rigidbody.posX[box1] = 0;
    Rigidbody.posY[box1] = 5;
    Rigidbody.posZ[box1] = 0;
    Rigidbody.rotW[box1] = 1;
    Rigidbody.mass[box1] = 1;
    Rigidbody.gravityScale[box1] = 0;
    Rigidbody.velX[box1] = 10;

    Collider.shape[box1] = ColliderShape.Box;
    Collider.sizeX[box1] = 1;
    Collider.sizeY[box1] = 1;
    Collider.sizeZ[box1] = 1;
    Collider.density[box1] = 1;
    Collider.restitution[box1] = 0.8;

    const box2 = state.createEntity();
    state.addComponent(box2, Rigidbody);
    state.addComponent(box2, Collider);
    state.addComponent(box2, CollisionEvents);
    state.addComponent(box2, Transform);

    Transform.scaleX[box2] = 1;
    Transform.scaleY[box2] = 1;
    Transform.scaleZ[box2] = 1;

    Rigidbody.type[box2] = BodyType.Dynamic;
    Rigidbody.posX[box2] = 1.5;
    Rigidbody.posY[box2] = 5;
    Rigidbody.posZ[box2] = 0;
    Rigidbody.rotW[box2] = 1;
    Rigidbody.mass[box2] = 1;
    Rigidbody.gravityScale[box2] = 0;
    Rigidbody.velX[box2] = -10;

    Collider.shape[box2] = ColliderShape.Box;
    Collider.sizeX[box2] = 1;
    Collider.sizeY[box2] = 1;
    Collider.sizeZ[box2] = 1;
    Collider.density[box2] = 1;
    Collider.restitution[box2] = 0.8;

    let touchStarted = false;
    let touchEnded = false;

    // Run for up to 3 seconds of simulation time to ensure collision and separation
    const maxSteps = Math.ceil(3.0 / TIME_CONSTANTS.FIXED_TIMESTEP);
    for (let i = 0; i < maxSteps; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      const touchedEvents = defineQuery([TouchedEvent])(state.world);
      const touchEndedEvents = defineQuery([TouchEndedEvent])(state.world);

      if (touchedEvents.length > 0 && !touchStarted) {
        touchStarted = true;
      }

      if (touchEndedEvents.length > 0 && touchStarted) {
        touchEnded = true;
        break;
      }
    }

    expect(touchStarted).toBe(true);
    expect(touchEnded).toBe(true);
  });
});
