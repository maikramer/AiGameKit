import { beforeEach, describe, expect, it } from 'bun:test';
import type { System } from 'vibegame';
import { State } from 'vibegame';

describe('Scheduler late group', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('should run systems in order: simulation -> late -> draw', () => {
    const order: string[] = [];

    const simulationSystem: System = {
      group: 'simulation',
      update: () => order.push('simulation'),
    };

    const lateSystem: System = {
      group: 'late',
      update: () => order.push('late'),
    };

    const drawSystem: System = {
      group: 'draw',
      update: () => order.push('draw'),
    };

    state.registerSystem(simulationSystem);
    state.registerSystem(lateSystem);
    state.registerSystem(drawSystem);

    state.step();

    expect(order).toEqual(['simulation', 'late', 'draw']);
  });

  it('should run late group between simulation and draw even with multiple systems', () => {
    const order: string[] = [];

    state.registerSystem({ group: 'setup', update: () => order.push('setup') });
    state.registerSystem({ group: 'simulation', update: () => order.push('sim1') });
    state.registerSystem({ group: 'simulation', update: () => order.push('sim2') });
    state.registerSystem({ group: 'late', update: () => order.push('late1') });
    state.registerSystem({ group: 'late', update: () => order.push('late2') });
    state.registerSystem({ group: 'draw', update: () => order.push('draw') });

    state.step();

    expect(order).toEqual(['setup', 'sim1', 'sim2', 'late1', 'late2', 'draw']);
  });
});
