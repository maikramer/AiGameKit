import { defineComponent, Types } from 'bitecs';
import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';

describe('prefab/template system', () => {
  let state: State;

  const TestHealth = defineComponent({ hp: Types.f32 });
  const TestScore = defineComponent({ points: Types.f32 });
  const TestPos = defineComponent({ x: Types.f32, y: Types.f32, z: Types.f32 });

  beforeEach(() => {
    state = new State();
    state.registerComponent('test-health', TestHealth);
    state.registerComponent('test-score', TestScore);
    state.registerComponent('test-pos', TestPos);
  });

  describe('registerTemplate', () => {
    it('stores a template by id', () => {
      state.registerTemplate('enemy', {
        components: {
          'test-health': { hp: 100 },
          'test-score': { points: 0 },
        },
      });

      const tpl = state.getTemplate('enemy');
      expect(tpl).toBeDefined();
      expect(tpl!.components).toEqual({
        'test-health': { hp: 100 },
        'test-score': { points: 0 },
      });
    });

    it('overwrites existing template with same id', () => {
      state.registerTemplate('enemy', {
        components: { 'test-health': { hp: 50 } },
      });
      state.registerTemplate('enemy', {
        components: { 'test-health': { hp: 200 } },
      });

      const tpl = state.getTemplate('enemy');
      expect(tpl!.components['test-health']).toEqual({ hp: 200 });
    });

    it('returns undefined for unknown template id', () => {
      expect(state.getTemplate('nonexistent')).toBeUndefined();
    });
  });

  describe('Instantiate', () => {
    it('creates entity from template with cloned components', () => {
      state.registerTemplate('enemy', {
        components: {
          'test-health': { hp: 100 },
          'test-score': { points: 10 },
        },
      });

      const eid = state.Instantiate('enemy');

      expect(state.exists(eid)).toBe(true);
      expect(state.hasComponent(eid, TestHealth)).toBe(true);
      expect(state.hasComponent(eid, TestScore)).toBe(true);
      expect(TestHealth.hp[eid]).toBe(100);
      expect(TestScore.points[eid]).toBe(10);
    });

    it('throws when template id is not registered', () => {
      expect(() => state.Instantiate('missing')).toThrow();
    });

    it('returns different entity ids for multiple instantiations', () => {
      state.registerTemplate('enemy', {
        components: { 'test-health': { hp: 50 } },
      });

      const eid1 = state.Instantiate('enemy');
      const eid2 = state.Instantiate('enemy');

      expect(eid1).not.toBe(eid2);
      expect(state.exists(eid1)).toBe(true);
      expect(state.exists(eid2)).toBe(true);
    });

    it('instances are independent — modifying one does not affect the other', () => {
      state.registerTemplate('enemy', {
        components: { 'test-health': { hp: 100 } },
      });

      const eid1 = state.Instantiate('enemy');
      const eid2 = state.Instantiate('enemy');

      TestHealth.hp[eid1] = 0;

      expect(TestHealth.hp[eid2]).toBe(100);
    });

    it('instances are independent of the template data', () => {
      const tpl = {
        components: { 'test-health': { hp: 100 } },
      };
      state.registerTemplate('enemy', tpl);

      const eid = state.Instantiate('enemy');

      tpl.components['test-health']!.hp = 999;

      expect(TestHealth.hp[eid]).toBe(100);
    });

    it('applies position override via transform component fields', () => {
      state.registerTemplate('item', {
        components: { 'test-pos': { x: 0, y: 0, z: 0 } },
      });

      const eid = state.Instantiate('item', {
        overrides: { 'test-pos': { x: 5, y: 10, z: 15 } },
      });

      expect(TestPos.x[eid]).toBe(5);
      expect(TestPos.y[eid]).toBe(10);
      expect(TestPos.z[eid]).toBe(15);
    });

    it('applies parent override by adding Parent component', () => {
      state.registerTemplate('child-template', {
        components: { 'test-health': { hp: 50 } },
      });

      const parentEid = state.createEntity();
      const childEid = state.Instantiate('child-template', {
        parent: parentEid,
      });

      const { Parent } = require('vibegame');
      expect(state.hasComponent(childEid, Parent)).toBe(true);
      expect(Parent.entity[childEid]).toBe(parentEid);
    });

    it('applies both overrides and parent simultaneously', () => {
      state.registerTemplate('npc', {
        components: {
          'test-pos': { x: 0, y: 0, z: 0 },
          'test-health': { hp: 80 },
        },
      });

      const parentEid = state.createEntity();
      const eid = state.Instantiate('npc', {
        parent: parentEid,
        overrides: { 'test-pos': { x: 3, y: 0, z: 0 } },
      });

      const { Parent } = require('vibegame');
      expect(state.hasComponent(eid, Parent)).toBe(true);
      expect(Parent.entity[eid]).toBe(parentEid);
      expect(TestPos.x[eid]).toBe(3);
      expect(TestHealth.hp[eid]).toBe(80);
    });

    it('does not modify the stored template after instantiation', () => {
      state.registerTemplate('enemy', {
        components: { 'test-health': { hp: 100 } },
      });

      state.Instantiate('enemy');
      state.Instantiate('enemy');

      const tpl = state.getTemplate('enemy');
      expect(tpl!.components['test-health']).toEqual({ hp: 100 });
    });

    it('creates entity that can be destroyed normally', () => {
      state.registerTemplate('enemy', {
        components: { 'test-health': { hp: 100 } },
      });

      const eid = state.Instantiate('enemy');
      expect(state.exists(eid)).toBe(true);

      state.destroyEntity(eid);
      expect(state.exists(eid)).toBe(false);
    });

    it('works with empty component set', () => {
      state.registerTemplate('empty', { components: {} });
      const eid = state.Instantiate('empty');
      expect(state.exists(eid)).toBe(true);
    });
  });
});
