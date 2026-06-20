import { logger } from '../../core/utils/logger';
import type { ParserParams, Plugin, Recipe } from '../../core';
import { AiStateComponent, MELEE_AI_KIND } from './components';
import type { MeleeAiConfig } from './components';
import { setMeleeAiConfig } from './components';
import { getDataRegistry } from '../rpg-core/registry';
import { RpgAiSystem } from './systems';

export const meleeAiRecipe: Recipe = {
  name: 'MeleeAi',
  components: ['aiState', 'health', 'faction', 'transform', 'gltfPending'],
  parserAttributes: ['preset'],
};

function meleeAiParser({ entity, element, state }: ParserParams): void {
  const rawPreset = element.attributes.preset;
  const preset = typeof rawPreset === 'string' ? rawPreset : undefined;
  if (!preset) return;
  const cfg = getDataRegistry(state).get<MeleeAiConfig>(MELEE_AI_KIND, preset);
  if (!cfg) {
    logger.warn(`[rpg-ai] No '${MELEE_AI_KIND}' preset named "${preset}"`);
    return;
  }
  setMeleeAiConfig(state, entity, cfg);
}

export const RpgAiPlugin: Plugin = {
  systems: [RpgAiSystem],
  recipes: [meleeAiRecipe],
  components: { aiState: AiStateComponent },
  config: {
    parsers: { MeleeAi: meleeAiParser },
  },
};
