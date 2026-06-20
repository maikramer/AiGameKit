import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';

describe('dispatchEvent listener isolation (C2)', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.headless = true;
  });

  it('still calls later listeners after an earlier one throws', () => {
    const eid = state.createEntity();
    let counter = 0;

    state.addEventListener(eid, 'foo', () => {
      throw new Error('boom');
    });
    state.addEventListener(eid, 'foo', () => {
      counter++;
    });

    state.dispatchEvent(eid, 'foo');

    expect(counter).toBe(1);
  });

  it('does not propagate the listener exception out of dispatchEvent', () => {
    const eid = state.createEntity();
    state.addEventListener(eid, 'foo', () => {
      throw new Error('boom');
    });

    expect(() => state.dispatchEvent(eid, 'foo')).not.toThrow();
  });

  it('keeps dispatching every listener when multiple throw', () => {
    const eid = state.createEntity();
    const thrown: string[] = [];

    state.addEventListener(eid, 'foo', () => {
      thrown.push('a');
      throw new Error('a');
    });
    state.addEventListener(eid, 'foo', () => {
      thrown.push('b');
      throw new Error('b');
    });
    state.addEventListener(eid, 'foo', () => {
      thrown.push('c');
    });

    expect(() => state.dispatchEvent(eid, 'foo')).not.toThrow();
    expect(thrown).toEqual(['a', 'b', 'c']);
  });

  it('addEventListenerOnce wrapper removes itself even when the wrapped callback throws', () => {
    const eid = state.createEntity();
    let calls = 0;

    state.addEventListenerOnce(eid, 'boom', () => {
      calls++;
      throw new Error('boom');
    });

    expect(() => state.dispatchEvent(eid, 'boom')).not.toThrow();
    expect(calls).toBe(1);

    expect(() => state.dispatchEvent(eid, 'boom')).not.toThrow();
    expect(calls).toBe(1);
  });
});
