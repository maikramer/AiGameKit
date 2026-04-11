import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import { HudPanel } from '../../../src/plugins/hud/components';

describe('HudPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(HudPlugin);
  });

  it('should have a recipe named "HudPanel" with components ["transform", "hudPanel"]', () => {
    expect(HudPlugin.recipes!).toHaveLength(1);
    expect(HudPlugin.recipes![0].name).toBe('HudPanel');
    expect(HudPlugin.recipes![0].components).toEqual(['transform', 'hudPanel']);
  });

  it('should register the hudPanel component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, HudPanel);
    expect(state.hasComponent(entity, HudPanel)).toBe(true);
  });

  it('should register the hud-panel recipe', () => {
    const recipe = state.getRecipe('HudPanel');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('hudPanel');
  });

  it('should have two systems registered (HudBuildSystem + HudSyncSystem)', () => {
    expect(HudPlugin.systems).toHaveLength(2);
  });

  it('should have config.defaults for hudPanel', () => {
    const defaults = HudPlugin.config!.defaults!.hudPanel;
    expect(defaults).toBeDefined();
    expect(defaults.width).toBeCloseTo(1.2);
    expect(defaults.height).toBeCloseTo(0.35);
    expect(defaults.bgR).toBe(0);
    expect(defaults.bgG).toBe(0);
    expect(defaults.bgB).toBe(0);
    expect(defaults.opacity).toBeCloseTo(0.75);
    expect(defaults.textIndex).toBe(0);
    expect(defaults.built).toBe(0);
  });

  it('should have config.adapters for hud-panel', () => {
    const adapters = HudPlugin.config!.adapters!['hud-panel'];
    expect(adapters).toBeDefined();
    expect(adapters.text).toBeDefined();
    expect(adapters['bg-color']).toBeDefined();
  });
});
