import { logger } from '../../core/utils/logger';
import type { Parser, Plugin, Recipe } from '../../core';
import { SpawnGateComponent } from './components';
import { SpawnGateSystem, gateEntity } from './systems';

export const spawnGateRecipe: Recipe = {
  name: 'SpawnGate',
  components: [],
  parserAttributes: ['target-entity', 'y-fallback', 'skin-distance'],
};

/**
 * `<SpawnGate target-entity="hero" y-fallback="50"/>` resolves the named target
 * entity and marks it for gating. The directive must appear after the target
 * in document order; an unresolved name is a no-op (warned) so a mis-ordered
 * gate never crashes scene load.
 */
export const spawnGateParser: Parser = ({ element, state }) => {
  const targetName = element.attributes['target-entity'];
  if (targetName === undefined || targetName === null) return;
  const name = String(targetName).trim();
  if (!name) return;

  const targetEid = state.getEntityByName(name);
  if (targetEid === null) {
    logger.warn(
      `[SpawnGate] target-entity "${name}" not found at parse time; gate skipped. Place <SpawnGate> after the target entity.`
    );
    return;
  }

  const yFallbackRaw = element.attributes['y-fallback'];
  const yFallback =
    yFallbackRaw !== undefined && yFallbackRaw !== null
      ? Number(yFallbackRaw)
      : undefined;

  const skinRaw = element.attributes['skin-distance'];
  const skinDistance =
    skinRaw !== undefined && skinRaw !== null && !Number.isNaN(Number(skinRaw))
      ? Number(skinRaw)
      : undefined;

  gateEntity(state, targetEid, { yFallback, skinDistance });
};

export const SpawnGatePlugin: Plugin = {
  systems: [SpawnGateSystem],
  recipes: [spawnGateRecipe],
  components: { 'spawn-gate': SpawnGateComponent },
  config: {
    parsers: {
      SpawnGate: spawnGateParser,
    },
    defaults: {
      'spawn-gate': {
        ready: 0,
        yOffset: 0,
        skinDistance: 0.05,
      },
    },
  },
};
