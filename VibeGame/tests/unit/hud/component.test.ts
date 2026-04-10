import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { HudPanel } from '../../../src/plugins/hud/components';

const HUD_PANEL_FIELDS = [
  'width',
  'height',
  'bgR',
  'bgG',
  'bgB',
  'opacity',
  'textIndex',
  'built',
] as const;

describe('HudPanel Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 8 fields defined', () => {
    for (const field of HUD_PANEL_FIELDS) {
      expect(HudPanel[field]).toBeDefined();
      expect(typeof HudPanel[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, HudPanel);

    for (const field of HUD_PANEL_FIELDS) {
      expect(HudPanel[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading width and height', () => {
    state.addComponent(entity, HudPanel);
    HudPanel.width[entity] = 1.2;
    HudPanel.height[entity] = 0.35;
    expect(HudPanel.width[entity]).toBeCloseTo(1.2);
    expect(HudPanel.height[entity]).toBeCloseTo(0.35);
  });

  it('should allow writing and reading background color channels', () => {
    state.addComponent(entity, HudPanel);
    HudPanel.bgR[entity] = 0.1;
    HudPanel.bgG[entity] = 0.2;
    HudPanel.bgB[entity] = 0.3;
    expect(HudPanel.bgR[entity]).toBeCloseTo(0.1);
    expect(HudPanel.bgG[entity]).toBeCloseTo(0.2);
    expect(HudPanel.bgB[entity]).toBeCloseTo(0.3);
  });

  it('should allow writing and reading opacity', () => {
    state.addComponent(entity, HudPanel);
    HudPanel.opacity[entity] = 0.75;
    expect(HudPanel.opacity[entity]).toBeCloseTo(0.75);
  });

  it('should allow writing and reading textIndex', () => {
    state.addComponent(entity, HudPanel);
    HudPanel.textIndex[entity] = 42;
    expect(HudPanel.textIndex[entity]).toBe(42);
  });

  it('should allow writing and reading built', () => {
    state.addComponent(entity, HudPanel);
    HudPanel.built[entity] = 1;
    expect(HudPanel.built[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, HudPanel);
    const entity2 = state.createEntity();
    state.addComponent(entity2, HudPanel);

    HudPanel.width[entity] = 1.0;
    HudPanel.width[entity2] = 2.0;
    HudPanel.opacity[entity] = 0.5;
    HudPanel.opacity[entity2] = 0.9;

    expect(HudPanel.width[entity]).toBeCloseTo(1.0);
    expect(HudPanel.width[entity2]).toBeCloseTo(2.0);
    expect(HudPanel.opacity[entity]).toBeCloseTo(0.5);
    expect(HudPanel.opacity[entity2]).toBeCloseTo(0.9);
  });
});
