import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { RaycastPlugin } from '../../../src/plugins/raycast/plugin';
import {
  RaycastSource,
  RaycastHit,
} from '../../../src/plugins/raycast/components';

describe('RaycastPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(RaycastPlugin);
  });

  it('should have a recipe named "raycast-source" with correct components', () => {
    expect(RaycastPlugin.recipes!).toHaveLength(1);
    expect(RaycastPlugin.recipes![0].name).toBe('raycast-source');
    expect(RaycastPlugin.recipes![0].components).toEqual([
      'transform',
      'raycastSource',
      'raycastResult',
    ]);
  });

  it('should register the raycastSource component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, RaycastSource);
    expect(state.hasComponent(entity, RaycastSource)).toBe(true);
  });

  it('should register the raycastResult component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, RaycastHit);
    expect(state.hasComponent(entity, RaycastHit)).toBe(true);
  });

  it('should register the raycast-source recipe', () => {
    const recipe = state.getRecipe('raycast-source');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('raycastSource');
    expect(recipe?.components).toContain('raycastResult');
  });

  it('should have two systems registered (RaycastResetSystem + RaycastSystem)', () => {
    expect(RaycastPlugin.systems).toHaveLength(2);
  });

  it('should have config.defaults for raycastSource', () => {
    const defaults = RaycastPlugin.config!.defaults!.raycastSource;
    expect(defaults).toBeDefined();
    expect(defaults.dirX).toBe(0);
    expect(defaults.dirY).toBe(0);
    expect(defaults.dirZ).toBe(-1);
    expect(defaults.maxDist).toBe(100);
    expect(defaults.layerMask).toBe(0xffff);
    expect(defaults.mode).toBe(0);
  });

  it('should have config.defaults for raycastResult', () => {
    const defaults = RaycastPlugin.config!.defaults!.raycastResult;
    expect(defaults).toBeDefined();
    expect(defaults.hitValid).toBe(0);
    expect(defaults.hitEntity).toBe(0);
    expect(defaults.hitDist).toBe(0);
    expect(defaults.hitNormalX).toBe(0);
    expect(defaults.hitNormalY).toBe(1);
    expect(defaults.hitNormalZ).toBe(0);
    expect(defaults.hitPointX).toBe(0);
    expect(defaults.hitPointY).toBe(0);
    expect(defaults.hitPointZ).toBe(0);
  });

  it('should have config.adapters for raycast-source direction', () => {
    const adapters = RaycastPlugin.config!.adapters!['raycast-source'];
    expect(adapters).toBeDefined();
    expect(adapters.direction).toBeDefined();
    expect(typeof adapters.direction).toBe('function');
  });
});
