import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  NODE_HARVESTED,
  NODE_RESPAWNED,
  ResourceNode,
  ResourceNodePlugin,
  State,
  XMLParser,
  getResourceNodeKind,
  harvest,
  isDepleted,
  isResourceNode,
  onEvent,
  parseXMLToEntities,
  resolveResourceNodeKind,
  type NodeHarvestedPayload,
  type NodeRespawnedPayload,
} from 'vibegame';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

function newState(): State {
  const state = new State();
  state.registerPlugin(ResourceNodePlugin);
  return state;
}

function spawnFromXml(state: State, xml: string): number {
  const parsed = XMLParser.parse(xml);
  const results = parseXMLToEntities(state, parsed.root);
  return results[0].entity;
}

describe('ResourceNode tag + harvest', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('createFromRecipe tags the entity as a wood node', () => {
    const eid = state.createFromRecipe('ResourceNode', {
      kind: 'wood',
      yield: 3,
    });

    expect(isResourceNode(state, eid)).toBe(true);
    expect(getResourceNodeKind(state, eid)).toBe('wood');
    expect(harvest(state, eid)).toBe(3);
  });

  it('one-shot node (respawn=0) does not deplete on harvest', () => {
    const eid = state.createFromRecipe('ResourceNode', {
      kind: 'wood',
      yield: 2,
    });

    expect(harvest(state, eid)).toBe(2);
    expect(isDepleted(state, eid)).toBe(false);
    expect(harvest(state, eid)).toBe(2);
    expect(isDepleted(state, eid)).toBe(false);
  });

  it('harvest emits node:harvested with kind, yield and depleted flag', () => {
    const eid = state.createFromRecipe('ResourceNode', {
      kind: 'wood',
      yield: 4,
    });

    const payloads: NodeHarvestedPayload[] = [];
    onEvent(state, NODE_HARVESTED, (p) => {
      payloads.push(p as NodeHarvestedPayload);
    });

    harvest(state, eid);

    expect(payloads.length).toBe(1);
    expect(payloads[0]).toEqual({
      target: eid,
      kind: 'wood',
      yield: 4,
      depleted: false,
    });
  });
});

describe('ResourceNode kind enum resolution', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('<ResourceNode kind="stone"> resolves via the config enum (XML parser path)', () => {
    const eid = spawnFromXml(
      state,
      `<Scene><ResourceNode kind="stone" yield="5" respawn="30"/></Scene>`
    );

    expect(getResourceNodeKind(state, eid)).toBe('stone');
    expect(ResourceNode.kind[eid]).toBe(1);
  });

  it('<ResourceNode kind="ore"> resolves to enum value 2', () => {
    const eid = spawnFromXml(
      state,
      `<Scene><ResourceNode kind="ore" yield="1"/></Scene>`
    );

    expect(getResourceNodeKind(state, eid)).toBe('ore');
    expect(ResourceNode.kind[eid]).toBe(2);
  });

  it('numeric kind strings pass through', () => {
    expect(resolveResourceNodeKind(state, '2')).toBe(2);
    expect(resolveResourceNodeKind(state, '0')).toBe(0);
  });

  it('unknown kinds fall back to 0 (wood) without throwing', () => {
    expect(resolveResourceNodeKind(state, 'unobtanium')).toBe(0);
  });

  it('custom kinds can be registered by extending the enum', () => {
    state.config.register({
      enums: { 'resource-node': { kind: { crystal: 7 } } },
    });

    expect(resolveResourceNodeKind(state, 'crystal')).toBe(7);

    const eid = spawnFromXml(
      state,
      `<Scene><ResourceNode kind="crystal" yield="9"/></Scene>`
    );
    expect(getResourceNodeKind(state, eid)).toBe('crystal');
    expect(ResourceNode.kind[eid]).toBe(7);
  });
});

describe('ResourceNode respawn', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('respawnable node depletes then respawns after the cooldown', () => {
    const eid = spawnFromXml(
      state,
      `<Scene><ResourceNode kind="stone" yield="5" respawn="30"/></Scene>`
    );

    expect(harvest(state, eid)).toBe(5);
    expect(isDepleted(state, eid)).toBe(true);
    expect(ResourceNode.respawnAt[eid]).toBe(30);

    expect(harvest(state, eid)).toBe(0);

    state.step(29);
    expect(isDepleted(state, eid)).toBe(true);

    const respawned: NodeRespawnedPayload[] = [];
    onEvent(state, NODE_RESPAWNED, (p) => {
      respawned.push(p as NodeRespawnedPayload);
    });

    state.step(1);
    expect(isDepleted(state, eid)).toBe(false);
    expect(harvest(state, eid)).toBe(5);

    expect(respawned.length).toBe(1);
    expect(respawned[0]).toEqual({ target: eid, kind: 'stone' });
  });

  it('node:respawned fires only once when the timer elapses', () => {
    const eid = spawnFromXml(
      state,
      `<Scene><ResourceNode kind="ore" yield="2" respawn="10"/></Scene>`
    );

    harvest(state, eid);

    const counts = { respawned: 0 };
    onEvent(state, NODE_RESPAWNED, () => {
      counts.respawned++;
    });

    state.step(10);
    expect(counts.respawned).toBe(1);

    state.step(5);
    state.step(5);
    expect(counts.respawned).toBe(1);
  });
});

describe('ResourceNode non-node entities', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('helpers are no-ops on entities without the component', () => {
    const eid = state.createEntity();

    expect(isResourceNode(state, eid)).toBe(false);
    expect(getResourceNodeKind(state, eid)).toBe('');
    expect(isDepleted(state, eid)).toBe(false);
    expect(harvest(state, eid)).toBe(0);
  });
});
