import { beforeEach, describe, expect, it } from "bun:test";
import {
  cleanupEntityCoroutines,
  CoroutineRunnerSystem,
  getActiveCoroutines,
  getCoroutine,
  startCoroutine,
  stopAllCoroutines,
  stopCoroutine,
  State,
} from "vibegame";

describe("coroutine scheduler", () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerSystem(CoroutineRunnerSystem);
  });

  it("startCoroutine returns incrementing IDs", () => {
    const eid = state.createEntity();
    function* gen() { yield; }
    const id1 = startCoroutine(state, eid, gen);
    const id2 = startCoroutine(state, eid, gen);
    expect(id1).toBe(1);
    expect(id2).toBe(2);
  });

  it("startCoroutine accepts a generator function", () => {
    const eid = state.createEntity();
    function* gen() { yield; }
    const id = startCoroutine(state, eid, () => gen());
    expect(id).toBe(1);
  });

  it("startCoroutine accepts a raw generator", () => {
    const eid = state.createEntity();
    function* gen() { yield; }
    const id = startCoroutine(state, eid, gen());
    expect(id).toBe(1);
  });

  it("yield null advances one frame per step", () => {
    const eid = state.createEntity();
    const log: string[] = [];
    function* myCoroutine() {
      log.push("start");
      yield;
      log.push("frame2");
      yield;
      log.push("frame3");
    }

    startCoroutine(state, eid, myCoroutine);
    expect(log).toEqual(["start"]);

    state.step();
    expect(log).toEqual(["start", "frame2"]);

    state.step();
    expect(log).toEqual(["start", "frame2", "frame3"]);
  });

  it("generator completes and is removed after returning", () => {
    const eid = state.createEntity();
    const log: string[] = [];
    function* gen() {
      log.push("a");
      yield;
      log.push("b");
    }

    startCoroutine(state, eid, gen);
    expect(log).toEqual(["a"]);

    state.step();
    expect(log).toEqual(["a", "b"]);

    state.step();
    const coroutines = getActiveCoroutines(state, eid);
    expect(coroutines).toBeUndefined();
  });

  it("multiple coroutines on same entity run independently", () => {
    const eid = state.createEntity();
    const log: string[] = [];
    function* genA() {
      log.push("A1");
      yield;
      log.push("A2");
    }
    function* genB() {
      log.push("B1");
      yield;
      log.push("B2");
    }

    startCoroutine(state, eid, genA);
    startCoroutine(state, eid, genB);
    expect(log).toEqual(["A1", "B1"]);

    state.step();
    expect(log).toEqual(["A1", "B1", "A2", "B2"]);
  });

  it("stopCoroutine stops a specific coroutine by ID", () => {
    const eid = state.createEntity();
    const log: string[] = [];
    function* genA() {
      log.push("A1");
      yield;
      log.push("A2");
    }
    function* genB() {
      log.push("B1");
      yield;
      log.push("B2");
    }

    const idA = startCoroutine(state, eid, genA);
    const idB = startCoroutine(state, eid, genB);
    stopCoroutine(state, eid, idA);

    state.step();
    expect(log).toEqual(["A1", "B1", "B2"]);
    expect(getCoroutine(state, eid, idA)).toBeUndefined();
    expect(getCoroutine(state, eid, idB)).toBeUndefined();
  });

  it("stopAllCoroutines stops all coroutines on entity", () => {
    const eid = state.createEntity();
    const log: string[] = [];
    function* gen() {
      log.push("run");
      yield;
      log.push("after");
    }

    startCoroutine(state, eid, gen);
    startCoroutine(state, eid, gen);
    stopAllCoroutines(state, eid);

    state.step();
    expect(log).toEqual(["run", "run"]);
  });

  it("coroutines are cleaned up when entity is destroyed", () => {
    const eid = state.createEntity();
    function* gen() { yield; yield; yield; }

    startCoroutine(state, eid, gen);
    startCoroutine(state, eid, gen);

    state.destroyEntity(eid);

    const coroutines = getActiveCoroutines(state, eid);
    expect(coroutines).toBeUndefined();
  });

  it("destroyed entity coroutines are cleaned up during system update", () => {
    const eid = state.createEntity();
    const log: string[] = [];
    function* gen() {
      log.push("run");
      yield;
    }

    startCoroutine(state, eid, gen);

    state.destroyEntity(eid);

    state.step();
    expect(log).toEqual(["run"]);
    expect(getActiveCoroutines(state, eid)).toBeUndefined();
  });

  it("getCoroutine returns entry for active coroutine", () => {
    const eid = state.createEntity();
    function* gen() { yield; yield; }

    const id = startCoroutine(state, eid, gen);
    const entry = getCoroutine(state, eid, id);
    expect(entry).toBeDefined();
    expect(entry!.done).toBe(false);
  });

  it("getCoroutine returns undefined for unknown ID", () => {
    const eid = state.createEntity();
    expect(getCoroutine(state, eid, 999)).toBeUndefined();
  });

  it("stopCoroutine on non-existent ID is a no-op", () => {
    const eid = state.createEntity();
    expect(() => stopCoroutine(state, eid, 999)).not.toThrow();
  });

  it("stopAllCoroutines on entity with no coroutines is a no-op", () => {
    const eid = state.createEntity();
    expect(() => stopAllCoroutines(state, eid)).not.toThrow();
  });

  it("cleanupEntityCoroutines removes all coroutine state for entity", () => {
    const eid = state.createEntity();
    function* gen() { yield; yield; }

    startCoroutine(state, eid, gen);
    startCoroutine(state, eid, gen);

    cleanupEntityCoroutines(state, eid);

    expect(getActiveCoroutines(state, eid)).toBeUndefined();
  });

  it("IDs are unique across different entities", () => {
    const eid1 = state.createEntity();
    const eid2 = state.createEntity();
    function* gen() { yield; }

    const id1 = startCoroutine(state, eid1, gen);
    const id2 = startCoroutine(state, eid2, gen);
    expect(id1).not.toBe(id2);
  });

  it("generator returning immediately is cleaned up on first update", () => {
    const eid = state.createEntity();
    const log: string[] = [];
    function* gen() {
      log.push("done");
    }

    startCoroutine(state, eid, gen);
    expect(log).toEqual(["done"]);

    state.step();
    expect(getActiveCoroutines(state, eid)).toBeUndefined();
  });
});
