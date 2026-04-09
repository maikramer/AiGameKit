import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { NetworkPlugin } from '../../../src/plugins/network/plugin';
import { Networked, NetworkBuffer } from '../../../src/plugins/network/components';

describe('NetworkPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(NetworkPlugin);
  });

  it('should have a recipe named "networked-player" with components ["transform", "networked", "networkBuffer"]', () => {
    expect(NetworkPlugin.recipes!).toHaveLength(1);
    expect(NetworkPlugin.recipes![0].name).toBe('networked-player');
    expect(NetworkPlugin.recipes![0].components).toEqual(['transform', 'networked', 'networkBuffer']);
  });

  it('should register the networked component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, Networked);
    expect(state.hasComponent(entity, Networked)).toBe(true);
  });

  it('should register the networkBuffer component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, NetworkBuffer);
    expect(state.hasComponent(entity, NetworkBuffer)).toBe(true);
  });

  it('should register the networked-player recipe', () => {
    const recipe = state.getRecipe('networked-player');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('networked');
    expect(recipe?.components).toContain('networkBuffer');
  });

  it('should have three systems registered', () => {
    expect(NetworkPlugin.systems).toHaveLength(3);
  });

  it('should have config.defaults for networked', () => {
    const defaults = NetworkPlugin.config!.defaults!.networked;
    expect(defaults).toBeDefined();
    expect(defaults.networkId).toBe(0);
    expect(defaults.isOwner).toBe(1);
    expect(defaults.interpolate).toBe(1);
  });

  it('should have config.defaults for networkBuffer with rotation W defaulting to 1', () => {
    const defaults = NetworkPlugin.config!.defaults!.networkBuffer;
    expect(defaults).toBeDefined();
    expect(defaults.prevX).toBe(0);
    expect(defaults.prevY).toBe(0);
    expect(defaults.prevZ).toBe(0);
    expect(defaults.prevRotW).toBe(1);
    expect(defaults.prevScaleX).toBe(1);
    expect(defaults.prevScaleY).toBe(1);
    expect(defaults.prevScaleZ).toBe(1);
    expect(defaults.nextRotW).toBe(1);
    expect(defaults.nextScaleX).toBe(1);
    expect(defaults.nextScaleY).toBe(1);
    expect(defaults.nextScaleZ).toBe(1);
  });
});
