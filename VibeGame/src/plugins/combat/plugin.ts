import type { Plugin, Recipe } from '../../core';
import type { ParserParams, XMLValue } from '../../core';
import { getDataRegistry } from '../rpg-core/registry';
import {
  FactionComponent,
  Health,
  ProjectileConfig,
  ProjectileData,
  bindCombatState,
} from './components';
import {
  PROJECTILE_TEMPLATE_KIND,
  type ProjectileTemplate,
} from './projectile';
import {
  CombatDeathCleanupSystem,
  DamageResolutionSystem,
  ProjectileCleanupSystem,
} from './systems';

const factionRecipe: Recipe = {
  name: 'Faction',
  components: ['faction'],
};

const projectileTemplateRecipe: Recipe = {
  name: 'ProjectileTemplate',
  components: [],
  parserAttributes: [
    'id',
    'speed',
    'damage',
    'max-life',
    'sensor-radius',
    'faction',
  ],
};

function toNumber(value: XMLValue | undefined): number {
  return typeof value === 'number' ? value : Number(value ?? 0);
}

function projectileTemplateParser({ element, state }: ParserParams): void {
  const attrs = element.attributes;
  const id = String(attrs.id ?? '');
  if (!id) return;
  const template: ProjectileTemplate = {
    id,
    speed: toNumber(attrs.speed),
    damage: toNumber(attrs.damage),
    maxLife: toNumber(attrs['max-life']),
    sensorRadius:
      attrs['sensor-radius'] !== undefined
        ? toNumber(attrs['sensor-radius'])
        : undefined,
    faction: attrs.faction !== undefined ? String(attrs.faction) : undefined,
  };
  getDataRegistry(state).register(PROJECTILE_TEMPLATE_KIND, id, template);
}

export const CombatPlugin: Plugin = {
  systems: [
    DamageResolutionSystem,
    ProjectileCleanupSystem,
    CombatDeathCleanupSystem,
  ],
  recipes: [factionRecipe, projectileTemplateRecipe],
  components: {
    health: Health,
    projectileData: ProjectileData,
    projectileConfig: ProjectileConfig,
    faction: FactionComponent,
  },
  config: {
    defaults: {
      health: { current: 100, max: 100 },
      projectileData: { damage: 10, ownerEid: 0, lifetime: 3.0, age: 0 },
      projectileConfig: { speed: 0, maxLife: 3.0, damage: 10, faction: 0 },
      faction: { tag: 0 },
    },
    enums: {
      faction: {
        tag: { player: 0, enemy: 1, neutral: 2, merchant: 3 },
      },
    },
    parsers: {
      ProjectileTemplate: projectileTemplateParser,
    },
  },
  initialize(state) {
    bindCombatState(state);
  },
};
