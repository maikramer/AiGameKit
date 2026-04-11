import { beforeEach, describe, expect, it } from "bun:test";
import { State, LayerMask } from "vibegame";

describe("Layer system", () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  describe("built-in layers", () => {
    it("has Default at layer 0", () => {
      expect(LayerMask.NameToLayer("Default")).toBe(0);
      expect(LayerMask.LayerToName(0)).toBe("Default");
    });

    it("has TransparentFX at layer 1", () => {
      expect(LayerMask.NameToLayer("TransparentFX")).toBe(1);
    });

    it("has IgnoreRaycast at layer 2", () => {
      expect(LayerMask.NameToLayer("IgnoreRaycast")).toBe(2);
    });

    it("has Water at layer 3", () => {
      expect(LayerMask.NameToLayer("Water")).toBe(3);
    });

    it("has UI at layer 4", () => {
      expect(LayerMask.NameToLayer("UI")).toBe(4);
    });

    it("has Player at layer 6 (skips 5)", () => {
      expect(LayerMask.NameToLayer("Player")).toBe(6);
    });

    it("has Enemy at layer 7", () => {
      expect(LayerMask.NameToLayer("Enemy")).toBe(7);
    });

    it("has PhysicsBody at layer 8", () => {
      expect(LayerMask.NameToLayer("PhysicsBody")).toBe(8);
    });

    it("has Trigger at layer 9", () => {
      expect(LayerMask.NameToLayer("Trigger")).toBe(9);
    });

    it("layer 5 is unassigned", () => {
      expect(LayerMask.LayerToName(5)).toBe("");
    });
  });

  describe("LayerMask", () => {
    it("NameToLayer returns -1 for unknown name", () => {
      expect(LayerMask.NameToLayer("NonExistent")).toBe(-1);
    });

    it("LayerToName returns empty string for unknown layer", () => {
      expect(LayerMask.LayerToName(30)).toBe("");
    });

    it("GetMask returns correct bitmask for single layer", () => {
      expect(LayerMask.GetMask(["Default"])).toBe(1);
      expect(LayerMask.GetMask(["Player"])).toBe(1 << 6);
    });

    it("GetMask returns combined bitmask for multiple layers", () => {
      const mask = LayerMask.GetMask(["Player", "Enemy"]);
      expect(mask).toBe((1 << 6) | (1 << 7));
    });

    it("GetMask ignores unknown layer names", () => {
      expect(LayerMask.GetMask(["Default", "NonExistent"])).toBe(1);
    });
  });

  describe("State.setLayer / State.getLayer", () => {
    it("sets and gets a layer on an entity", () => {
      const eid = state.createEntity();
      state.setLayer(eid, 6);
      expect(state.getLayer(eid)).toBe(6);
    });

    it("returns 0 (Default) for entity without Layer component", () => {
      const eid = state.createEntity();
      expect(state.getLayer(eid)).toBe(0);
    });

    it("overwrites previous layer", () => {
      const eid = state.createEntity();
      state.setLayer(eid, 6);
      state.setLayer(eid, 7);
      expect(state.getLayer(eid)).toBe(7);
    });
  });
});
