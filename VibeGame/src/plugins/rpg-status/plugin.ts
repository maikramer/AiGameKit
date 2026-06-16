import type { Parser, Plugin, Recipe } from '../../core';
import { getDataRegistry } from '../rpg-core';
import type { StatusEffectDef } from '../rpg-core/types';
import {
  STATUS_KIND,
  StatusEffectComponent,
  applyStatus,
  ensureDeathSubscription,
} from './components';
import {
  StatusEffectEventBridgeSystem,
  StatusEffectTickSystem,
} from './systems';

const statusApplicationRecipe: Recipe = {
  name: 'StatusApplication',
  components: ['status-effect'],
  parserAttributes: ['status', 'target'],
};

const statusApplicationParser: Parser = ({ entity, element, state }) => {
  const raw = element.attributes.status;
  if (raw === undefined || raw === null) return;
  const defId = String(raw).trim();
  if (defId.length === 0) return;
  const target = String(element.attributes.target ?? 'self').trim();
  if (target !== 'self') return;
  applyStatus(state, entity, defId);
};

const DEFAULT_STATUS_DEFS: readonly StatusEffectDef[] = [
  {
    id: 'speed-buff',
    name: 'Speed Buff',
    duration: 10,
    modifiers: [{ stat: 'speed', magnitude: 1.3, stackMode: 'replace' }],
  },
  {
    id: 'heal-over-time',
    name: 'Heal Over Time',
    duration: 6,
    modifiers: [],
    tickInterval: 2,
    tickEffect: {
      kind: 'event-trigger',
      payload: { triggers: 'status:heal', amount: 5 },
    },
  },
  {
    id: 'poison',
    name: 'Poison',
    duration: 10,
    modifiers: [],
    tickInterval: 1,
    tickEffect: {
      kind: 'event-trigger',
      payload: { triggers: 'status:damage', amount: 2 },
    },
  },
];

export const StatusEffectsPlugin: Plugin = {
  systems: [StatusEffectTickSystem, StatusEffectEventBridgeSystem],
  recipes: [statusApplicationRecipe],
  components: { 'status-effect': StatusEffectComponent },
  config: {
    defaults: {
      'status-effect': { count: 0, version: 0 },
    },
    parsers: { StatusApplication: statusApplicationParser },
  },
  initialize(state) {
    ensureDeathSubscription(state);
    const registry = getDataRegistry(state);
    for (const def of DEFAULT_STATUS_DEFS) {
      if (!registry.has(STATUS_KIND, def.id)) {
        registry.register(STATUS_KIND, def.id, def);
      }
    }
  },
};
