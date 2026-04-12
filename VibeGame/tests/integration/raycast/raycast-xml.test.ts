import { describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import {
  RaycastSource,
  RaycastHit,
} from '../../../src/plugins/raycast/components';
import { RaycastPlugin } from '../../../src/plugins/raycast/plugin';

describe('Raycast XML recipe', () => {
  function setup(): void {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  }

  it('registers raycast-source recipe via plugin', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const recipe = state.getRecipe('RaycastSource');
    expect(recipe).toBeDefined();
    expect(recipe?.name).toBe('RaycastSource');
    expect(recipe?.components).toContain('transform');
    expect(recipe?.components).toContain('raycastSource');
    expect(recipe?.components).toContain('raycastHit');
  });

  it('raycast-source recipe creates entity with correct components', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const xml = '<root><RaycastSource max-dist="50"></RaycastSource></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;
    expect(state.hasComponent(entity, RaycastSource)).toBe(true);
    expect(state.hasComponent(entity, RaycastHit)).toBe(true);
  });

  it('parses max-dist attribute from XML', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const xml = '<root><RaycastSource max-dist="200"></RaycastSource></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(RaycastSource.maxDist[entity]).toBeCloseTo(200);
  });

  it('parses layer-mask attribute', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const xml = '<root><RaycastSource layer-mask="255"></RaycastSource></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(RaycastSource.layerMask[entity]).toBe(255);
  });

  it('parses mode attribute', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const xml = '<root><RaycastSource mode="1"></RaycastSource></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(RaycastSource.mode[entity]).toBe(1);
  });

  it('direction adapter works via createFromRecipe with string value', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const entity = state.createFromRecipe('RaycastSource', {
      direction: '0 1 0',
    });
    expect(RaycastSource.dirX[entity]).toBeCloseTo(0);
    expect(RaycastSource.dirY[entity]).toBeCloseTo(1);
    expect(RaycastSource.dirZ[entity]).toBeCloseTo(0);
  });

  it('direction adapter normalizes arbitrary vector via createFromRecipe', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const entity = state.createFromRecipe('RaycastSource', {
      direction: '3 4 0',
    });
    expect(RaycastSource.dirX[entity]).toBeCloseTo(3 / 5);
    expect(RaycastSource.dirY[entity]).toBeCloseTo(4 / 5);
    expect(RaycastSource.dirZ[entity]).toBeCloseTo(0);
  });

  it('applies default values for RaycastSource component', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, RaycastSource);

    expect(RaycastSource.dirX[entity]).toBeCloseTo(0);
    expect(RaycastSource.dirY[entity]).toBeCloseTo(0);
    expect(RaycastSource.dirZ[entity]).toBeCloseTo(-1);
    expect(RaycastSource.maxDist[entity]).toBeCloseTo(100);
    expect(RaycastSource.layerMask[entity]).toBe(0xffff);
    expect(RaycastSource.mode[entity]).toBe(0);
  });

  it('applies default values for RaycastResult component', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, RaycastHit);

    expect(RaycastHit.hitValid[entity]).toBe(0);
    expect(RaycastHit.hitEntity[entity]).toBe(0);
    expect(RaycastHit.hitDist[entity]).toBeCloseTo(0);
    expect(RaycastHit.hitNormalX[entity]).toBeCloseTo(0);
    expect(RaycastHit.hitNormalY[entity]).toBeCloseTo(1);
    expect(RaycastHit.hitNormalZ[entity]).toBeCloseTo(0);
    expect(RaycastHit.hitPointX[entity]).toBeCloseTo(0);
    expect(RaycastHit.hitPointY[entity]).toBeCloseTo(0);
    expect(RaycastHit.hitPointZ[entity]).toBeCloseTo(0);
  });

  it('allows writing and reading RaycastSource fields', () => {
    setup();
    const state = new State();
    state.registerPlugin(RaycastPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, RaycastSource);

    RaycastSource.dirX[entity] = 0.5;
    RaycastSource.dirY[entity] = 0.5;
    RaycastSource.dirZ[entity] = -0.7071;
    RaycastSource.maxDist[entity] = 500;
    RaycastSource.mode[entity] = 1;

    expect(RaycastSource.dirX[entity]).toBeCloseTo(0.5);
    expect(RaycastSource.dirY[entity]).toBeCloseTo(0.5);
    expect(RaycastSource.dirZ[entity]).toBeCloseTo(-0.7071);
    expect(RaycastSource.maxDist[entity]).toBeCloseTo(500);
    expect(RaycastSource.mode[entity]).toBe(1);
  });
});
