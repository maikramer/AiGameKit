import type { GameDictionary } from '../../../shared/src/i18n';

/**
 * Game-specific strings. Modal/options/save keys come from the shared kit
 * (`examples/shared/src/i18n.ts`) and are merged underneath these.
 */
export const FARM_DICTIONARY: GameDictionary = {
  en: {
    'modal.tab.options': 'System',
    'options.controls':
      'WASD move · Q/E rotate camera · wheel zoom · 1-6 tools · J use/interact · Space jump · Esc pause',
    'farm.title': 'Simple Farm',
    'farm.subtitle': 'Waking the valley…',
    'farm.prompt.sleep': 'Sleep until morning',
    'interact.chop': 'Chop',
    'interact.mine': 'Mine',
    'item.wood': 'Wood',
    'item.stone': 'Stone',
    'item.berry': 'Berries',
    'item.ore': 'Ore',
    'interact.pick': 'Pick',
    'farm.prompt.shop': 'Shop',
    'farm.toast.slept': 'You slept — stamina restored',
    'farm.toast.day':
      'Crops: {grown} grew · {ripened} ripe · {withered} withered',
    'farm.toast.tired': 'Too tired… sleep to recover',
    'farm.toast.no_seeds': 'No seeds left — buy some at the stall',
    'farm.toast.bought': 'Bought {item}',
    'farm.toast.sold': 'Sold everything for {gold}g',
  },
  pt: {
    'modal.tab.options': 'Sistema',
    'options.controls':
      'WASD mover · Q/E rodar câmara · roda do rato zoom · 1-6 ferramentas · J usar/interagir · Espaço saltar · Esc pausa',
    'farm.title': 'Simple Farm',
    'farm.subtitle': 'A acordar o vale…',
    'farm.prompt.sleep': 'Dormir até de manhã',
    'interact.chop': 'Cortar',
    'interact.mine': 'Picar',
    'item.wood': 'Madeira',
    'item.stone': 'Pedra',
    'item.berry': 'Bagas',
    'item.ore': 'Minério',
    'interact.pick': 'Apanhar',
    'farm.prompt.shop': 'Loja',
    'farm.toast.slept': 'Dormiste — stamina restaurada',
    'farm.toast.day':
      'Culturas: {grown} cresceram · {ripened} maduras · {withered} secaram',
    'farm.toast.tired': 'Exausto… dorme para recuperar',
    'farm.toast.no_seeds': 'Sem sementes — compra na banca',
    'farm.toast.bought': 'Compraste {item}',
    'farm.toast.sold': 'Vendeste tudo por {gold}g',
  },
};
