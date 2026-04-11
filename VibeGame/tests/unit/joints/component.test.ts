import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { Joint } from '../../../src/plugins/joints/components';

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
      expect(Joint[field]).toBeDefined();
      expect(typeof Joint[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, Joint);

    for (const field of JOINT_FIELDS) {
      expect(Joint[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading body references', () => {
    state.addComponent(entity, Joint);
    Joint.bodyA[entity] = 5;
    Joint.bodyB[entity] = 10;
    expect(Joint.bodyA[entity]).toBe(5);
    expect(Joint.bodyB[entity]).toBe(10);
  });

  it('should allow writing and reading jointType', () => {
    state.addComponent(entity, Joint);
    Joint.jointType[entity] = 1;
    expect(Joint.jointType[entity]).toBe(1);
  });

  it('should allow writing and reading anchor positions', () => {
    state.addComponent(entity, Joint);
    Joint.anchorAX[entity] = 0.5;
    Joint.anchorAY[entity] = 1.0;
    Joint.anchorAZ[entity] = -0.5;
    Joint.anchorBX[entity] = 0.1;
    Joint.anchorBY[entity] = 0.2;
    Joint.anchorBZ[entity] = 0.3;
    expect(Joint.anchorAX[entity]).toBeCloseTo(0.5);
    expect(Joint.anchorAY[entity]).toBeCloseTo(1.0);
    expect(Joint.anchorAZ[entity]).toBeCloseTo(-0.5);
    expect(Joint.anchorBX[entity]).toBeCloseTo(0.1);
    expect(Joint.anchorBY[entity]).toBeCloseTo(0.2);
    expect(Joint.anchorBZ[entity]).toBeCloseTo(0.3);
  });

  it('should allow writing and reading axis', () => {
    state.addComponent(entity, Joint);
    Joint.axisX[entity] = 0;
    Joint.axisY[entity] = 1;
    Joint.axisZ[entity] = 0;
    expect(Joint.axisX[entity]).toBe(0);
    expect(Joint.axisY[entity]).toBe(1);
    expect(Joint.axisZ[entity]).toBe(0);
  });

  it('should allow writing and reading limits', () => {
    state.addComponent(entity, Joint);
    Joint.limitsMin[entity] = -1.57;
    Joint.limitsMax[entity] = 1.57;
    expect(Joint.limitsMin[entity]).toBeCloseTo(-1.57);
    expect(Joint.limitsMax[entity]).toBeCloseTo(1.57);
  });

  it('should allow writing and reading motor fields', () => {
    state.addComponent(entity, Joint);
    Joint.motorSpeed[entity] = 2.5;
    Joint.motorMaxForce[entity] = 100.0;
    expect(Joint.motorSpeed[entity]).toBeCloseTo(2.5);
    expect(Joint.motorMaxForce[entity]).toBeCloseTo(100.0);
  });

  it('should allow writing and reading spring and rope fields', () => {
    state.addComponent(entity, Joint);
    Joint.ropeLength[entity] = 5.0;
    Joint.springStiffness[entity] = 10.0;
    Joint.springDamping[entity] = 1.0;
    expect(Joint.ropeLength[entity]).toBeCloseTo(5.0);
    expect(Joint.springStiffness[entity]).toBeCloseTo(10.0);
    expect(Joint.springDamping[entity]).toBeCloseTo(1.0);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, Joint);
    const entity2 = state.createEntity();
    state.addComponent(entity2, Joint);

    Joint.jointType[entity] = 1;
    Joint.jointType[entity2] = 3;
    Joint.motorSpeed[entity] = 1.5;
    Joint.motorSpeed[entity2] = 3.0;

    expect(Joint.jointType[entity]).toBe(1);
    expect(Joint.jointType[entity2]).toBe(3);
    expect(Joint.motorSpeed[entity]).toBeCloseTo(1.5);
    expect(Joint.motorSpeed[entity2]).toBeCloseTo(3.0);
  });
});
