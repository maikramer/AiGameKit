import {
  loadDictionary,
  loadEngineDefaultDictionary,
  setLocale,
} from 'aigamekit-vibegame';
import type { State } from 'aigamekit-vibegame';

export interface GameDictionary {
  en: Record<string, string>;
  pt: Record<string, string>;
}

/** Keys every example modal/options screen uses (see also options.ts). */
const SHARED_EN: Record<string, string> = {
  'modal.pause': 'Paused',
  'modal.hint': 'Press Q to resume',
  'options.music': 'Music',
  'options.quality': 'Quality',
  'options.sfx': 'Sound FX',
  'options.save': '💾 Save Game',
  'options.load': '📂 Load Game',
  'hud.saved': 'Game saved!',
  'hud.loaded': 'Save restored.',
};

const SHARED_PT: Record<string, string> = {
  'modal.pause': 'Pausa',
  'modal.hint': 'Aperte Q para voltar',
  'options.music': 'Música',
  'options.quality': 'Qualidade',
  'options.sfx': 'Efeitos',
  'options.save': '💾 Salvar jogo',
  'options.load': '📂 Carregar jogo',
  'hud.saved': 'Jogo salvo!',
  'hud.loaded': 'Progresso restaurado.',
};

/** Boot locale from the browser language (pt → pt, everything else → en). */
export function detectLocale(): 'en' | 'pt' {
  return navigator.language.startsWith('pt') ? 'pt' : 'en';
}

/**
 * Load the engine default dictionary + the shared modal/options keys plus the
 * game's own keys, and apply the boot locale. Returns the chosen locale.
 */
export function initI18n(state: State, game: GameDictionary): 'en' | 'pt' {
  loadEngineDefaultDictionary(state);
  loadDictionary(state, 'en', { ...SHARED_EN, ...game.en });
  loadDictionary(state, 'pt', { ...SHARED_PT, ...game.pt });
  const locale = detectLocale();
  setLocale(state, locale);
  return locale;
}
