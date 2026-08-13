import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import type { System } from 'vibegame';

/**
 * `before`/`after` accept a system object **or** its `name`. The name form
 * exists for systems that cannot import each other (plugin ↔ systems cycles).
 *
 * Resolution used to be object identity only, so a string entry matched
 * nothing and the ordering constraint was dropped without a word — the system
 * still ran, just in the wrong place.
 */
describe('system ordering by name', () => {
  let state: State;
  let order: string[];

  function tracer(name: string, extra: Partial<System> = {}): System {
    return {
      name,
      group: 'simulation',
      update: () => {
        order.push(name);
      },
      ...extra,
    };
  }

  beforeEach(() => {
    state = new State();
    order = [];
  });

  it('honours an `after` constraint written as a name', () => {
    // Registered in the order that would come out wrong without the constraint.
    state.registerSystem(tracer('B', { after: ['A'] }));
    state.registerSystem(tracer('A'));
    state.step(0.016);
    expect(order).toEqual(['A', 'B']);
  });

  it('honours a `before` constraint written as a name', () => {
    state.registerSystem(tracer('Y'));
    state.registerSystem(tracer('X', { before: ['Y'] }));
    state.step(0.016);
    expect(order).toEqual(['X', 'Y']);
  });

  it('mixes name and object entries in one constraint list', () => {
    const first = tracer('First');
    state.registerSystem(tracer('Last', { after: [first, 'Middle'] }));
    state.registerSystem(tracer('Middle', { after: [first] }));
    state.registerSystem(first);
    state.step(0.016);
    expect(order).toEqual(['First', 'Middle', 'Last']);
  });

  it('ignores a name that matches no registered system', () => {
    state.registerSystem(tracer('Only', { after: ['NotRegistered'] }));
    expect(() => state.step(0.016)).not.toThrow();
    expect(order).toEqual(['Only']);
  });

  it('still detects a cycle expressed through names', () => {
    state.registerSystem(tracer('P', { after: ['Q'] }));
    state.registerSystem(tracer('Q', { after: ['P'] }));
    expect(() => state.step(0.016)).toThrow(/[Cc]ircular/);
  });

  it('rejects an `after` that names a system in another group', () => {
    state.registerSystem(tracer('Sim'));
    state.registerSystem(tracer('Draw', { group: 'draw', after: ['Sim'] }));
    expect(() => state.step(0.016)).toThrow(
      /Draw.*after.*Sim.*draw vs simulation/
    );
  });
});
