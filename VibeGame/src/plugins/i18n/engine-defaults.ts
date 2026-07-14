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
    'hud.target': 'Enemy',
    'hud.timer': 'Time',
    'hud.controls': 'Controls',
    'hud.controls.text': 'WASD move · Space jump · Q save · E load',
    'hud.mission.title': 'Mission',
    'hud.mission': 'Defeat the boss!',
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
    'modal.pause': 'Paused',
    'modal.resume': 'Resume',
    'modal.save': 'Save',
    'modal.load': 'Load',
    'modal.restart': 'Restart',
    'modal.hint': 'Press Esc to resume',
    'modal.level': 'Level',
    'modal.tab.menu': 'Menu',
    'modal.tab.skills': 'Skills',
    'modal.tab.inventory': 'Inventory',
    'modal.tab.options': 'Options',
    'modal.tab.system': 'System',
    'modal.tab.quests': 'Quests',
    'modal.tab.wiki': 'Wiki',
    'modal.skillPoints': '{n} skill points',
    'modal.skillRequires': 'Requires: {names}',
    'modal.inventoryEmpty': 'Bag is empty',
    'modal.inventorySelect': 'Select an item',
    'modal.inventoryNoDesc': 'No description.',
    'modal.wikiEmpty': 'No entries yet.',
    'modal.wikiGeneral': 'General',
    'quests.active': 'Active',
    'quests.completed': 'Completed',
    'quests.failed': 'Failed',
    'options.on': 'On',
    'options.off': 'Off',
  });

export function loadEngineDefaultDictionary(state: State): void {
  loadDictionary(state, ENGINE_DEFAULT_LOCALE, ENGINE_DEFAULT_EN_DICTIONARY);
}
