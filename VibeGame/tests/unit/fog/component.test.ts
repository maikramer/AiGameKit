import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { Fog } from '../../../src/plugins/fog/components';

const FOG_FIELDS = [
  'mode',
  'density',
  'near',
  'far',
  'colorR',
  'colorG',
  'colorB',
  'heightFalloff',
  'baseHeight',
  'volumetricStrength',
  'quality',
  'noiseScale',
] as const;

describe('Fog Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 12 f32 fields', () => {
    for (const field of FOG_FIELDS) {
      expect(Fog[field]).toBeDefined();
      expect(typeof Fog[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, Fog);

    for (const field of FOG_FIELDS) {
      expect(Fog[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading mode', () => {
    state.addComponent(entity, Fog);
    Fog.mode[entity] = 2;
    expect(Fog.mode[entity]).toBe(2);
  });

  it('should allow writing and reading density', () => {
    state.addComponent(entity, Fog);
    Fog.density[entity] = 0.015;
    expect(Fog.density[entity]).toBeCloseTo(0.015);
  });

  it('should allow writing and reading near and far', () => {
    state.addComponent(entity, Fog);
    Fog.near[entity] = 1;
    Fog.far[entity] = 1000;
    expect(Fog.near[entity]).toBe(1);
    expect(Fog.far[entity]).toBe(1000);
  });

  it('should allow writing and reading color channels', () => {
    state.addComponent(entity, Fog);
    Fog.colorR[entity] = 0.533;
    Fog.colorG[entity] = 0.6;
    Fog.colorB[entity] = 0.667;
    expect(Fog.colorR[entity]).toBeCloseTo(0.533);
    expect(Fog.colorG[entity]).toBeCloseTo(0.6);
    expect(Fog.colorB[entity]).toBeCloseTo(0.667);
  });

  it('should allow writing and reading height falloff and base height', () => {
    state.addComponent(entity, Fog);
    Fog.heightFalloff[entity] = 1.0;
    Fog.baseHeight[entity] = -5;
    expect(Fog.heightFalloff[entity]).toBeCloseTo(1.0);
    expect(Fog.baseHeight[entity]).toBe(-5);
  });

  it('should allow writing and reading volumetric strength', () => {
    state.addComponent(entity, Fog);
    Fog.volumetricStrength[entity] = 0.5;
    expect(Fog.volumetricStrength[entity]).toBeCloseTo(0.5);
  });

  it('should allow writing and reading quality', () => {
    state.addComponent(entity, Fog);
    Fog.quality[entity] = 2;
    expect(Fog.quality[entity]).toBe(2);
  });

  it('should allow writing and reading noise scale', () => {
    state.addComponent(entity, Fog);
    Fog.noiseScale[entity] = 1.5;
    expect(Fog.noiseScale[entity]).toBeCloseTo(1.5);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, Fog);
    const entity2 = state.createEntity();
    state.addComponent(entity2, Fog);

    Fog.density[entity] = 0.01;
    Fog.density[entity2] = 0.05;
    Fog.mode[entity] = 0;
    Fog.mode[entity2] = 2;

    expect(Fog.density[entity]).toBeCloseTo(0.01);
    expect(Fog.density[entity2]).toBeCloseTo(0.05);
    expect(Fog.mode[entity]).toBe(0);
    expect(Fog.mode[entity2]).toBe(2);
  });
});
