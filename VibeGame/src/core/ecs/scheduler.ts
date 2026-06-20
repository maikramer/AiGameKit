import { commitRemovals } from 'bitecs';
import { TIME_CONSTANTS } from './constants';
import { sortSystemsByConstraints } from './ordering';
import type { State } from './state';
import { System } from './types';

export class Scheduler {
  private accumulator = 0;
  private readonly setup = new WeakSet<System>();
  private systemGroupCache = new Map<string, System[]>();
  private lastSystemsSize = 0;
  private maxFixedStepsPerFrame: number =
    TIME_CONSTANTS.MAX_FIXED_STEPS_PER_FRAME;

  getAccumulator(): number {
    return this.accumulator;
  }

  step(state: State, deltaTime = TIME_CONSTANTS.DEFAULT_DELTA) {
    const fixedDeltaTime = TIME_CONSTANTS.FIXED_TIMESTEP;
    const mutableTime = state.time as {
      deltaTime: number;
      unscaledDeltaTime: number;
      elapsed: number;
      frameCount: number;
      realtimeSinceStartup: number;
      fixedTime: number;
    };

    const unscaledDelta = deltaTime;
    const scaledDelta = unscaledDelta * state.time.timeScale;

    mutableTime.unscaledDeltaTime = unscaledDelta;
    mutableTime.realtimeSinceStartup += unscaledDelta;
    mutableTime.elapsed = mutableTime.realtimeSinceStartup;
    mutableTime.frameCount += 1;

    mutableTime.deltaTime = scaledDelta;
    this.accumulator += scaledDelta;

    commitRemovals(state.world);
    this.runSystemGroup(state, 'setup');

    const maxFixedSteps = this.maxFixedStepsPerFrame;
    let fixedSteps = 0;
    while (this.accumulator >= fixedDeltaTime && fixedSteps < maxFixedSteps) {
      commitRemovals(state.world);
      mutableTime.deltaTime = fixedDeltaTime;
      mutableTime.fixedTime += fixedDeltaTime;
      this.runSystemGroup(state, 'fixed');
      this.accumulator -= fixedDeltaTime;
      fixedSteps++;
    }
    if (fixedSteps >= maxFixedSteps) {
      this.accumulator = 0;
    }

    mutableTime.deltaTime = scaledDelta;
    commitRemovals(state.world);
    this.runSystemGroup(state, 'simulation');
    this.runSystemGroup(state, 'late');
    this.runSystemGroup(state, 'draw');
  }

  setMaxFixedStepsPerFrame(n: number): void {
    this.maxFixedStepsPerFrame = n;
  }

  getMaxFixedStepsPerFrame(): number {
    return this.maxFixedStepsPerFrame;
  }

  private runSystemGroup(
    state: State,
    group: 'setup' | 'simulation' | 'fixed' | 'late' | 'draw'
  ) {
    const systems = this.getSystemsByGroup(state, group);
    for (const system of systems) {
      if (!this.setup.has(system)) {
        system.setup?.(state);
        this.setup.add(system);
      }
      system.update?.(state);
    }
  }

  private getSystemsByGroup(
    state: State,
    group: 'setup' | 'simulation' | 'fixed' | 'late' | 'draw'
  ): System[] {
    if (state.systems.size !== this.lastSystemsSize) {
      this.systemGroupCache.clear();
      this.lastSystemsSize = state.systems.size;
    }

    const cacheKey = group;
    if (this.systemGroupCache.has(cacheKey)) {
      return this.systemGroupCache.get(cacheKey)!;
    }

    const allSystems = Array.from(state.systems);
    const systems = allSystems.filter(
      (system) => (system.group ?? 'simulation') === group
    );

    const sorted = sortSystemsByConstraints(systems, group, allSystems);
    this.systemGroupCache.set(cacheKey, sorted);
    return sorted;
  }
}
