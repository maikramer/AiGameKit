import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import {
  YukaAgentComponent,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_SEEK,
  YUKA_BEHAVIOR_WANDER,
} from '../../../src/plugins/ai-yuka/components';
import { YukaAiPlugin } from '../../../src/plugins/ai-yuka/plugin';
import { MeshRenderer } from '../../../src/plugins/rendering/components';
import { RenderingPlugin } from '../../../src/plugins/rendering/plugin';

describe('AI-Yuka XML recipe', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('registers npc recipe via plugin', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const recipe = state.getRecipe('NPC');
    expect(recipe).toBeDefined();
    expect(recipe?.name).toBe('NPC');
    expect(recipe?.components).toContain('transform');
    expect(recipe?.components).toContain('yukaAgent');
  });

  it('npc recipe creates entity with correct components', () => {
    const state = new State();
    state.registerPlugin(RenderingPlugin);
    state.registerPlugin(YukaAiPlugin);

    const xml = '<root><NPC behavior="seek"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, YukaAgentComponent)).toBe(true);
    expect(state.hasComponent(entity, MeshRenderer)).toBe(true);
    expect(MeshRenderer.shape[entity]).toBe(1);
  });

  it('parses seek behavior enum', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const xml = '<root><NPC behavior="seek"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(YukaAgentComponent.behavior[entity]).toBe(YUKA_BEHAVIOR_SEEK);
  });

  it('parses wander behavior enum', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const xml = '<root><NPC behavior="wander"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(YukaAgentComponent.behavior[entity]).toBe(YUKA_BEHAVIOR_WANDER);
  });

  it('parses flee behavior enum', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const xml = '<root><NPC behavior="flee"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(YukaAgentComponent.behavior[entity]).toBe(YUKA_BEHAVIOR_FLEE);
  });

  it('parses max-speed and max-force attributes', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const xml =
      '<root><NPC behavior="seek" max-speed="5" max-force="20"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(YukaAgentComponent.maxSpeed[entity]).toBeCloseTo(5);
    expect(YukaAgentComponent.maxForce[entity]).toBeCloseTo(20);
  });

  it('parses target-x/z coordinates', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const xml =
      '<root><NPC behavior="seek" target-x="10" target-z="-5"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(YukaAgentComponent.targetX[entity]).toBeCloseTo(10);
    expect(YukaAgentComponent.targetZ[entity]).toBeCloseTo(-5);
  });

  it('applies default values for YukaAgentComponent', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, YukaAgentComponent);

    expect(YukaAgentComponent.behavior[entity]).toBe(YUKA_BEHAVIOR_SEEK);
    expect(YukaAgentComponent.maxSpeed[entity]).toBeCloseTo(3);
    expect(YukaAgentComponent.maxForce[entity]).toBeCloseTo(8);
    expect(YukaAgentComponent.active[entity]).toBe(1);
  });

  it('creates multiple NPCs with independent values', () => {
    const state = new State();
    state.registerPlugin(YukaAiPlugin);

    const xml =
      '<root>' +
      '<NPC behavior="seek" max-speed="5" target-x="10"></NPC>' +
      '<NPC behavior="flee" max-speed="8" target-x="-3"></NPC>' +
      '</root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(2);
    expect(YukaAgentComponent.behavior[entities[0].entity]).toBe(
      YUKA_BEHAVIOR_SEEK
    );
    expect(YukaAgentComponent.maxSpeed[entities[0].entity]).toBeCloseTo(5);
    expect(YukaAgentComponent.targetX[entities[0].entity]).toBeCloseTo(10);

    expect(YukaAgentComponent.behavior[entities[1].entity]).toBe(
      YUKA_BEHAVIOR_FLEE
    );
    expect(YukaAgentComponent.maxSpeed[entities[1].entity]).toBeCloseTo(8);
    expect(YukaAgentComponent.targetX[entities[1].entity]).toBeCloseTo(-3);
  });
});
