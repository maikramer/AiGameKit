import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { NetworkStatus, Networked, NetworkBuffer } from '../../../src/plugins/network/components';

const NETWORK_STATUS_FIELDS = [
  'connected',
] as const;

const NETWORKED_FIELDS = [
  'networkId',
  'isOwner',
  'interpolate',
] as const;

const NETWORK_BUFFER_FIELDS = [
  'prevX',
  'prevY',
  'prevZ',
  'prevRotX',
  'prevRotY',
  'prevRotZ',
  'prevRotW',
  'prevScaleX',
  'prevScaleY',
  'prevScaleZ',
  'nextX',
  'nextY',
  'nextZ',
  'nextRotX',
  'nextRotY',
  'nextRotZ',
  'nextRotW',
  'nextScaleX',
  'nextScaleY',
  'nextScaleZ',
] as const;

describe('NetworkStatus Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have the connected field defined', () => {
    for (const field of NETWORK_STATUS_FIELDS) {
      expect(NetworkStatus[field]).toBeDefined();
      expect(typeof NetworkStatus[field][entity]).toBe('number');
    }
  });

  it('should initialize connected to 0', () => {
    state.addComponent(entity, NetworkStatus);
    expect(NetworkStatus.connected[entity]).toBe(0);
  });

  it('should allow writing and reading connected (roundtrip)', () => {
    state.addComponent(entity, NetworkStatus);
    NetworkStatus.connected[entity] = 2;
    expect(NetworkStatus.connected[entity]).toBe(2);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, NetworkStatus);
    state.addComponent(entity2, NetworkStatus);

    NetworkStatus.connected[entity] = 1;
    NetworkStatus.connected[entity2] = 3;

    expect(NetworkStatus.connected[entity]).toBe(1);
    expect(NetworkStatus.connected[entity2]).toBe(3);
  });
});

describe('Networked Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 3 fields defined', () => {
    for (const field of NETWORKED_FIELDS) {
      expect(Networked[field]).toBeDefined();
      expect(typeof Networked[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, Networked);

    for (const field of NETWORKED_FIELDS) {
      expect(Networked[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading all fields (roundtrip)', () => {
    state.addComponent(entity, Networked);
    Networked.networkId[entity] = 42;
    Networked.isOwner[entity] = 1;
    Networked.interpolate[entity] = 1;

    expect(Networked.networkId[entity]).toBe(42);
    expect(Networked.isOwner[entity]).toBe(1);
    expect(Networked.interpolate[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, Networked);
    state.addComponent(entity2, Networked);

    Networked.networkId[entity] = 1;
    Networked.networkId[entity2] = 2;
    Networked.isOwner[entity] = 1;
    Networked.isOwner[entity2] = 0;

    expect(Networked.networkId[entity]).toBe(1);
    expect(Networked.networkId[entity2]).toBe(2);
    expect(Networked.isOwner[entity]).toBe(1);
    expect(Networked.isOwner[entity2]).toBe(0);
  });
});

describe('NetworkBuffer Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 20 fields defined', () => {
    for (const field of NETWORK_BUFFER_FIELDS) {
      expect(NetworkBuffer[field]).toBeDefined();
      expect(typeof NetworkBuffer[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, NetworkBuffer);

    for (const field of NETWORK_BUFFER_FIELDS) {
      expect(NetworkBuffer[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading position fields (roundtrip)', () => {
    state.addComponent(entity, NetworkBuffer);
    NetworkBuffer.prevX[entity] = 1.0;
    NetworkBuffer.prevY[entity] = 2.0;
    NetworkBuffer.prevZ[entity] = 3.0;
    NetworkBuffer.nextX[entity] = 4.0;
    NetworkBuffer.nextY[entity] = 5.0;
    NetworkBuffer.nextZ[entity] = 6.0;

    expect(NetworkBuffer.prevX[entity]).toBeCloseTo(1.0);
    expect(NetworkBuffer.prevY[entity]).toBeCloseTo(2.0);
    expect(NetworkBuffer.prevZ[entity]).toBeCloseTo(3.0);
    expect(NetworkBuffer.nextX[entity]).toBeCloseTo(4.0);
    expect(NetworkBuffer.nextY[entity]).toBeCloseTo(5.0);
    expect(NetworkBuffer.nextZ[entity]).toBeCloseTo(6.0);
  });

  it('should allow writing and reading rotation fields (roundtrip)', () => {
    state.addComponent(entity, NetworkBuffer);
    NetworkBuffer.prevRotX[entity] = 0.0;
    NetworkBuffer.prevRotY[entity] = 0.0;
    NetworkBuffer.prevRotZ[entity] = 0.7071;
    NetworkBuffer.prevRotW[entity] = 0.7071;
    NetworkBuffer.nextRotX[entity] = 0.0;
    NetworkBuffer.nextRotY[entity] = 1.0;
    NetworkBuffer.nextRotZ[entity] = 0.0;
    NetworkBuffer.nextRotW[entity] = 0.0;

    expect(NetworkBuffer.prevRotX[entity]).toBeCloseTo(0.0);
    expect(NetworkBuffer.prevRotZ[entity]).toBeCloseTo(0.7071);
    expect(NetworkBuffer.prevRotW[entity]).toBeCloseTo(0.7071);
    expect(NetworkBuffer.nextRotY[entity]).toBeCloseTo(1.0);
  });

  it('should allow writing and reading scale fields (roundtrip)', () => {
    state.addComponent(entity, NetworkBuffer);
    NetworkBuffer.prevScaleX[entity] = 2.0;
    NetworkBuffer.prevScaleY[entity] = 3.0;
    NetworkBuffer.prevScaleZ[entity] = 4.0;
    NetworkBuffer.nextScaleX[entity] = 0.5;
    NetworkBuffer.nextScaleY[entity] = 0.5;
    NetworkBuffer.nextScaleZ[entity] = 0.5;

    expect(NetworkBuffer.prevScaleX[entity]).toBeCloseTo(2.0);
    expect(NetworkBuffer.prevScaleY[entity]).toBeCloseTo(3.0);
    expect(NetworkBuffer.prevScaleZ[entity]).toBeCloseTo(4.0);
    expect(NetworkBuffer.nextScaleX[entity]).toBeCloseTo(0.5);
    expect(NetworkBuffer.nextScaleY[entity]).toBeCloseTo(0.5);
    expect(NetworkBuffer.nextScaleZ[entity]).toBeCloseTo(0.5);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, NetworkBuffer);
    state.addComponent(entity2, NetworkBuffer);

    NetworkBuffer.prevX[entity] = 10;
    NetworkBuffer.prevX[entity2] = 20;
    NetworkBuffer.nextX[entity] = 30;
    NetworkBuffer.nextX[entity2] = 40;

    expect(NetworkBuffer.prevX[entity]).toBeCloseTo(10);
    expect(NetworkBuffer.prevX[entity2]).toBeCloseTo(20);
    expect(NetworkBuffer.nextX[entity]).toBeCloseTo(30);
    expect(NetworkBuffer.nextX[entity2]).toBeCloseTo(40);
  });
});
