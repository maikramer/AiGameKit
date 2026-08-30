import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { YukaAiPlugin } from '../../../src/plugins/ai-yuka/plugin';
import {
  YukaAgentComponent,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_SEEK,
  YUKA_BEHAVIOR_WANDER,
} from '../../../src/plugins/ai-yuka/components';

describe('YukaAiPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(YukaAiPlugin);
  });

  it('should have a recipe named "NPC" with transform, yukaAgent, and renderer placeholder', () => {
    expect(YukaAiPlugin.recipes!).toHaveLength(1);
    expect(YukaAiPlugin.recipes![0].name).toBe('NPC');
    expect(YukaAiPlugin.recipes![0].components).toEqual([
      'transform',
      'yukaAgent',
      'meshRenderer',
    ]);
  });

  it('should register the yukaAgent component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, YukaAgentComponent);
    expect(state.hasComponent(entity, YukaAgentComponent)).toBe(true);
  });

  it('should register the npc recipe', () => {
    const recipe = state.getRecipe('NPC');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('yukaAgent');
  });

  it('should have one system registered (YukaAgentSystem)', () => {
    expect(YukaAiPlugin.systems).toHaveLength(1);
  });

  it('should have config.defaults for yuka-agent', () => {
    const defaults = YukaAiPlugin.config!.defaults!['yuka-agent'];
    expect(defaults).toBeDefined();
    expect(defaults.behavior).toBe(YUKA_BEHAVIOR_SEEK);
    expect(defaults.maxSpeed).toBeCloseTo(3);
    expect(defaults.maxForce).toBeCloseTo(8);
    expect(defaults.active).toBe(1);
  });

  it('should have config.enums for yuka-agent behavior', () => {
    const enums = YukaAiPlugin.config!.enums!['yuka-agent'];
    expect(enums).toBeDefined();
    expect(enums.behavior).toBeDefined();
    expect(enums.behavior.seek).toBe(YUKA_BEHAVIOR_SEEK);
    expect(enums.behavior.wander).toBe(YUKA_BEHAVIOR_WANDER);
    expect(enums.behavior.flee).toBe(YUKA_BEHAVIOR_FLEE);
  });
});
