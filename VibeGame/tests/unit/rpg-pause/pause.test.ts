import { beforeEach, describe, expect, it } from 'bun:test';
import {
  PAUSE_CHANGED,
  PAUSE_POPPED,
  PAUSE_PUSHED,
  PauseCoordinatorPlugin,
  PauseSystem,
  RpgCoreEventsPlugin,
  State,
  emitEvent,
  getActiveModal,
  getPauseState,
  isInputMovementSuppressed,
  onEvent,
  popModal,
  pushModal,
  setInputMovementSuppressed,
  setTimeScale,
  suppressInput,
  isPaused,
} from 'vibegame';

describe('PauseCoordinatorPlugin', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(RpgCoreEventsPlugin);
    state.registerPlugin(PauseCoordinatorPlugin);
    setInputMovementSuppressed(false);
  });

  describe('pushModal pauses time + input', () => {
    it('sets paused=true, timeScale=0, input suppressed immediately', () => {
      pushModal(state, 'pause-menu');

      expect(isPaused(state)).toBe(true);
      expect(state.time.timeScale).toBe(0);
      expect(isInputMovementSuppressed()).toBe(true);
    });

    it('getActiveModal returns the pushed modal name', () => {
      pushModal(state, 'pause-menu');
      expect(getActiveModal(state)).toBe('pause-menu');
    });

    it('emits pause:pushed and pause:changed with the modal name', () => {
      const pushed: unknown[] = [];
      const changed: unknown[] = [];
      onEvent(state, PAUSE_PUSHED, (p) => pushed.push(p));
      onEvent(state, PAUSE_CHANGED, (p) => changed.push(p));

      pushModal(state, 'pause-menu');

      expect(pushed).toEqual([{ modal: 'pause-menu', stack: ['pause-menu'] }]);
      expect(changed.length).toBeGreaterThanOrEqual(1);
    });

    it('emits no pause:changed when already paused', () => {
      pushModal(state, 'a');
      let changed = 0;
      onEvent(state, PAUSE_CHANGED, () => changed++);
      pushModal(state, 'b');
      expect(changed).toBe(0);
    });
  });

  describe('popModal resumes when stack empty', () => {
    it('restores timeScale=1 and input when the last modal is popped', () => {
      pushModal(state, 'a');
      pushModal(state, 'b');

      popModal(state);
      expect(isPaused(state)).toBe(true);
      expect(state.time.timeScale).toBe(0);

      popModal(state);
      expect(isPaused(state)).toBe(false);
      expect(state.time.timeScale).toBe(1);
      expect(isInputMovementSuppressed()).toBe(false);
    });

    it('emits pause:popped and pause:changed only when transitioning to unpaused', () => {
      pushModal(state, 'a');
      pushModal(state, 'b');

      const popped: unknown[] = [];
      let changedCount = 0;
      onEvent(state, PAUSE_POPPED, (p) => popped.push(p));
      onEvent(state, PAUSE_CHANGED, () => changedCount++);

      popModal(state);
      expect(popped).toEqual([{ modal: 'b', stack: ['a'] }]);
      expect(changedCount).toBe(0);

      popModal(state);
      expect(popped).toHaveLength(2);
      expect(changedCount).toBe(1);
    });

    it('popModal(name) removes the named modal from anywhere in the stack', () => {
      pushModal(state, 'a');
      pushModal(state, 'b');
      pushModal(state, 'c');

      popModal(state, 'a');
      expect(getActiveModal(state)).toBe('c');
      expect(isPaused(state)).toBe(true);
      expect(getPauseState(state).modalStack).toEqual(['b', 'c']);
    });

    it('popModal with unknown name is a graceful no-op', () => {
      pushModal(state, 'a');
      expect(() => popModal(state, 'nonexistent')).not.toThrow();
      expect(isPaused(state)).toBe(true);
      expect(getActiveModal(state)).toBe('a');
    });

    it('popModal on empty stack is a graceful no-op', () => {
      expect(() => popModal(state)).not.toThrow();
      expect(isPaused(state)).toBe(false);
    });
  });

  describe('multiple modal stack ordering', () => {
    it('stays paused until every modal has been popped (LIFO)', () => {
      pushModal(state, 'menu');
      pushModal(state, 'shop');
      pushModal(state, 'dialog');

      expect(isPaused(state)).toBe(true);
      expect(getActiveModal(state)).toBe('dialog');

      popModal(state);
      expect(getActiveModal(state)).toBe('shop');

      popModal(state);
      expect(getActiveModal(state)).toBe('menu');

      popModal(state);
      expect(getActiveModal(state)).toBeUndefined();
      expect(isPaused(state)).toBe(false);
    });
  });

  describe('setTimeScale', () => {
    it('sets the active time scale when not paused', () => {
      setTimeScale(state, 0.5);
      expect(state.time.timeScale).toBe(0.5);
    });

    it('is forced to 0 while paused and restored on resume', () => {
      setTimeScale(state, 0.5);
      pushModal(state, 'm');
      expect(state.time.timeScale).toBe(0);

      popModal(state);
      expect(state.time.timeScale).toBe(0.5);
    });
  });

  describe('suppressInput', () => {
    it('toggles input suppression directly when not paused', () => {
      suppressInput(state, true);
      expect(isInputMovementSuppressed()).toBe(true);
      suppressInput(state, false);
      expect(isInputMovementSuppressed()).toBe(false);
    });

    it('does not resume input while a modal is open', () => {
      pushModal(state, 'm');
      suppressInput(state, false);
      popModal(state);
      expect(isInputMovementSuppressed()).toBe(false);
    });
  });

  describe('PauseSystem re-syncs each frame', () => {
    it('forces timeScale back to 0 when a modal is open (defence against tampering)', () => {
      pushModal(state, 'm');
      state.time.timeScale = 1;

      state.registerSystem(PauseSystem);
      state.step();

      expect(state.time.timeScale).toBe(0);
    });

    it('forces input suppression back on when a modal is open', () => {
      pushModal(state, 'm');
      setInputMovementSuppressed(false);

      state.registerSystem(PauseSystem);
      state.step();

      expect(isInputMovementSuppressed()).toBe(true);
    });

    it('restores the desired scale when unpaused each frame', () => {
      setTimeScale(state, 0.25);
      state.time.timeScale = 1;

      state.registerSystem(PauseSystem);
      state.step();

      expect(state.time.timeScale).toBe(0.25);
    });
  });

  describe('event const names', () => {
    it('exports documented const strings', () => {
      expect(PAUSE_PUSHED).toBe('pause:pushed');
      expect(PAUSE_POPPED).toBe('pause:popped');
      expect(PAUSE_CHANGED).toBe('pause:changed');
    });
  });

  describe('per-State isolation', () => {
    it('different states maintain independent pause stacks', () => {
      const other = new State();
      other.registerPlugin(RpgCoreEventsPlugin);
      other.registerPlugin(PauseCoordinatorPlugin);

      pushModal(state, 'a');
      expect(isPaused(other)).toBe(false);
      expect(getActiveModal(other)).toBeUndefined();
    });
  });

  describe('emitEvent interop', () => {
    it('a custom handler can listen for pause:changed via emitEvent plumbing', () => {
      let fired = false;
      onEvent(state, PAUSE_CHANGED, () => {
        fired = true;
      });
      emitEvent(state, PAUSE_CHANGED, null);
      expect(fired).toBe(true);
    });
  });
});
