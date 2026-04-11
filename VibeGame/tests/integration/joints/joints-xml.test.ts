import { describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { Joint } from '../../../src/plugins/joints/components';
import { JointsPlugin } from '../../../src/plugins/joints/plugin';

describe('Joints XML recipe', () => {
  function setup(): void {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  }

  it('registers joint recipe via plugin', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const recipe = state.getRecipe('joint');
    expect(recipe).toBeDefined();
    expect(recipe?.name).toBe('joint');
    expect(recipe?.components).toContain('physicsJoint');
  });

  it('joint recipe creates entity with PhysicsJoint component', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const xml = '<root><joint joint-type="1"></joint></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Joint)).toBe(true);
  });

  it('parses joint-type numeric value from XML', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const xml = '<root><joint joint-type="3"></joint></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Joint.jointType[entity]).toBe(3);
  });

  it('parses spring joint with custom fields', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const xml =
      '<root><joint joint-type="5" spring-stiffness="25" spring-damping="2" rope-length="5"></joint></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(Joint.jointType[entity]).toBe(5);
    expect(Joint.springStiffness[entity]).toBeCloseTo(25);
    expect(Joint.springDamping[entity]).toBeCloseTo(2);
    expect(Joint.ropeLength[entity]).toBeCloseTo(5);
  });

  it('applies default values for PhysicsJoint component', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, Joint);

    expect(Joint.jointType[entity]).toBe(1);
    expect(Joint.bodyA[entity]).toBe(0);
    expect(Joint.bodyB[entity]).toBe(0);
    expect(Joint.axisX[entity]).toBeCloseTo(0);
    expect(Joint.axisY[entity]).toBeCloseTo(1);
    expect(Joint.axisZ[entity]).toBeCloseTo(0);
    expect(Joint.limitsMax[entity]).toBeCloseTo(6.28);
    expect(Joint.created[entity]).toBe(0);
  });

  it('allows writing and reading joint anchor fields', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, Joint);

    Joint.anchorAX[entity] = 1.5;
    Joint.anchorAY[entity] = -0.5;
    Joint.anchorAZ[entity] = 0;
    Joint.anchorBX[entity] = 2;
    Joint.anchorBY[entity] = 0;
    Joint.anchorBZ[entity] = -1;

    expect(Joint.anchorAX[entity]).toBeCloseTo(1.5);
    expect(Joint.anchorAY[entity]).toBeCloseTo(-0.5);
    expect(Joint.anchorAZ[entity]).toBeCloseTo(0);
    expect(Joint.anchorBX[entity]).toBeCloseTo(2);
    expect(Joint.anchorBY[entity]).toBeCloseTo(0);
    expect(Joint.anchorBZ[entity]).toBeCloseTo(-1);
  });

  it('allows writing and reading motor and limits fields', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, Joint);

    Joint.limitsMin[entity] = 0;
    Joint.limitsMax[entity] = 3.14;
    Joint.motorSpeed[entity] = 2.0;
    Joint.motorMaxForce[entity] = 50;

    expect(Joint.limitsMax[entity]).toBeCloseTo(3.14);
    expect(Joint.motorSpeed[entity]).toBeCloseTo(2.0);
    expect(Joint.motorMaxForce[entity]).toBeCloseTo(50);
  });

  it('supports multiple joints with independent values', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const xml =
      '<root>' +
      '<joint joint-type="0"></joint>' +
      '<joint joint-type="5" spring-stiffness="30"></joint>' +
      '</root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(2);
    expect(Joint.jointType[entities[0].entity]).toBe(0);
    expect(Joint.jointType[entities[1].entity]).toBe(5);
    expect(Joint.springStiffness[entities[1].entity]).toBeCloseTo(30);
  });
});
