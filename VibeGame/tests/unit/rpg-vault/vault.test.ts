import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  ECONOMY_GAINED,
  ECONOMY_SPENT,
  RpgCoreEventsPlugin,
  RpgVaultPlugin,
  State,
  VaultComponent,
  XMLParser,
  addResource,
  getCapacity,
  getResource,
  onEvent,
  parseXMLToEntities,
  pruneVaults,
  registerResourceKind,
  setCapacity,
  spendResource,
} from 'vibegame';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

function newState(): State {
  const state = new State();
  state.registerPlugin(RpgCoreEventsPlugin);
  state.registerPlugin(RpgVaultPlugin);
  return state;
}

describe('ResourceVault / RpgVaultPlugin', () => {
  describe('add + spend + get round-trip', () => {
    it('adds, then reads back the amount', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 500);
      expect(getResource(state, eid, 'gold')).toBe(500);
    });

    it('spends and returns true when sufficient, deducting the amount', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 500);
      const ok = spendResource(state, eid, 'gold', 200);
      expect(ok).toBe(true);
      expect(getResource(state, eid, 'gold')).toBe(300);
    });

    it('spending the exact balance succeeds and leaves zero', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 100);
      expect(spendResource(state, eid, 'gold', 100)).toBe(true);
      expect(getResource(state, eid, 'gold')).toBe(0);
    });
  });

  describe('spend rejects when insufficient', () => {
    it('returns false and leaves the balance intact', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 100);
      const ok = spendResource(state, eid, 'gold', 200);
      expect(ok).toBe(false);
      expect(getResource(state, eid, 'gold')).toBe(100);
    });

    it('returns false for a kind that was never registered', () => {
      const state = newState();
      const eid = state.createEntity();
      expect(spendResource(state, eid, 'gems', 1)).toBe(false);
    });
  });

  describe('capacity', () => {
    it('clamps addResource to the configured capacity', () => {
      const state = newState();
      const eid = state.createEntity();
      setCapacity(state, eid, 'gold', 100);
      addResource(state, eid, 'gold', 150);
      expect(getResource(state, eid, 'gold')).toBe(100);
    });

    it('exposes the configured capacity via getCapacity', () => {
      const state = newState();
      const eid = state.createEntity();
      setCapacity(state, eid, 'gold', 42);
      expect(getCapacity(state, eid, 'gold')).toBe(42);
    });

    it('defaults capacity when none was set', () => {
      const state = newState();
      const eid = state.createEntity();
      expect(getCapacity(state, eid, 'gold')).toBe(9999);
    });

    it('setCapacity lowers an existing balance that exceeds the new cap', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 500);
      setCapacity(state, eid, 'gold', 100);
      expect(getResource(state, eid, 'gold')).toBe(100);
    });
  });

  describe('events', () => {
    it('emits ECONOMY_GAINED on add with {entity, kind, amount}', () => {
      const state = newState();
      const eid = state.createEntity();
      const payloads: Array<Record<string, unknown>> = [];
      onEvent(state, ECONOMY_GAINED, (p) => {
        payloads.push(p as Record<string, unknown>);
      });
      addResource(state, eid, 'gold', 100);
      expect(payloads.length).toBe(1);
      expect(payloads[0]).toEqual({ entity: eid, kind: 'gold', amount: 100 });
    });

    it('emits ECONOMY_SPENT on spend with {entity, kind, amount}', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 100);
      const payloads: Array<Record<string, unknown>> = [];
      onEvent(state, ECONOMY_SPENT, (p) => {
        payloads.push(p as Record<string, unknown>);
      });
      const ok = spendResource(state, eid, 'gold', 50);
      expect(ok).toBe(true);
      expect(payloads.length).toBe(1);
      expect(payloads[0]).toEqual({ entity: eid, kind: 'gold', amount: 50 });
    });

    it('does not emit ECONOMY_SPENT when spend is rejected', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 10);
      const seen: unknown[] = [];
      onEvent(state, ECONOMY_SPENT, (p) => seen.push(p));
      spendResource(state, eid, 'gold', 999);
      expect(seen.length).toBe(0);
    });

    it('emits the clamped delta when addResource hits capacity', () => {
      const state = newState();
      const eid = state.createEntity();
      setCapacity(state, eid, 'gold', 100);
      const seen: Array<Record<string, unknown>> = [];
      onEvent(state, ECONOMY_GAINED, (p) =>
        seen.push(p as Record<string, unknown>)
      );
      addResource(state, eid, 'gold', 150);
      expect(seen[0]).toEqual({ entity: eid, kind: 'gold', amount: 100 });
    });
  });

  describe('registerResourceKind', () => {
    it('returns a stable index for the same kind', () => {
      const state = newState();
      const a = registerResourceKind(state, 'gold');
      const b = registerResourceKind(state, 'gold');
      expect(a).toBe(b);
      expect(a).toBeGreaterThanOrEqual(0);
    });

    it('returns distinct indices for distinct kinds', () => {
      const state = newState();
      const gold = registerResourceKind(state, 'gold');
      const wood = registerResourceKind(state, 'wood');
      expect(gold).not.toBe(wood);
    });
  });

  describe('isolation', () => {
    it('keeps different resource kinds independent per entity', () => {
      const state = newState();
      const eid = state.createEntity();
      addResource(state, eid, 'gold', 100);
      addResource(state, eid, 'wood', 40);
      expect(getResource(state, eid, 'gold')).toBe(100);
      expect(getResource(state, eid, 'wood')).toBe(40);
    });

    it('keeps different entities independent', () => {
      const state = newState();
      const a = state.createEntity();
      const b = state.createEntity();
      addResource(state, a, 'gold', 100);
      addResource(state, b, 'gold', 7);
      expect(getResource(state, a, 'gold')).toBe(100);
      expect(getResource(state, b, 'gold')).toBe(7);
    });

    it('keeps different States independent', () => {
      const s1 = newState();
      const s2 = newState();
      const a = s1.createEntity();
      const b = s2.createEntity();
      addResource(s1, a, 'gold', 100);
      addResource(s2, b, 'gold', 5);
      expect(getResource(s1, a, 'gold')).toBe(100);
      expect(getResource(s2, b, 'gold')).toBe(5);
    });
  });

  describe('VaultComponent marker', () => {
    it('marks an entity active once a resource is added', () => {
      const state = newState();
      const eid = state.createEntity();
      // VaultComponent.active is a global SOA array (module-level, like Health);
      // reset this slot to isolate from prior tests that reused the same eid index.
      VaultComponent.active[eid] = 0;
      addResource(state, eid, 'gold', 1);
      expect(VaultComponent.active[eid]).toBe(1);
    });
  });

  describe('pruneVaults / VaultEventBridgeSystem cleanup', () => {
    it('pruneVaults removes entries for destroyed entities', () => {
      const state = newState();
      const alive = state.createEntity();
      const dead = state.createEntity();
      addResource(state, alive, 'gold', 10);
      addResource(state, dead, 'gold', 20);
      const removed = pruneVaults(
        state,
        (eid) => state.exists(eid) && eid !== dead
      );
      expect(removed).toBe(1);
      expect(getResource(state, dead, 'gold')).toBe(0);
      expect(getResource(state, alive, 'gold')).toBe(10);
    });
  });

  describe('<Vault> recipe (XML)', () => {
    it('creates an active vault entity with capacity-<kind> attributes', () => {
      const state = newState();
      const xml = `<Scene><Vault capacity-gold="1000" capacity-wood="999"/></Scene>`;
      const results = parseXMLToEntities(state, XMLParser.parse(xml).root);

      expect(results.length).toBe(1);
      const eid = results[0].entity;

      expect(getCapacity(state, eid, 'gold')).toBe(1000);
      expect(getCapacity(state, eid, 'wood')).toBe(999);
    });
  });
});
