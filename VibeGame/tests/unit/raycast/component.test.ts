import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  RaycastSource,
  RaycastHit,
} from '../../../src/plugins/raycast/components';

const SOURCE_FIELDS = [
  'dirX',
  'dirY',
  'dirZ',
  'maxDist',
  'layerMask',
  'mode',
] as const;

const RESULT_FIELDS = [
  'hitValid',
  'hitEntity',
  'hitDist',
  'hitNormalX',
  'hitNormalY',
  'hitNormalZ',
  'hitPointX',
  'hitPointY',
  'hitPointZ',
] as const;

describe('RaycastSource Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 6 fields defined', () => {
    for (const field of SOURCE_FIELDS) {
      expect(RaycastSource[field]).toBeDefined();
      expect(typeof RaycastSource[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, RaycastSource);

    for (const field of SOURCE_FIELDS) {
      expect(RaycastSource[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading direction', () => {
    state.addComponent(entity, RaycastSource);
    RaycastSource.dirX[entity] = 0;
    RaycastSource.dirY[entity] = 1;
    RaycastSource.dirZ[entity] = 0;
    expect(RaycastSource.dirX[entity]).toBe(0);
    expect(RaycastSource.dirY[entity]).toBe(1);
    expect(RaycastSource.dirZ[entity]).toBe(0);
  });

  it('should allow writing and reading maxDist', () => {
    state.addComponent(entity, RaycastSource);
    RaycastSource.maxDist[entity] = 100;
    expect(RaycastSource.maxDist[entity]).toBe(100);
  });

  it('should allow writing and reading layerMask', () => {
    state.addComponent(entity, RaycastSource);
    RaycastSource.layerMask[entity] = 0xffff;
    expect(RaycastSource.layerMask[entity]).toBe(0xffff);
  });

  it('should allow writing and reading mode', () => {
    state.addComponent(entity, RaycastSource);
    RaycastSource.mode[entity] = 0;
    expect(RaycastSource.mode[entity]).toBe(0);
    RaycastSource.mode[entity] = 1;
    expect(RaycastSource.mode[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, RaycastSource);
    const entity2 = state.createEntity();
    state.addComponent(entity2, RaycastSource);

    RaycastSource.maxDist[entity] = 50;
    RaycastSource.maxDist[entity2] = 200;
    RaycastSource.mode[entity] = 0;
    RaycastSource.mode[entity2] = 1;

    expect(RaycastSource.maxDist[entity]).toBe(50);
    expect(RaycastSource.maxDist[entity2]).toBe(200);
    expect(RaycastSource.mode[entity]).toBe(0);
    expect(RaycastSource.mode[entity2]).toBe(1);
  });
});

describe('RaycastResult Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 9 fields defined', () => {
    for (const field of RESULT_FIELDS) {
      expect(RaycastHit[field]).toBeDefined();
      expect(typeof RaycastHit[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, RaycastHit);

    for (const field of RESULT_FIELDS) {
      expect(RaycastHit[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading hitValid and hitEntity', () => {
    state.addComponent(entity, RaycastHit);
    RaycastHit.hitValid[entity] = 1;
    RaycastHit.hitEntity[entity] = 42;
    expect(RaycastHit.hitValid[entity]).toBe(1);
    expect(RaycastHit.hitEntity[entity]).toBe(42);
  });

  it('should allow writing and reading hit distance', () => {
    state.addComponent(entity, RaycastHit);
    RaycastHit.hitDist[entity] = 15.5;
    expect(RaycastHit.hitDist[entity]).toBeCloseTo(15.5);
  });

  it('should allow writing and reading hit normal', () => {
    state.addComponent(entity, RaycastHit);
    RaycastHit.hitNormalX[entity] = 0;
    RaycastHit.hitNormalY[entity] = 1;
    RaycastHit.hitNormalZ[entity] = 0;
    expect(RaycastHit.hitNormalX[entity]).toBe(0);
    expect(RaycastHit.hitNormalY[entity]).toBe(1);
    expect(RaycastHit.hitNormalZ[entity]).toBe(0);
  });

  it('should allow writing and reading hit point', () => {
    state.addComponent(entity, RaycastHit);
    RaycastHit.hitPointX[entity] = 1.5;
    RaycastHit.hitPointY[entity] = 2.0;
    RaycastHit.hitPointZ[entity] = -3.0;
    expect(RaycastHit.hitPointX[entity]).toBeCloseTo(1.5);
    expect(RaycastHit.hitPointY[entity]).toBeCloseTo(2.0);
    expect(RaycastHit.hitPointZ[entity]).toBeCloseTo(-3.0);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, RaycastHit);
    const entity2 = state.createEntity();
    state.addComponent(entity2, RaycastHit);

    RaycastHit.hitValid[entity] = 1;
    RaycastHit.hitValid[entity2] = 0;
    RaycastHit.hitDist[entity] = 10;
    RaycastHit.hitDist[entity2] = 50;

    expect(RaycastHit.hitValid[entity]).toBe(1);
    expect(RaycastHit.hitValid[entity2]).toBe(0);
    expect(RaycastHit.hitDist[entity]).toBe(10);
    expect(RaycastHit.hitDist[entity2]).toBe(50);
  });
});
