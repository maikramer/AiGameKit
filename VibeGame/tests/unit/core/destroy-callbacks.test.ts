import { defineComponent, Types } from "bitecs";
import { beforeEach, describe, expect, it } from "bun:test";
import { State } from "vibegame";

describe("destroyEntity callbacks", () => {
  let state: State;

  const TestComponent = defineComponent({
    value: Types.f32,
  });

  beforeEach(() => {
    state = new State();
  });

  it("fires per-entity callback registered with onDestroy", () => {
    const eid = state.createEntity();
    let fired = false;
    state.onDestroy(eid, () => {
      fired = true;
    });
    state.destroyEntity(eid);
    expect(fired).toBe(true);
  });

  it("fires callbacks BEFORE removeEntity — component still accessible inside callback", () => {
    const eid = state.createEntity();
    state.addComponent(eid, TestComponent, { value: 42 });

    let valueInsideCallback: number | null = null;
    state.onDestroy(eid, () => {
      valueInsideCallback = TestComponent.value[eid];
    });
    state.destroyEntity(eid);
    expect(valueInsideCallback).toBe(42);
  });

  it("passes correct entity id to per-entity callback", () => {
    const eid = state.createEntity();
    let receivedEid: number | null = null;
    state.onDestroy(eid, (id) => {
      receivedEid = id;
    });
    state.destroyEntity(eid);
    expect(receivedEid).toBe(eid);
  });

  it("fires global onDestroyAll callback for every destroyed entity", () => {
    const eid1 = state.createEntity();
    const eid2 = state.createEntity();
    const received: number[] = [];
    state.onDestroyAll((id) => {
      received.push(id);
    });
    state.destroyEntity(eid1);
    state.destroyEntity(eid2);
    expect(received).toEqual([eid1, eid2]);
  });

  it("fires global callback with correct entity id", () => {
    const eid = state.createEntity();
    let receivedEid: number | null = null;
    state.onDestroyAll((id) => {
      receivedEid = id;
    });
    state.destroyEntity(eid);
    expect(receivedEid).toBe(eid);
  });

  it("fires multiple per-entity callbacks in registration order", () => {
    const eid = state.createEntity();
    const order: number[] = [];
    state.onDestroy(eid, () => {
      order.push(1);
    });
    state.onDestroy(eid, () => {
      order.push(2);
    });
    state.onDestroy(eid, () => {
      order.push(3);
    });
    state.destroyEntity(eid);
    expect(order).toEqual([1, 2, 3]);
  });

  it("fires per-entity callbacks before global callbacks", () => {
    const eid = state.createEntity();
    const order: string[] = [];
    state.onDestroy(eid, () => {
      order.push("per-entity");
    });
    state.onDestroyAll(() => {
      order.push("global");
    });
    state.destroyEntity(eid);
    expect(order).toEqual(["per-entity", "global"]);
  });

  it("does not fire callbacks for entity with no registered callbacks", () => {
    const eid = state.createEntity();
    let globalFired = false;
    state.onDestroyAll(() => {
      globalFired = true;
    });
    // Destroy entity with no per-entity callbacks — should still fire global
    state.destroyEntity(eid);
    expect(globalFired).toBe(true);
  });

  it("continues firing remaining callbacks if one throws", () => {
    const eid = state.createEntity();
    const order: number[] = [];
    state.onDestroy(eid, () => {
      order.push(1);
    });
    state.onDestroy(eid, () => {
      throw new Error("test error");
    });
    state.onDestroy(eid, () => {
      order.push(3);
    });
    state.destroyEntity(eid);
    expect(order).toEqual([1, 3]);
  });

  it("removes specific callback with offDestroy", () => {
    const eid = state.createEntity();
    let count = 0;
    const cb = () => {
      count++;
    };
    state.onDestroy(eid, cb);
    state.offDestroy(eid, cb);
    state.destroyEntity(eid);
    expect(count).toBe(0);
  });

  it("only removes the specified callback, keeping others", () => {
    const eid = state.createEntity();
    const order: number[] = [];
    const cb1 = () => order.push(1);
    const cb2 = () => order.push(2);
    const cb3 = () => order.push(3);
    state.onDestroy(eid, cb1);
    state.onDestroy(eid, cb2);
    state.onDestroy(eid, cb3);
    state.offDestroy(eid, cb2);
    state.destroyEntity(eid);
    expect(order).toEqual([1, 3]);
  });

  it("cleans up per-entity callbacks after firing", () => {
    const eid = state.createEntity();
    let count = 0;
    state.onDestroy(eid, () => {
      count++;
    });
    state.destroyEntity(eid);
    expect(count).toBe(1);
  });
});
