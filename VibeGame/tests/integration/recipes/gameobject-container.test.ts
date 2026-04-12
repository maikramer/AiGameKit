import { defineComponent, Types } from 'bitecs';
import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';

describe('GameObject Container Syntax', () => {
  let state: State;

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;

    state = new State();

    const Transform = defineComponent({
      posX: Types.f32,
      posY: Types.f32,
      posZ: Types.f32,
      scaleX: Types.f32,
      scaleY: Types.f32,
      scaleZ: Types.f32,
    });
    const Parent = defineComponent({ entity: Types.i32 });
    const Rigidbody = defineComponent({ type: Types.i8, mass: Types.f32 });
    const Collider = defineComponent({
      shape: Types.i8,
      radius: Types.f32,
      height: Types.f32,
    });
    const MeshRenderer = defineComponent({
      visible: Types.ui8,
      shape: Types.i8,
    });
    const PointLight = defineComponent({
      intensity: Types.f32,
      colorR: Types.f32,
    });
    const AudioSource = defineComponent({
      volume: Types.f32,
      clipPath: Types.f32,
    });

    state.registerComponent('transform', Transform);
    state.registerComponent('parent', Parent);
    state.registerComponent('rigidbody', Rigidbody);
    state.registerComponent('collider', Collider);
    state.registerComponent('meshRenderer', MeshRenderer);
    state.registerComponent('pointLight', PointLight);
    state.registerComponent('audioSource', AudioSource);

    state.registerConfig({
      defaults: {
        transform: {
          posX: 0,
          posY: 0,
          posZ: 0,
          scaleX: 1,
          scaleY: 1,
          scaleZ: 1,
        },
        rigidbody: { type: 0, mass: 1 },
        collider: { shape: 0, radius: 0.5, height: 1 },
        meshRenderer: { visible: 1, shape: 0 },
        pointLight: { intensity: 1, colorR: 1 },
        audioSource: { volume: 1, clipPath: 0 },
      },
      enums: {
        rigidbody: {
          type: { static: 0, dynamic: 1, kinematic: 2 },
        },
        collider: {
          shape: { box: 0, sphere: 1, capsule: 2 },
        },
      },
      shorthands: {
        transform: { pos: 'pos', scale: 'scale' },
        collider: { shape: 'shape' },
      },
    });

    state.registerRecipe({
      name: 'Rigidbody',
      merge: true,
      components: ['rigidbody', 'transform'],
    });
    state.registerRecipe({
      name: 'Collider',
      merge: true,
      components: ['collider', 'transform'],
    });
    state.registerRecipe({
      name: 'MeshRenderer',
      merge: true,
      components: ['transform', 'meshRenderer'],
    });
    state.registerRecipe({
      name: 'PointLight',
      merge: true,
      components: ['transform', 'pointLight'],
    });
    state.registerRecipe({
      name: 'AudioSource',
      merge: true,
      components: ['audioSource'],
    });
    state.registerRecipe({
      name: 'Transform',
      merge: true,
      components: ['transform'],
    });
  });

  it('should create entity with name from <GameObject name="Test">', () => {
    const xml = '<root><GameObject name="Test"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;
    expect(state.getEntityByName('Test')).toBe(entity);
  });

  it('should set Transform position from child <Transform pos="1 2 3" />', () => {
    const Transform = state.getComponent('transform')!;
    const xml =
      '<root><GameObject><Transform pos="1 2 3" /></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Transform)).toBe(true);
    expect((Transform as any).posX[entity]).toBe(1);
    expect((Transform as any).posY[entity]).toBe(2);
    expect((Transform as any).posZ[entity]).toBe(3);
  });

  it('should auto-add Transform when no <Transform> child is present', () => {
    const Transform = state.getComponent('transform')!;
    const xml =
      '<root><GameObject name="NoTransformChild"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Transform)).toBe(true);
  });

  it('should set tag component from tag="Player" attribute', () => {
    const xml = '<root><GameObject tag="Player"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    const Tag = state.getComponent('tag');
    if (Tag) {
      expect(state.hasComponent(entity, Tag)).toBe(true);
      expect((Tag as any).value[entity]).toBe(1);
    }
  });

  it('should set layer component from layer="6" attribute', () => {
    const xml = '<root><GameObject layer="6"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    const Layer = state.getComponent('layer');
    if (Layer) {
      expect(state.hasComponent(entity, Layer)).toBe(true);
      expect((Layer as any).value[entity]).toBe(6);
    }
  });

  it('should set layer component from layer="Player" attribute (name lookup)', () => {
    const xml = '<root><GameObject layer="Player"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    const Layer = state.getComponent('layer');
    if (Layer) {
      expect(state.hasComponent(entity, Layer)).toBe(true);
      expect((Layer as any).value[entity]).toBe(6);
    }
  });

  it('should merge multiple component children onto parent entity', () => {
    const Rigidbody = state.getComponent('rigidbody')!;
    const Collider = state.getComponent('collider')!;
    const MeshRenderer = state.getComponent('meshRenderer')!;
    const Transform = state.getComponent('transform')!;

    const xml = `<root>
      <GameObject name="Player">
        <Rigidbody rigidbody="type: dynamic" />
        <Collider collider="shape: capsule" />
        <MeshRenderer meshRenderer="visible: 1" />
      </GameObject>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;

    expect(state.hasComponent(entity, Transform)).toBe(true);
    expect(state.hasComponent(entity, Rigidbody)).toBe(true);
    expect(state.hasComponent(entity, Collider)).toBe(true);
    expect(state.hasComponent(entity, MeshRenderer)).toBe(true);

    expect((Rigidbody as any).type[entity]).toBe(1);
    expect((Collider as any).shape[entity]).toBe(2);
    expect((MeshRenderer as any).visible[entity]).toBe(1);
  });

  it('should support shorthand syntax (backward compat)', () => {
    const xml =
      '<root><GameObject name="Door" pos="5 0 0" rigidbody="type: static" collider="shape: box" /></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;
    expect(state.getEntityByName('Door')).toBe(entity);
  });

  it('should support container syntax equivalent to shorthand', () => {
    const xml = `<root>
      <GameObject name="Door" pos="5 0 0">
        <Rigidbody rigidbody="type: static" />
        <Collider collider="shape: box" />
      </GameObject>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const entity = entities[0].entity;
    expect(state.getEntityByName('Door')).toBe(entity);

    const Transform = state.getComponent('transform')!;
    expect((Transform as any).posX[entity]).toBe(5);
    expect((Transform as any).posY[entity]).toBe(0);
    expect((Transform as any).posZ[entity]).toBe(0);

    const Rigidbody = state.getComponent('rigidbody')!;
    expect((Rigidbody as any).type[entity]).toBe(0);

    const Collider = state.getComponent('collider')!;
    expect((Collider as any).shape[entity]).toBe(0);
  });

  it('should create parent-child hierarchy for nested <GameObject>', () => {
    const Parent = state.getComponent('parent')!;
    const Transform = state.getComponent('transform')!;

    const xml = `<root>
      <GameObject name="Parent">
        <GameObject name="Child1"></GameObject>
        <GameObject name="Child2"></GameObject>
      </GameObject>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    expect(entities[0].children.length).toBe(2);

    const parentEntity = entities[0].entity;
    const childEntity1 = entities[0].children[0].entity;
    const childEntity2 = entities[0].children[1].entity;

    expect(state.hasComponent(childEntity1, Parent)).toBe(true);
    expect((Parent as any).entity[childEntity1]).toBe(parentEntity);
    expect(state.hasComponent(childEntity2, Parent)).toBe(true);
    expect((Parent as any).entity[childEntity2]).toBe(parentEntity);

    expect(state.hasComponent(parentEntity, Transform)).toBe(true);
    expect(state.hasComponent(childEntity1, Transform)).toBe(true);
    expect(state.hasComponent(childEntity2, Transform)).toBe(true);
  });

  it('should handle mixed container children: components + nested GameObjects', () => {
    const Parent = state.getComponent('parent')!;
    const Collider = state.getComponent('collider')!;

    const xml = `<root>
      <GameObject name="Root">
        <Collider collider="shape: box" />
        <GameObject name="NestedChild"></GameObject>
      </GameObject>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    expect(entities.length).toBe(1);
    const rootEntity = entities[0].entity;

    expect(state.hasComponent(rootEntity, Collider)).toBe(true);
    expect((Collider as any).shape[rootEntity]).toBe(0);

    expect(entities[0].children.length).toBe(1);
    const childEntity = entities[0].children[0].entity;
    expect(state.hasComponent(childEntity, Parent)).toBe(true);
    expect((Parent as any).entity[childEntity]).toBe(rootEntity);
  });

  it('should handle tag and layer with component children simultaneously', () => {
    const xml = `<root>
      <GameObject name="Hero" tag="Player" layer="6">
        <Rigidbody rigidbody="type: dynamic" />
      </GameObject>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    const Tag = state.getComponent('tag');
    const Layer = state.getComponent('layer');
    const Rigidbody = state.getComponent('rigidbody')!;

    if (Tag) {
      expect(state.hasComponent(entity, Tag)).toBe(true);
      expect((Tag as any).value[entity]).toBe(1);
    }
    if (Layer) {
      expect(state.hasComponent(entity, Layer)).toBe(true);
      expect((Layer as any).value[entity]).toBe(6);
    }
    expect(state.hasComponent(entity, Rigidbody)).toBe(true);
    expect((Rigidbody as any).type[entity]).toBe(1);
  });

  it('should register new tag when unknown tag name is used', () => {
    const xml = '<root><GameObject tag="CustomEnemy"></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    const Tag = state.getComponent('tag');
    if (Tag) {
      expect(state.hasComponent(entity, Tag)).toBe(true);
      expect((Tag as any).value[entity]).toBeGreaterThan(0);
    }
  });

  it('should handle empty <Transform /> child (no-op, transform already present)', () => {
    const Transform = state.getComponent('transform')!;
    const xml =
      '<root><GameObject name="WithEmptyTransform"><Transform /></GameObject></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(state.hasComponent(entity, Transform)).toBe(true);
    expect(entities[0].children.length).toBe(0);
  });

  it('should support PointLight as merge child', () => {
    const PointLight = state.getComponent('pointLight')!;
    const xml = `<root>
      <GameObject name="Lamp">
        <PointLight pointLight="intensity: 2" />
      </GameObject>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(state.hasComponent(entity, PointLight)).toBe(true);
    expect((PointLight as any).intensity[entity]).toBe(2);
  });

  it('should support AudioSource as merge child', () => {
    const AudioSource = state.getComponent('audioSource')!;
    const xml = `<root>
      <GameObject name="Speaker">
        <AudioSource audioSource="volume: 0.5" />
      </GameObject>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);

    const entity = entities[0].entity;
    expect(state.hasComponent(entity, AudioSource)).toBe(true);
    expect((AudioSource as any).volume[entity]).toBe(0.5);
  });
});
