import { beforeEach, describe, expect, it } from "bun:test";
import { State } from "vibegame";

describe("UnityEvent callback system", () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  describe("addEventListener / dispatchEvent", () => {
    it("dispatches event to registered listener", () => {
      const eid = state.createEntity();
      let received: unknown = null;
      state.addEventListener(eid, "onHit", (data) => {
        received = data;
      });
      state.dispatchEvent(eid, "onHit", { damage: 10 });
      expect(received).toEqual({ damage: 10 });
    });

    it("dispatches to multiple listeners for same event", () => {
      const eid = state.createEntity();
      const results: number[] = [];
      state.addEventListener(eid, "onHit", () => results.push(1));
      state.addEventListener(eid, "onHit", () => results.push(2));
      state.dispatchEvent(eid, "onHit");
      expect(results.sort()).toEqual([1, 2]);
    });

    it("does not dispatch to different event name", () => {
      const eid = state.createEntity();
      let called = false;
      state.addEventListener(eid, "onHit", () => {
        called = true;
      });
      state.dispatchEvent(eid, "onDeath");
      expect(called).toBe(false);
    });

    it("dispatches with undefined data when not provided", () => {
      const eid = state.createEntity();
      let received: unknown = "initial";
      state.addEventListener(eid, "onHit", (data) => {
        received = data;
      });
      state.dispatchEvent(eid, "onHit");
      expect(received).toBeUndefined();
    });
  });

  describe("removeEventListener", () => {
    it("removes specific listener", () => {
      const eid = state.createEntity();
      let count = 0;
      const cb = () => {
        count++;
      };
      state.addEventListener(eid, "onHit", cb);
      state.removeEventListener(eid, "onHit", cb);
      state.dispatchEvent(eid, "onHit");
      expect(count).toBe(0);
    });

    it("only removes the specified callback", () => {
      const eid = state.createEntity();
      const order: number[] = [];
      const cb1 = () => order.push(1);
      const cb2 = () => order.push(2);
      state.addEventListener(eid, "onHit", cb1);
      state.addEventListener(eid, "onHit", cb2);
      state.removeEventListener(eid, "onHit", cb1);
      state.dispatchEvent(eid, "onHit");
      expect(order).toEqual([2]);
    });
  });

  describe("addEventListenerOnce", () => {
    it("fires once then auto-removes", () => {
      const eid = state.createEntity();
      let count = 0;
      state.addEventListenerOnce(eid, "onHit", () => {
        count++;
      });
      state.dispatchEvent(eid, "onHit");
      state.dispatchEvent(eid, "onHit");
      expect(count).toBe(1);
    });

    it("receives data on the single fire", () => {
      const eid = state.createEntity();
      let received: unknown = null;
      state.addEventListenerOnce(eid, "onHit", (data) => {
        received = data;
      });
      state.dispatchEvent(eid, "onHit", 42);
      expect(received).toBe(42);
    });
  });

  describe("removeAllEventListeners", () => {
    it("removes all listeners for a specific event", () => {
      const eid = state.createEntity();
      let count = 0;
      state.addEventListener(eid, "onHit", () => count++);
      state.addEventListener(eid, "onHit", () => count++);
      state.addEventListener(eid, "onDeath", () => count++);
      state.removeAllEventListeners(eid, "onHit");
      state.dispatchEvent(eid, "onHit");
      state.dispatchEvent(eid, "onDeath");
      expect(count).toBe(1);
    });

    it("removes all listeners for all events when no event name given", () => {
      const eid = state.createEntity();
      let count = 0;
      state.addEventListener(eid, "onHit", () => count++);
      state.addEventListener(eid, "onDeath", () => count++);
      state.removeAllEventListeners(eid);
      state.dispatchEvent(eid, "onHit");
      state.dispatchEvent(eid, "onDeath");
      expect(count).toBe(0);
    });
  });

  describe("entity destroy cleanup", () => {
    it("cleans up event listeners when entity is destroyed", () => {
      const eid = state.createEntity();
      let count = 0;
      state.addEventListener(eid, "onHit", () => count++);
      state.destroyEntity(eid);
      state.dispatchEvent(eid, "onHit");
      expect(count).toBe(0);
    });
  });
});
