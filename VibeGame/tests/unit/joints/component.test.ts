import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { PhysicsJoint } from '../../../src/plugins/joints/components';

const JOINT_FIELDS = [
  'bodyA',
  'bodyB',
  'jointType',
  'anchorAX',
  'anchorAY',
  'anchorAZ',
  'anchorBX',
  'anchorBY',
  'anchorBZ',
  'axisX',
  'axisY',
  'axisZ',
  'limitsMin',
  'limitsMax',
  'motorSpeed',
  'motorMaxForce',
  'ropeLength',
  'springStiffness',
  'springDamping',
  'created',
] as const;

describe('PhysicsJoint Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 20 fields defined', () => {
    for (const field of JOINT_FIELDS) {
      expect(PhysicsJoint[field]).toBeDefined();
      expect(typeof PhysicsJoint[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, PhysicsJoint);

    for (const field of JOINT_FIELDS) {
      expect(PhysicsJoint[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading body references', () => {
    state.addComponent(entity, PhysicsJoint);
    PhysicsJoint.bodyA[entity] = 5;
    PhysicsJoint.bodyB[entity] = 10;
    expect(PhysicsJoint.bodyA[entity]).toBe(5);
    expect(PhysicsJoint.bodyB[entity]).toBe(10);
  });

  it('should allow writing and reading jointType', () => {
    state.addComponent(entity, PhysicsJoint);
    PhysicsJoint.jointType[entity] = 1;
    expect(PhysicsJoint.jointType[entity]).toBe(1);
  });

  it('should allow writing and reading anchor positions', () => {
    state.addComponent(entity, PhysicsJoint);
    PhysicsJoint.anchorAX[entity] = 0.5;
    PhysicsJoint.anchorAY[entity] = 1.0;
    PhysicsJoint.anchorAZ[entity] = -0.5;
    PhysicsJoint.anchorBX[entity] = 0.1;
    PhysicsJoint.anchorBY[entity] = 0.2;
    PhysicsJoint.anchorBZ[entity] = 0.3;
    expect(PhysicsJoint.anchorAX[entity]).toBeCloseTo(0.5);
    expect(PhysicsJoint.anchorAY[entity]).toBeCloseTo(1.0);
    expect(PhysicsJoint.anchorAZ[entity]).toBeCloseTo(-0.5);
    expect(PhysicsJoint.anchorBX[entity]).toBeCloseTo(0.1);
    expect(PhysicsJoint.anchorBY[entity]).toBeCloseTo(0.2);
    expect(PhysicsJoint.anchorBZ[entity]).toBeCloseTo(0.3);
  });

  it('should allow writing and reading axis', () => {
    state.addComponent(entity, PhysicsJoint);
    PhysicsJoint.axisX[entity] = 0;
    PhysicsJoint.axisY[entity] = 1;
    PhysicsJoint.axisZ[entity] = 0;
    expect(PhysicsJoint.axisX[entity]).toBe(0);
    expect(PhysicsJoint.axisY[entity]).toBe(1);
    expect(PhysicsJoint.axisZ[entity]).toBe(0);
  });

  it('should allow writing and reading limits', () => {
    state.addComponent(entity, PhysicsJoint);
    PhysicsJoint.limitsMin[entity] = -1.57;
    PhysicsJoint.limitsMax[entity] = 1.57;
    expect(PhysicsJoint.limitsMin[entity]).toBeCloseTo(-1.57);
    expect(PhysicsJoint.limitsMax[entity]).toBeCloseTo(1.57);
  });

  it('should allow writing and reading motor fields', () => {
    state.addComponent(entity, PhysicsJoint);
    PhysicsJoint.motorSpeed[entity] = 2.5;
    PhysicsJoint.motorMaxForce[entity] = 100.0;
    expect(PhysicsJoint.motorSpeed[entity]).toBeCloseTo(2.5);
    expect(PhysicsJoint.motorMaxForce[entity]).toBeCloseTo(100.0);
  });

  it('should allow writing and reading spring and rope fields', () => {
    state.addComponent(entity, PhysicsJoint);
    PhysicsJoint.ropeLength[entity] = 5.0;
    PhysicsJoint.springStiffness[entity] = 10.0;
    PhysicsJoint.springDamping[entity] = 1.0;
    expect(PhysicsJoint.ropeLength[entity]).toBeCloseTo(5.0);
    expect(PhysicsJoint.springStiffness[entity]).toBeCloseTo(10.0);
    expect(PhysicsJoint.springDamping[entity]).toBeCloseTo(1.0);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, PhysicsJoint);
    const entity2 = state.createEntity();
    state.addComponent(entity2, PhysicsJoint);

    PhysicsJoint.jointType[entity] = 1;
    PhysicsJoint.jointType[entity2] = 3;
    PhysicsJoint.motorSpeed[entity] = 1.5;
    PhysicsJoint.motorSpeed[entity2] = 3.0;

    expect(PhysicsJoint.jointType[entity]).toBe(1);
    expect(PhysicsJoint.jointType[entity2]).toBe(3);
    expect(PhysicsJoint.motorSpeed[entity]).toBeCloseTo(1.5);
    expect(PhysicsJoint.motorSpeed[entity2]).toBeCloseTo(3.0);
  });
});
