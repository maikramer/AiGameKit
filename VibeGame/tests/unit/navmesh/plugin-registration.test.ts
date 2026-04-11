import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { NavmeshPlugin } from '../../../src/plugins/navmesh/plugin';
import { NavMeshSurface, NavMeshAgent } from '../../../src/plugins/navmesh/components';

describe('NavmeshPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(NavmeshPlugin);
  });

  it('should have two recipes: "nav-mesh" and "nav-agent"', () => {
    expect(NavmeshPlugin.recipes!).toHaveLength(2);
    expect(NavmeshPlugin.recipes![0].name).toBe('nav-mesh');
    expect(NavmeshPlugin.recipes![0].components).toEqual(['navMesh']);
    expect(NavmeshPlugin.recipes![1].name).toBe('nav-agent');
    expect(NavmeshPlugin.recipes![1].components).toEqual([
      'transform',
      'navAgent',
    ]);
  });

  it('should register the navMesh component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, NavMeshSurface);
    expect(state.hasComponent(entity, NavMeshSurface)).toBe(true);
  });

  it('should register the navAgent component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, NavMeshAgent);
    expect(state.hasComponent(entity, NavMeshAgent)).toBe(true);
  });

  it('should register the nav-mesh recipe', () => {
    const recipe = state.getRecipe('nav-mesh');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('navMesh');
  });

  it('should register the nav-agent recipe', () => {
    const recipe = state.getRecipe('nav-agent');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('navAgent');
  });

  it('should have four systems registered', () => {
    expect(NavmeshPlugin.systems).toHaveLength(4);
  });

  it('should have config.defaults for navMesh', () => {
    const defaults = NavmeshPlugin.config!.defaults!.navMesh;
    expect(defaults).toBeDefined();
    expect(defaults.loaded).toBe(0);
    expect(defaults.buildFromScene).toBe(0);
  });

  it('should have config.defaults for navAgent', () => {
    const defaults = NavmeshPlugin.config!.defaults!.navAgent;
    expect(defaults).toBeDefined();
    expect(defaults.targetX).toBe(0);
    expect(defaults.targetY).toBe(0);
    expect(defaults.targetZ).toBe(0);
    expect(defaults.speed).toBeCloseTo(3);
    expect(defaults.tolerance).toBeCloseTo(0.35);
    expect(defaults.status).toBe(0);
  });
});
