import { describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { PhysicsJoint } from '../../../src/plugins/joints/components';
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
    expect(state.hasComponent(entity, PhysicsJoint)).toBe(true);
  });

  it('parses joint-type numeric value from XML', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const xml = '<root><joint joint-type="3"></joint></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(PhysicsJoint.jointType[entity]).toBe(3);
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
    expect(PhysicsJoint.jointType[entity]).toBe(5);
    expect(PhysicsJoint.springStiffness[entity]).toBeCloseTo(25);
    expect(PhysicsJoint.springDamping[entity]).toBeCloseTo(2);
    expect(PhysicsJoint.ropeLength[entity]).toBeCloseTo(5);
  });

  it('applies default values for PhysicsJoint component', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, PhysicsJoint);

    expect(PhysicsJoint.jointType[entity]).toBe(1);
    expect(PhysicsJoint.bodyA[entity]).toBe(0);
    expect(PhysicsJoint.bodyB[entity]).toBe(0);
    expect(PhysicsJoint.axisX[entity]).toBeCloseTo(0);
    expect(PhysicsJoint.axisY[entity]).toBeCloseTo(1);
    expect(PhysicsJoint.axisZ[entity]).toBeCloseTo(0);
    expect(PhysicsJoint.limitsMax[entity]).toBeCloseTo(6.28);
    expect(PhysicsJoint.created[entity]).toBe(0);
  });

  it('allows writing and reading joint anchor fields', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, PhysicsJoint);

    PhysicsJoint.anchorAX[entity] = 1.5;
    PhysicsJoint.anchorAY[entity] = -0.5;
    PhysicsJoint.anchorAZ[entity] = 0;
    PhysicsJoint.anchorBX[entity] = 2;
    PhysicsJoint.anchorBY[entity] = 0;
    PhysicsJoint.anchorBZ[entity] = -1;

    expect(PhysicsJoint.anchorAX[entity]).toBeCloseTo(1.5);
    expect(PhysicsJoint.anchorAY[entity]).toBeCloseTo(-0.5);
    expect(PhysicsJoint.anchorAZ[entity]).toBeCloseTo(0);
    expect(PhysicsJoint.anchorBX[entity]).toBeCloseTo(2);
    expect(PhysicsJoint.anchorBY[entity]).toBeCloseTo(0);
    expect(PhysicsJoint.anchorBZ[entity]).toBeCloseTo(-1);
  });

  it('allows writing and reading motor and limits fields', () => {
    setup();
    const state = new State();
    state.registerPlugin(JointsPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, PhysicsJoint);

    PhysicsJoint.limitsMin[entity] = 0;
    PhysicsJoint.limitsMax[entity] = 3.14;
    PhysicsJoint.motorSpeed[entity] = 2.0;
    PhysicsJoint.motorMaxForce[entity] = 50;

    expect(PhysicsJoint.limitsMax[entity]).toBeCloseTo(3.14);
    expect(PhysicsJoint.motorSpeed[entity]).toBeCloseTo(2.0);
    expect(PhysicsJoint.motorMaxForce[entity]).toBeCloseTo(50);
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
    expect(PhysicsJoint.jointType[entities[0].entity]).toBe(0);
    expect(PhysicsJoint.jointType[entities[1].entity]).toBe(5);
    expect(PhysicsJoint.springStiffness[entities[1].entity]).toBeCloseTo(30);
  });
});
