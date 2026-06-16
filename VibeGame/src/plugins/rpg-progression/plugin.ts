import type { Parser, Plugin, Recipe } from '../../core';
import { getDataRegistry, getEventBus } from '../rpg-core';
import {
  DEFAULT_SKILL_POINTS_PER_LEVEL,
  ProgressionComponent,
  getProgressionConfig,
  setProgressionConfig,
} from './components';
import { ProgressionEventBridgeSystem } from './systems';

const progressionRecipe: Recipe = {
  name: 'Progression',
  components: ['progression'],
  overrides: { 'progression.level': 1 },
  parserAttributes: ['xp-curve', 'skill-points-per-level'],
};

function parseIntOrDefault(raw: unknown, fallback: number): number {
  if (raw === undefined || raw === null) return fallback;
  const n = parseInt(String(raw), 10);
  return Number.isFinite(n) ? n : fallback;
}

const progressionParser: Parser = ({ entity, element, state }) => {
  const xpCurveRaw = element.attributes['xp-curve'];
  const xpCurve =
    xpCurveRaw !== undefined &&
    xpCurveRaw !== null &&
    String(xpCurveRaw).length > 0
      ? String(xpCurveRaw)
      : getProgressionConfig(state, entity).xpCurve;
  const skillPointsPerLevel = parseIntOrDefault(
    element.attributes['skill-points-per-level'],
    DEFAULT_SKILL_POINTS_PER_LEVEL
  );
  setProgressionConfig(state, entity, { xpCurve, skillPointsPerLevel });
};

export const ProgressionPlugin: Plugin = {
  systems: [ProgressionEventBridgeSystem],
  components: { progression: ProgressionComponent },
  recipes: [progressionRecipe],
  config: {
    defaults: {
      progression: { xp: 0, level: 1, unspentPoints: 0, spent: 0 },
    },
    parsers: { Progression: progressionParser },
  },
  initialize(state) {
    getEventBus(state);
    const registry = getDataRegistry(state);
    if (!registry.has('xp-curve', 'default')) {
      registry.register('xp-curve', 'default', {
        id: 'default',
        fn: (level: number) => 5 + level,
      });
    }
  },
};
