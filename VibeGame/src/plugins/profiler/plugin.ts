import type { Plugin, Recipe } from '../../core';
import { ProfilerStats } from './components';
import { ProfilerDebugSystem } from './systems';

const profilerRecipe: Recipe = {
  name: 'Profiler',
  components: ['profiler-stats'],
  overrides: {
    'profiler-stats.lastFPS': 0,
    'profiler-stats.frameTimeMs': 0,
    'profiler-stats.systemCount': 0,
  },
};

export const ProfilerPlugin: Plugin = {
  systems: [ProfilerDebugSystem],
  recipes: [profilerRecipe],
  components: { 'profiler-stats': ProfilerStats },
  config: {
    defaults: {
      'profiler-stats': { lastFPS: 0, frameTimeMs: 0, systemCount: 0 },
    },
  },
};
