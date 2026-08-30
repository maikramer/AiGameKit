import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  NODE_HARVESTED,
  ResourceNode,
  ResourceNodePlugin,
  State,
  harvest,
  isDepleted,
  onEvent,
  resolveResourceNodeKind,
  type NodeHarvestedPayload,
} from 'aigamekit-vibegame';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

function newState(): State {
  const state = new State();
  state.registerPlugin(ResourceNodePlugin);
  return state;
}

describe('ResourceNode matrix — numeric kind passthrough', () => {
  for (let n = 0; n <= 49; n++) {
    it(`resolveResourceNodeKind("${n}") → ${n}`, () => {
      const state = newState();
      expect(resolveResourceNodeKind(state, String(n))).toBe(n);
    });
  }
});

describe('ResourceNode matrix — builtin enum names', () => {
  const kinds = ['wood', 'stone', 'ore'] as const;
  const values = [0, 1, 2] as const;

  for (let i = 0; i < kinds.length; i++) {
    it(`resolve ${kinds[i]} → ${values[i]}`, () => {
      const state = newState();
      expect(resolveResourceNodeKind(state, kinds[i])).toBe(values[i]);
    });
  }
});

describe('ResourceNode matrix — custom enum extension', () => {
  for (let i = 0; i < 30; i++) {
    it(`custom kind mineral-${i} maps to ${100 + i}`, () => {
      const state = newState();
      state.config.register({
        enums: { 'resource-node': { kind: { [`mineral-${i}`]: 100 + i } } },
      });
      expect(resolveResourceNodeKind(state, `mineral-${i}`)).toBe(100 + i);
    });
  }
});

describe('ResourceNode matrix — harvest yield values', () => {
  for (let yieldAmt = 1; yieldAmt <= 25; yieldAmt++) {
    it(`harvest returns yield=${yieldAmt} on one-shot node`, () => {
      const state = newState();
      const eid = state.createFromRecipe('ResourceNode', {
        kind: 'wood',
        yield: yieldAmt,
      });
      expect(harvest(state, eid)).toBe(yieldAmt);
      expect(isDepleted(state, eid)).toBe(false);
    });
  }
});

describe('ResourceNode matrix — harvest event payload yield', () => {
  for (let y = 1; y <= 20; y++) {
    it(`NODE_HARVESTED reports yield=${y}`, () => {
      const state = newState();
      const eid = state.createFromRecipe('ResourceNode', {
        kind: 'stone',
        yield: y,
      });
      const payloads: NodeHarvestedPayload[] = [];
      onEvent(state, NODE_HARVESTED, (p) =>
        payloads.push(p as NodeHarvestedPayload)
      );
      harvest(state, eid);
      expect(payloads[0]?.yield).toBe(y);
    });
  }
});

describe('ResourceNode matrix — depleted harvest returns 0', () => {
  for (let i = 0; i < 10; i++) {
    it(`second harvest on respawnable node #${i} returns 0`, () => {
      const state = newState();
      const eid = state.createFromRecipe('ResourceNode', {
        kind: 'ore',
        yield: 2,
        respawn: 5,
      });
      harvest(state, eid);
      expect(harvest(state, eid)).toBe(0);
      expect(ResourceNode.depleted[eid]).toBe(1);
    });
  }
});
