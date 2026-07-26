import { beforeEach, describe, expect, it } from 'bun:test';
import {
  applyCrowdAgentToEntity,
  CCT_RESYNC_XZ,
  CCT_RESYNC_Y,
  NavMeshAgent,
  NavMeshPlugin,
  needsCrowdResync,
  State,
} from 'vibegame';
import {
  CharacterController,
  CharacterMovement,
  PhysicsPlugin,
  Rigidbody,
} from 'vibegame/physics';
import { Transform, TransformsPlugin } from 'vibegame/transforms';

describe('navmesh CCT bridge', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(PhysicsPlugin);
    state.registerPlugin(NavMeshPlugin);
    await state.initializePlugins();
  });

  it('with CharacterController writes desiredVel and does not move Transform XZ', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, CharacterController);
    state.addComponent(eid, NavMeshAgent);
    Transform.posX[eid] = 1;
    Transform.posY[eid] = 2;
    Transform.posZ[eid] = 3;
    NavMeshAgent.faceVelocity[eid] = 0;
    NavMeshAgent.suspended[eid] = 0;

    const result = applyCrowdAgentToEntity(state, eid, {
      posX: 10,
      posY: 20,
      posZ: 30,
      velX: 1.5,
      velY: 0,
      velZ: -0.5,
    });

    expect(result.wroteTransformXZ).toBe(false);
    expect(Transform.posX[eid]).toBeCloseTo(1);
    expect(Transform.posZ[eid]).toBeCloseTo(3);
    expect(state.hasComponent(eid, CharacterMovement)).toBe(true);
    expect(CharacterMovement.desiredVelX[eid]).toBeCloseTo(1.5);
    expect(CharacterMovement.desiredVelZ[eid]).toBeCloseTo(-0.5);
  });

  it('without CharacterController writes Transform XZ from crowd pose', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, NavMeshAgent);
    Transform.posX[eid] = 1;
    Transform.posZ[eid] = 3;
    NavMeshAgent.faceVelocity[eid] = 0;
    NavMeshAgent.suspended[eid] = 0;

    const result = applyCrowdAgentToEntity(state, eid, {
      posX: 10,
      posY: 20,
      posZ: 30,
      velX: 0,
      velY: 0,
      velZ: 0,
    });

    expect(result.wroteTransformXZ).toBe(true);
    expect(Transform.posX[eid]).toBeCloseTo(10);
    expect(Transform.posZ[eid]).toBeCloseTo(30);
  });

  it('faceVelocity on CCT also stamps Rigidbody so physics sync cannot snap yaw', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, CharacterController);
    state.addComponent(eid, Rigidbody);
    state.addComponent(eid, NavMeshAgent);
    Rigidbody.eulerY[eid] = 0;
    Rigidbody.rotW[eid] = 1;
    NavMeshAgent.faceVelocity[eid] = 1;
    NavMeshAgent.suspended[eid] = 0;

    applyCrowdAgentToEntity(state, eid, {
      posX: 0,
      posY: 0,
      posZ: 0,
      velX: 1,
      velY: 0,
      velZ: 0,
    });

    expect(Rigidbody.poseDirty[eid]).toBe(1);
    expect(Rigidbody.eulerY[eid]).toBeCloseTo(Transform.eulerY[eid], 4);
    expect(Rigidbody.rotY[eid]).toBeCloseTo(Transform.rotY[eid], 4);
  });

  it('suspended CCT zeroes desiredVel and skips Transform write', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, CharacterController);
    state.addComponent(eid, CharacterMovement);
    state.addComponent(eid, NavMeshAgent);
    Transform.posX[eid] = 1;
    Transform.posZ[eid] = 3;
    CharacterMovement.desiredVelX[eid] = 9;
    CharacterMovement.desiredVelZ[eid] = 9;
    NavMeshAgent.suspended[eid] = 1;

    const result = applyCrowdAgentToEntity(state, eid, {
      posX: 10,
      posY: 20,
      posZ: 30,
      velX: 2,
      velY: 0,
      velZ: 2,
    });

    expect(result.wroteTransformXZ).toBe(false);
    expect(Transform.posX[eid]).toBeCloseTo(1);
    expect(Transform.posZ[eid]).toBeCloseTo(3);
    expect(CharacterMovement.desiredVelX[eid]).toBe(0);
    expect(CharacterMovement.desiredVelZ[eid]).toBe(0);
  });
});

describe('needsCrowdResync', () => {
  it('does not resync while the CCT tracks the crowd agent', () => {
    expect(needsCrowdResync(0, 0, 0)).toBe(false);
    expect(needsCrowdResync(0.2, 0.1, 0.2)).toBe(false);
    expect(needsCrowdResync(0.3, 0, -0.3)).toBe(false);
  });

  it('resyncs once the horizontal drift reaches the threshold', () => {
    expect(needsCrowdResync(CCT_RESYNC_XZ, 0, 0)).toBe(true);
    expect(needsCrowdResync(0, 0, -CCT_RESYNC_XZ)).toBe(true);
    expect(needsCrowdResync(0.4, 0, 0.4)).toBe(true);
  });

  it('resyncs on a large vertical drift even when horizontally aligned', () => {
    expect(needsCrowdResync(0, CCT_RESYNC_Y, 0)).toBe(true);
    expect(needsCrowdResync(0, -CCT_RESYNC_Y, 0)).toBe(true);
    expect(needsCrowdResync(0, 1.4, 0)).toBe(false);
  });
});
