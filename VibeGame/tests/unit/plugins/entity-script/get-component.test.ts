import { beforeEach, describe, expect, it } from 'bun:test';
import { defineComponent, Types } from 'bitecs';

import { Parent } from '../../../../src/core/ecs/components';
import { State } from '../../../../src/core/ecs/state';
import { buildContext } from '../../../../src/plugins/entity-script/system';

const TestMarker = defineComponent({ value: Types.f32 });
const OtherMarker = defineComponent({ flag: Types.ui8 });

describe('EntityScriptContext getComponent methods', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('test-marker', TestMarker);
    state.registerComponent('other-marker', OtherMarker);
  });

  describe('getComponent', () => {
    it('returns component when entity has it', () => {
      const eid = state.createEntity();
      state.addComponent(eid, TestMarker, { value: 42 });
      const ctx = buildContext(state, eid);

      const result = ctx.getComponent('test-marker');

      expect(result).toBe(TestMarker);
    });

    it('returns null when entity does not have the component', () => {
      const eid = state.createEntity();
      state.addComponent(eid, TestMarker, { value: 42 });
      const ctx = buildContext(state, eid);

      const result = ctx.getComponent('other-marker');

      expect(result).toBeNull();
    });

    it('returns null for unregistered component name', () => {
      const eid = state.createEntity();
      const ctx = buildContext(state, eid);

      const result = ctx.getComponent('no-such-component');

      expect(result).toBeNull();
    });
  });

  describe('getComponentInParent', () => {
    it('returns component from self when present', () => {
      const eid = state.createEntity();
      state.addComponent(eid, TestMarker, { value: 1 });
      const ctx = buildContext(state, eid);

      const result = ctx.getComponentInParent('test-marker');

      expect(result).toBe(TestMarker);
    });

    it('returns component from direct parent when self lacks it', () => {
      const parent = state.createEntity();
      const child = state.createEntity();
      state.addComponent(parent, TestMarker, { value: 10 });
      state.addComponent(child, Parent, { entity: parent });

      const ctx = buildContext(state, child);
      const result = ctx.getComponentInParent('test-marker');

      expect(result).toBe(TestMarker);
    });

    it('traverses multiple ancestors', () => {
      const grandparent = state.createEntity();
      const parent = state.createEntity();
      const child = state.createEntity();
      state.addComponent(grandparent, TestMarker, { value: 5 });
      state.addComponent(parent, Parent, { entity: grandparent });
      state.addComponent(child, Parent, { entity: parent });

      const ctx = buildContext(state, child);
      const result = ctx.getComponentInParent('test-marker');

      expect(result).toBe(TestMarker);
    });

    it('returns null when no ancestor has the component', () => {
      const parent = state.createEntity();
      const child = state.createEntity();
      state.addComponent(child, Parent, { entity: parent });

      const ctx = buildContext(state, child);
      const result = ctx.getComponentInParent('test-marker');

      expect(result).toBeNull();
    });

    it('stops at self when self has the component', () => {
      const parent = state.createEntity();
      const child = state.createEntity();
      state.addComponent(child, TestMarker, { value: 99 });
      state.addComponent(child, Parent, { entity: parent });

      const ctx = buildContext(state, child);
      const result = ctx.getComponentInParent('test-marker');

      expect(result).toBe(TestMarker);
    });
  });

  describe('getComponentInChildren', () => {
    it('returns component from self when present', () => {
      const eid = state.createEntity();
      state.addComponent(eid, TestMarker, { value: 1 });
      const ctx = buildContext(state, eid);

      const result = ctx.getComponentInChildren('test-marker');

      expect(result).toBe(TestMarker);
    });

    it('returns component from direct child', () => {
      const parent = state.createEntity();
      const child = state.createEntity();
      state.addComponent(child, Parent, { entity: parent });
      state.addComponent(child, TestMarker, { value: 10 });

      const ctx = buildContext(state, parent);
      const result = ctx.getComponentInChildren('test-marker');

      expect(result).toBe(TestMarker);
    });

    it('searches depth-first through nested children', () => {
      const root = state.createEntity();
      const mid = state.createEntity();
      const leaf = state.createEntity();
      state.addComponent(mid, Parent, { entity: root });
      state.addComponent(leaf, Parent, { entity: mid });
      state.addComponent(leaf, TestMarker, { value: 7 });

      const ctx = buildContext(state, root);
      const result = ctx.getComponentInChildren('test-marker');

      expect(result).toBe(TestMarker);
    });

    it('returns null when no child has the component', () => {
      const parent = state.createEntity();
      const child = state.createEntity();
      state.addComponent(child, Parent, { entity: parent });

      const ctx = buildContext(state, parent);
      const result = ctx.getComponentInChildren('test-marker');

      expect(result).toBeNull();
    });

    it('does not search siblings', () => {
      const parent = state.createEntity();
      const childA = state.createEntity();
      const childB = state.createEntity();
      state.addComponent(childA, Parent, { entity: parent });
      state.addComponent(childB, Parent, { entity: parent });
      state.addComponent(childB, TestMarker, { value: 55 });

      const ctx = buildContext(state, childA);
      const result = ctx.getComponentInChildren('test-marker');

      expect(result).toBeNull();
    });

    it('finds in grandchild when self and direct child lack it', () => {
      const root = state.createEntity();
      const child = state.createEntity();
      const grandchild = state.createEntity();
      state.addComponent(child, Parent, { entity: root });
      state.addComponent(grandchild, Parent, { entity: child });
      state.addComponent(grandchild, OtherMarker, { flag: 1 });

      const ctx = buildContext(state, root);
      const result = ctx.getComponentInChildren('other-marker');

      expect(result).toBe(OtherMarker);
      expect(OtherMarker.flag[grandchild]).toBe(1);
    });
  });
});
