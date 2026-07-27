import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import {
  BodyType,
  CharacterController,
  CharacterMovement,
  Collider,
  ColliderShape,
  PhysicsPlugin,
  Rigidbody,
} from 'vibegame/physics';
import { Transform, TransformsPlugin } from 'vibegame/transforms';

describe('Creature recipe', () => {
  let state: State;

  beforeEach(async () => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;

    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(PhysicsPlugin);
    await state.initializePlugins();
  });

  it('creates kinematic CCT stack from XML', () => {
    const xml = '<root><Creature pos="1 2 3" /></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);
    const eid = entities[0].entity;

    expect(state.hasComponent(eid, Transform)).toBe(true);
    expect(state.hasComponent(eid, Rigidbody)).toBe(true);
    expect(state.hasComponent(eid, Collider)).toBe(true);
    expect(state.hasComponent(eid, CharacterController)).toBe(true);
    expect(state.hasComponent(eid, CharacterMovement)).toBe(true);

    expect(Transform.posX[eid]).toBeCloseTo(1);
    expect(Transform.posY[eid]).toBeCloseTo(2);
    expect(Transform.posZ[eid]).toBeCloseTo(3);

    expect(Rigidbody.type[eid]).toBe(BodyType.KinematicPositionBased);
    expect(Rigidbody.gravityScale[eid]).toBeCloseTo(1);
    expect(Rigidbody.lockRotX[eid]).toBe(1);
    expect(Rigidbody.lockRotZ[eid]).toBe(1);

    expect(Collider.shape[eid]).toBe(ColliderShape.Capsule);
    expect(Collider.radius[eid]).toBeCloseTo(0.4);
    expect(Collider.height[eid]).toBeCloseTo(0.2);
    expect(Collider.posOffsetY[eid]).toBeCloseTo(0.5);

    expect(CharacterController.snapDist[eid]).toBeCloseTo(0.5);
  });
});
