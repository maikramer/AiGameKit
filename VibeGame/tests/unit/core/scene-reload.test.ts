import { defineComponent, Types } from "bitecs";
import { beforeEach, describe, expect, it } from "bun:test";
import { JSDOM } from "jsdom";
import { Scene, State, XMLParser, parseXMLToEntities, getAllEntities } from "vibegame";
import { startCoroutine, getActiveCoroutines, CoroutineRunnerSystem } from "vibegame";

describe("Scene reload", () => {
  let state: State;

  const TestValue = defineComponent({ value: Types.f32 });

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;

    state = new State();
    state.registerComponent("test-value", TestValue);
  });

  describe("state.xmlSource", () => {
    it("stores XML source string on state", () => {
      const xml = "<world><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);
      state.xmlSource = xml;
      expect(state.xmlSource).toBe(xml);
    });

    it("is undefined by default", () => {
      expect(state.xmlSource).toBeUndefined();
    });
  });

  describe("Scene.reload", () => {
    it("destroys all entities and re-parses XML from xmlSource", () => {
      const xml = "<world><entity></entity><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);

      const entitiesBefore = getAllEntities(state.world);
      expect(entitiesBefore.length).toBeGreaterThan(0);

      state.xmlSource = xml;
      Scene.reload(state);

      const entitiesAfter = getAllEntities(state.world);
      expect(entitiesAfter.length).toBeGreaterThan(0);
      expect(entitiesAfter.length).toBe(entitiesBefore.length);
    });

    it("fires OnDestroy callbacks for all entities before destruction", () => {
      const xml = "<world><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);

      const entitiesBefore = getAllEntities(state.world);
      const destroyed: number[] = [];
      for (const eid of entitiesBefore) {
        state.onDestroy(eid, (id) => {
          destroyed.push(id);
        });
      }

      state.xmlSource = xml;
      Scene.reload(state);

      for (const eid of entitiesBefore) {
        expect(destroyed).toContain(eid);
      }
    });

    it("stops all coroutines during reload", () => {
      state.registerSystem(CoroutineRunnerSystem);

      const xml = "<world><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);

      const entitiesBefore = getAllEntities(state.world);
      for (const eid of entitiesBefore) {
        function* gen() {
          yield;
          yield;
        }
        startCoroutine(state, eid, gen);
      }

      state.xmlSource = xml;
      Scene.reload(state);

      for (const eid of entitiesBefore) {
        expect(getActiveCoroutines(state, eid)).toBeUndefined();
      }
    });

    it("clears all template registrations on reload", () => {
      state.registerTemplate("test-tpl", {
        components: { "test-value": { value: 42 } },
      });
      expect(state.getTemplate("test-tpl")).toBeDefined();

      const xml = "<world><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);

      state.xmlSource = xml;
      Scene.reload(state);

      expect(state.getTemplate("test-tpl")).toBeUndefined();
    });

    it("creates new entities from re-parsed XML", () => {
      const xml = "<world><entity></entity><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);

      const entitiesBefore = new Set(getAllEntities(state.world));

      state.xmlSource = xml;
      Scene.reload(state);

      const entitiesAfter = getAllEntities(state.world);
      for (const eid of entitiesAfter) {
        expect(entitiesBefore.has(eid)).toBe(false);
      }
    });

    it("handles multiple reloads without issues", () => {
      const xml = "<world><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);

      state.xmlSource = xml;

      for (let i = 0; i < 5; i++) {
        Scene.reload(state);
        const entities = getAllEntities(state.world);
        expect(entities.length).toBeGreaterThan(0);
      }
    });

    it("throws if xmlSource is not set", () => {
      expect(() => Scene.reload(state)).toThrow(/xmlSource/);
    });
  });

  describe("Scene.reloadAsync", () => {
    it("returns a promise that resolves after reload", async () => {
      const xml = "<world><entity></entity></world>";
      const parsed = XMLParser.parse(xml);
      parseXMLToEntities(state, parsed.root);

      state.xmlSource = xml;
      await Scene.reloadAsync(state);

      const entities = getAllEntities(state.world);
      expect(entities.length).toBeGreaterThan(0);
    });

    it("throws if xmlSource is not set", async () => {
      await expect(Scene.reloadAsync(state)).rejects.toThrow(/xmlSource/);
    });
  });
});
