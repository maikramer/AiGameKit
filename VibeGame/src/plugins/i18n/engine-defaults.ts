import type { State } from '../../core';
import { loadDictionary } from './utils';

export const ENGINE_DEFAULT_LOCALE = 'en' as const;

export const ENGINE_DEFAULT_EN_DICTIONARY: Readonly<Record<string, string>> =
  Object.freeze({
    'hud.health': 'Health',
    'hud.xp': 'XP',
    'hud.gold': 'Gold',
    'hud.wood': 'Wood',
    'hud.stone': 'Stone',
    'hud.boss': 'Boss',
    'hud.timer': 'Time',
    'hud.controls': 'Controls',
    'hint.harvest.wood': 'Chop Tree',
    'hint.harvest.stone': 'Mine Rock',
    'hint.merchant': 'Talk to Merchant',
    'banner.level-up': 'Level Up!',
    'banner.victory': 'Victory!',
    'banner.defeat': 'Defeat',
    'menu.skills': 'Skills',
    'menu.inventory': 'Inventory',
    'menu.options': 'Options',
    'menu.resume': 'Resume',
    'skill.vitality': 'Vitality',
    'skill.strength': 'Strength',
    'skill.agility': 'Agility',
  });

export function loadEngineDefaultDictionary(state: State): void {
  loadDictionary(state, ENGINE_DEFAULT_LOCALE, ENGINE_DEFAULT_EN_DICTIONARY);
}
