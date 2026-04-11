import { beforeEach, describe, expect, it } from "bun:test";
import { Parent, State } from "vibegame";

describe("cascade destroy", () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  function addChild(parentEid: number): number {
    const child = state.createEntity();
    state.addComponent(child, Parent, { entity: parentEid });
    return child;
  }

  it("destroying parent destroys immediate children", () => {
    const parent = state.createEntity();
    const child1 = addChild(parent);
    const child2 = addChild(parent);

    state.destroyEntity(parent);

    expect(state.exists(parent)).toBe(false);
    expect(state.exists(child1)).toBe(false);
    expect(state.exists(child2)).toBe(false);
  });

  it("destroying parent destroys grandchildren (deep hierarchy)", () => {
    const root = state.createEntity();
    const child = addChild(root);
    const grandchild1 = addChild(child);
    const grandchild2 = addChild(child);
    const greatGrandchild = addChild(grandchild1);

    state.destroyEntity(root);

    expect(state.exists(root)).toBe(false);
    expect(state.exists(child)).toBe(false);
    expect(state.exists(grandchild1)).toBe(false);
    expect(state.exists(grandchild2)).toBe(false);
    expect(state.exists(greatGrandchild)).toBe(false);
  });

  it("destroying a leaf child does NOT destroy parent or siblings", () => {
    const parent = state.createEntity();
    const child1 = addChild(parent);
    const child2 = addChild(parent);

    state.destroyEntity(child1);

    expect(state.exists(parent)).toBe(true);
    expect(state.exists(child2)).toBe(true);
    expect(state.exists(child1)).toBe(false);
  });

  it("onDestroy callbacks fire for all descendants", () => {
    const root = state.createEntity();
    const child = addChild(root);
    const grandchild = addChild(child);

    const destroyed: number[] = [];
    state.onDestroy(root, (eid) => destroyed.push(eid));
    state.onDestroy(child, (eid) => destroyed.push(eid));
    state.onDestroy(grandchild, (eid) => destroyed.push(eid));

    state.destroyEntity(root);

    expect(destroyed).toContain(root);
    expect(destroyed).toContain(child);
    expect(destroyed).toContain(grandchild);
    expect(destroyed.length).toBe(3);
  });

  it("destroying entity with no children works as before (backward compat)", () => {
    const eid = state.createEntity();
    let callbackFired = false;
    state.onDestroy(eid, () => {
      callbackFired = true;
    });

    state.destroyEntity(eid);

    expect(callbackFired).toBe(true);
    expect(state.exists(eid)).toBe(false);
  });

  it("global onDestroyAll fires for every destroyed entity in cascade", () => {
    const parent = state.createEntity();
    const child1 = addChild(parent);
    const child2 = addChild(parent);

    const destroyed: number[] = [];
    state.onDestroyAll((eid) => destroyed.push(eid));

    state.destroyEntity(parent);

    expect(destroyed).toContain(parent);
    expect(destroyed).toContain(child1);
    expect(destroyed).toContain(child2);
    expect(destroyed.length).toBe(3);
  });

  it("getDescendants returns empty array for entity with no children", () => {
    const eid = state.createEntity();
    const desc = state.getDescendants(eid);
    expect(desc).toEqual([]);
  });

  it("getDescendants returns deepest-first order", () => {
    const root = state.createEntity();
    const child = addChild(root);
    const grandchild = addChild(child);

    const desc = state.getDescendants(root);

    expect(desc).toEqual([grandchild, child]);
  });

  it("descendants are destroyed deepest-first", () => {
    const root = state.createEntity();
    const child = addChild(root);
    const grandchild = addChild(child);

    const order: string[] = [];
    state.onDestroy(grandchild, () => order.push("grandchild"));
    state.onDestroy(child, () => order.push("child"));
    state.onDestroy(root, () => order.push("root"));

    state.destroyEntity(root);

    expect(order).toEqual(["grandchild", "child", "root"]);
  });

  it("cascade destroys siblings at same depth", () => {
    const parent = state.createEntity();
    const child1 = addChild(parent);
    const child2 = addChild(parent);
    const child3 = addChild(parent);

    state.destroyEntity(parent);

    expect(state.exists(parent)).toBe(false);
    expect(state.exists(child1)).toBe(false);
    expect(state.exists(child2)).toBe(false);
    expect(state.exists(child3)).toBe(false);
  });
});
