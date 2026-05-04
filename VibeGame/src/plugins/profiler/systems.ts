import type { State, System } from '../../core';
import { ProfilerStats } from './components';

const BUFFER_SIZE = 60;
const TOP_N = 5;

const frameTimes: number[] = [];
let profilerEntity = -1;
let lastLogFrame = 0;
let frameStartTime = 0;
const systemTimings = new Map<System, number[]>();

function getSystemName(system: System): string {
  const obj = system as Record<string, unknown>;
  return (
    (obj.name as string) || (obj.constructor?.name as string) || 'Anonymous'
  );
}

function instrumentSystems(state: State): void {
  for (const system of state.systems) {
    if (systemTimings.has(system)) continue;

    const originalUpdate = system.update?.bind(system);
    if (!originalUpdate) {
      systemTimings.set(system, []);
      continue;
    }

    const timings: number[] = [];
    systemTimings.set(system, timings);

    const wrappedUpdate = (s: State) => {
      const start = performance.now();
      originalUpdate(s);
      const elapsed = performance.now() - start;
      timings.push(elapsed);
      if (timings.length > BUFFER_SIZE) timings.shift();
    };

    Object.defineProperty(system, 'update', {
      value: wrappedUpdate,
      writable: false,
      configurable: true,
    });
  }
}

function getTopSystems(count: number): string {
  const averages: Array<{ name: string; avg: number }> = [];

  for (const [system, timings] of systemTimings) {
    if (timings.length === 0) continue;
    const avg = timings.reduce((a, b) => a + b, 0) / timings.length;
    averages.push({ name: getSystemName(system), avg });
  }

  averages.sort((a, b) => b.avg - a.avg);

  return averages
    .slice(0, count)
    .map((s) => `${s.name}(${s.avg.toFixed(1)}ms)`)
    .join(', ');
}

export const ProfilerDebugSystem: System = {
  group: 'late',
  last: true,

  setup(state: State): void {
    instrumentSystems(state);

    const eid = state.createEntity();
    state.addComponent(eid, ProfilerStats);
    state.setEntityName('__profiler__', eid);
    profilerEntity = eid;
  },

  update(state: State): void {
    instrumentSystems(state);

    const now = performance.now();
    if (frameStartTime > 0) {
      const frameTime = now - frameStartTime;
      frameTimes.push(frameTime);
      if (frameTimes.length > BUFFER_SIZE) frameTimes.shift();
    }
    frameStartTime = now;

    if (profilerEntity < 0 || !state.exists(profilerEntity)) return;

    const fps =
      frameTimes.length > 0
        ? 1000 / (frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length)
        : 0;
    const avgFrameTime =
      frameTimes.length > 0
        ? frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length
        : 0;

    ProfilerStats.lastFPS[profilerEntity] = fps;
    ProfilerStats.frameTimeMs[profilerEntity] = avgFrameTime;
    ProfilerStats.systemCount[profilerEntity] = state.systems.size;

    const frameCount = state.time.frameCount;
    if (
      frameCount - lastLogFrame >= BUFFER_SIZE &&
      frameTimes.length >= BUFFER_SIZE
    ) {
      lastLogFrame = frameCount;
      const topSystems = getTopSystems(TOP_N);
      console.log(
        `[profiler] FPS=${fps.toFixed(1)} | frame=${avgFrameTime.toFixed(1)}ms | top systems: ${topSystems}`
      );
    }
  },
};
