import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import {
  SteeringAgent,
  SteeringTarget,
} from '../../../src/plugins/ai-steering/components';
import { AiSteeringPlugin } from '../../../src/plugins/ai-steering/plugin';
import { MeshRenderer } from '../../../src/plugins/rendering/components';
import { RenderingPlugin } from '../../../src/plugins/rendering/plugin';

describe('AI-Steering XML recipe', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('registers npc recipe via plugin', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const recipe = state.getRecipe('NPC');
    expect(recipe).toBeDefined();
    expect(recipe?.name).toBe('NPC');
    expect(recipe?.components).toContain('transform');
    expect(recipe?.components).toContain('steeringAgent');
    expect(recipe?.components).toContain('steeringTarget');
  });

  it('npc recipe creates entity with correct components', () => {
    const state = new State();
    state.registerPlugin(RenderingPlugin);
    state.registerPlugin(AiSteeringPlugin);

    const xml = '<root><NPC behavior="seek"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, SteeringAgent)).toBe(true);
    expect(state.hasComponent(entity, SteeringTarget)).toBe(true);
    expect(state.hasComponent(entity, MeshRenderer)).toBe(true);
    expect(MeshRenderer.shape[entity]).toBe(1);
  });

  it('parses seek behavior enum', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const xml = '<root><NPC behavior="seek"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(SteeringAgent.behavior[entity]).toBe(0);
  });

  it('parses wander behavior enum', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const xml = '<root><NPC behavior="wander"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(SteeringAgent.behavior[entity]).toBe(1);
  });

  it('parses flee behavior enum', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const xml = '<root><NPC behavior="flee"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(SteeringAgent.behavior[entity]).toBe(2);
  });

  it('parses max-speed and max-force attributes', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const xml =
      '<root><NPC behavior="seek" max-speed="5" max-force="20"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(SteeringAgent.maxSpeed[entity]).toBeCloseTo(5);
    expect(SteeringAgent.maxForce[entity]).toBeCloseTo(20);
  });

  it('parses target-x/y/z coordinates', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const xml =
      '<root><NPC behavior="seek" target-x="10" target-y="2" target-z="-5"></NPC></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(SteeringTarget.targetX[entity]).toBeCloseTo(10);
    expect(SteeringTarget.targetY[entity]).toBeCloseTo(2);
    expect(SteeringTarget.targetZ[entity]).toBeCloseTo(-5);
  });

  it('applies default values for SteeringAgent component', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, SteeringAgent);

    expect(SteeringAgent.behavior[entity]).toBe(0);
    expect(SteeringAgent.maxSpeed[entity]).toBeCloseTo(3);
    expect(SteeringAgent.maxForce[entity]).toBeCloseTo(10);
    expect(SteeringAgent.active[entity]).toBe(1);
  });

  it('applies default values for SteeringTarget component', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, SteeringTarget);

    expect(SteeringTarget.targetEntity[entity]).toBe(0);
    expect(SteeringTarget.targetX[entity]).toBeCloseTo(0);
    expect(SteeringTarget.targetY[entity]).toBeCloseTo(0);
    expect(SteeringTarget.targetZ[entity]).toBeCloseTo(0);
  });

  it('allows writing and reading SteeringAgent fields', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, SteeringAgent);

    SteeringAgent.behavior[entity] = 2;
    SteeringAgent.maxSpeed[entity] = 7.5;
    SteeringAgent.maxForce[entity] = 15;
    SteeringAgent.active[entity] = 0;

    expect(SteeringAgent.behavior[entity]).toBe(2);
    expect(SteeringAgent.maxSpeed[entity]).toBeCloseTo(7.5);
    expect(SteeringAgent.maxForce[entity]).toBeCloseTo(15);
    expect(SteeringAgent.active[entity]).toBe(0);
  });

  it('creates multiple NPCs with independent values', () => {
    const state = new State();
    state.registerPlugin(AiSteeringPlugin);

    const xml =
      '<root>' +
      '<NPC behavior="seek" max-speed="5" target-x="10"></NPC>' +
      '<NPC behavior="flee" max-speed="8" target-x="-3"></NPC>' +
      '</root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(2);
    expect(SteeringAgent.behavior[entities[0].entity]).toBe(0);
    expect(SteeringAgent.maxSpeed[entities[0].entity]).toBeCloseTo(5);
    expect(SteeringTarget.targetX[entities[0].entity]).toBeCloseTo(10);

    expect(SteeringAgent.behavior[entities[1].entity]).toBe(2);
    expect(SteeringAgent.maxSpeed[entities[1].entity]).toBeCloseTo(8);
    expect(SteeringTarget.targetX[entities[1].entity]).toBeCloseTo(-3);
  });
});
