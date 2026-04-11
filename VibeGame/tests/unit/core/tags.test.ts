import { beforeEach, describe, expect, it } from "bun:test";
import {
  State,
  addTag,
  getTagId,
  getTagName,
} from "vibegame";

describe("Tag system", () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  describe("built-in tags", () => {
    it("has Untagged at ID 0", () => {
      expect(getTagId("Untagged")).toBe(0);
      expect(getTagName(0)).toBe("Untagged");
    });

    it("has Player at ID 1", () => {
      expect(getTagId("Player")).toBe(1);
      expect(getTagName(1)).toBe("Player");
    });

    it("has MainCamera at ID 2", () => {
      expect(getTagId("MainCamera")).toBe(2);
      expect(getTagName(2)).toBe("MainCamera");
    });

    it("has Respawn at ID 3", () => {
      expect(getTagId("Respawn")).toBe(3);
      expect(getTagName(3)).toBe("Respawn");
    });

    it("has Finish at ID 4", () => {
      expect(getTagId("Finish")).toBe(4);
      expect(getTagName(4)).toBe("Finish");
    });

    it("has EditorOnly at ID 5", () => {
      expect(getTagId("EditorOnly")).toBe(5);
      expect(getTagName(5)).toBe("EditorOnly");
    });
  });

  describe("addTag", () => {
    it("registers a custom tag and returns its ID", () => {
      const id = addTag("Enemy");
      expect(id).toBeGreaterThan(5);
      expect(getTagId("Enemy")).toBe(id);
      expect(getTagName(id)).toBe("Enemy");
    });

    it("returns existing ID for already-registered tag", () => {
      const id1 = addTag("Boss");
      const id2 = addTag("Boss");
      expect(id1).toBe(id2);
    });
  });

  describe("getTagId / getTagName", () => {
    it("returns -1 for unknown tag name", () => {
      expect(getTagId("NonExistent")).toBe(-1);
    });

    it("returns empty string for unknown tag ID", () => {
      expect(getTagName(250)).toBe("");
    });
  });

  describe("State.setTag / State.getTag", () => {
    it("sets and gets a tag on an entity", () => {
      const eid = state.createEntity();
      state.setTag(eid, "Player");
      expect(state.getTag(eid)).toBe("Player");
    });

    it("returns Untagged for entity without Tag component", () => {
      const eid = state.createEntity();
      expect(state.getTag(eid)).toBe("Untagged");
    });

    it("overwrites previous tag", () => {
      const eid = state.createEntity();
      state.setTag(eid, "Player");
      state.setTag(eid, "MainCamera");
      expect(state.getTag(eid)).toBe("MainCamera");
    });

    it("auto-registers custom tag via setTag", () => {
      const eid = state.createEntity();
      state.setTag(eid, "CustomBoss");
      expect(state.getTag(eid)).toBe("CustomBoss");
    });
  });

  describe("State.findByTag", () => {
    it("finds first entity with given tag", () => {
      state.createEntity();
      const eid2 = state.createEntity();
      state.setTag(eid2, "Player");
      expect(state.findByTag("Player")).toBe(eid2);
    });

    it("returns undefined when no entity has the tag", () => {
      expect(state.findByTag("Player")).toBeUndefined();
    });
  });

  describe("State.findGameObjectsWithTag", () => {
    it("finds all entities with given tag", () => {
      const eid1 = state.createEntity();
      const eid2 = state.createEntity();
      const eid3 = state.createEntity();
      state.setTag(eid1, "Player");
      state.setTag(eid2, "MainCamera");
      state.setTag(eid3, "Player");
      const result = state.findGameObjectsWithTag("Player");
      expect(result.sort()).toEqual([eid1, eid3].sort());
    });

    it("returns empty array when no entity has the tag", () => {
      expect(state.findGameObjectsWithTag("Player")).toEqual([]);
    });
  });
});
