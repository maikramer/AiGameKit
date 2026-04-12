import { beforeEach, describe, expect, it } from 'bun:test';
import {
  CoroutineFixedUpdateSystem,
  CoroutineLateFrameSystem,
  CoroutineRunnerSystem,
  startCoroutine,
  State,
  WaitForSeconds,
  WaitForSecondsRealtime,
  WaitForEndOfFrame,
  WaitForFixedUpdate,
  WaitUntil,
  WaitWhile,
} from 'vibegame';

describe('yield instructions', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerSystem(CoroutineRunnerSystem);
    state.registerSystem(CoroutineLateFrameSystem);
    state.registerSystem(CoroutineFixedUpdateSystem);
  });

  describe('WaitForSeconds', () => {
    it('resumes after N seconds of scaled time', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('start');
        yield WaitForSeconds(0.5);
        log.push('after-wait');
      }

      startCoroutine(state, eid, myCoroutine);
      expect(log).toEqual(['start']);

      state.step(0.3);
      expect(log).toEqual(['start']);

      state.step(0.3);
      expect(log).toEqual(['start', 'after-wait']);
    });

    it('respects timeScale', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('start');
        yield WaitForSeconds(1);
        log.push('done');
      }

      state.time.timeScale = 2;
      startCoroutine(state, eid, myCoroutine);

      state.step(0.4);
      expect(log).toEqual(['start']);

      state.step(0.2);
      expect(log).toEqual(['start', 'done']);
    });
  });

  describe('WaitForSecondsRealtime', () => {
    it('resumes after N seconds of unscaled time', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('start');
        yield WaitForSecondsRealtime(0.5);
        log.push('done');
      }

      startCoroutine(state, eid, myCoroutine);

      state.step(0.3);
      expect(log).toEqual(['start']);

      state.step(0.3);
      expect(log).toEqual(['start', 'done']);
    });

    it('ignores timeScale', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('start');
        yield WaitForSecondsRealtime(1);
        log.push('done');
      }

      state.time.timeScale = 0.5;
      startCoroutine(state, eid, myCoroutine);

      state.step(0.6);
      expect(log).toEqual(['start']);

      state.step(0.6);
      expect(log).toEqual(['start', 'done']);
    });
  });

  describe('WaitForEndOfFrame', () => {
    it('resumes at late phase of the same or next frame', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('start');
        yield WaitForEndOfFrame();
        log.push('end-of-frame');
      }

      startCoroutine(state, eid, myCoroutine);
      expect(log).toEqual(['start']);

      // After one full step (including late), should resume
      state.step();
      expect(log).toEqual(['start', 'end-of-frame']);
    });

    it('does not resume during simulation phase', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('start');
        yield WaitForEndOfFrame();
        log.push('late-only');
        yield;
        log.push('next-frame');
      }

      startCoroutine(state, eid, myCoroutine);
      expect(log).toEqual(['start']);

      // step: fixed -> simulation (skips WaitForEndOfFrame) -> late (resumes) -> draw
      state.step();
      expect(log).toEqual(['start', 'late-only']);

      state.step();
      expect(log).toEqual(['start', 'late-only', 'next-frame']);
    });
  });

  describe('WaitForFixedUpdate', () => {
    it('resumes at next fixed update', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('start');
        yield WaitForFixedUpdate();
        log.push('after-fixed');
      }

      startCoroutine(state, eid, myCoroutine);
      expect(log).toEqual(['start']);

      // FIXED_TIMESTEP = 0.02. Need to accumulate >= 0.02.
      state.step(0.01);
      expect(log).toEqual(['start']);

      state.step(0.01);
      expect(log).toEqual(['start', 'after-fixed']);
    });
  });

  describe('WaitUntil', () => {
    it('resumes when condition becomes true', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      let condition = false;

      function* myCoroutine() {
        log.push('start');
        yield WaitUntil(() => condition);
        log.push('unblocked');
      }

      startCoroutine(state, eid, myCoroutine);
      state.step();
      expect(log).toEqual(['start']);

      state.step();
      expect(log).toEqual(['start']); // still false

      condition = true;
      state.step();
      expect(log).toEqual(['start', 'unblocked']);
    });
  });

  describe('WaitWhile', () => {
    it('resumes when condition becomes false', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      let condition = true;

      function* myCoroutine() {
        log.push('start');
        yield WaitWhile(() => condition);
        log.push('unblocked');
      }

      startCoroutine(state, eid, myCoroutine);
      state.step();
      expect(log).toEqual(['start']);

      state.step();
      expect(log).toEqual(['start']); // still true

      condition = false;
      state.step();
      expect(log).toEqual(['start', 'unblocked']);
    });
  });

  describe('yield null/undefined (default behavior)', () => {
    it('yield null resumes next frame', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      function* myCoroutine() {
        log.push('a');
        yield null;
        log.push('b');
        yield undefined;
        log.push('c');
      }

      startCoroutine(state, eid, myCoroutine);
      expect(log).toEqual(['a']);

      state.step();
      expect(log).toEqual(['a', 'b']);

      state.step();
      expect(log).toEqual(['a', 'b', 'c']);
    });
  });

  describe('mixed yield instructions', () => {
    it('coroutine can use multiple different yield types', () => {
      const eid = state.createEntity();
      const log: string[] = [];
      let flag = false;

      function* myCoroutine() {
        log.push('1');
        yield; // next frame
        log.push('2');
        yield WaitUntil(() => flag);
        log.push('3');
        yield WaitForEndOfFrame();
        log.push('4');
      }

      startCoroutine(state, eid, myCoroutine);
      expect(log).toEqual(['1']);

      state.step();
      expect(log).toEqual(['1', '2']);

      // WaitUntil not met yet
      state.step();
      expect(log).toEqual(['1', '2']);

      flag = true;
      state.step();
      // WaitUntil passes in simulation, WaitForEndOfFrame picked up in late
      expect(log).toEqual(['1', '2', '3', '4']);
    });
  });
});
