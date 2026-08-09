import {
  loadFromLocalStorage,
  onEvent,
  saveToLocalStorage,
  setMusicVolume,
  setSfxVolume,
  MODAL_OPTION_CHANGED,
} from 'vibegame';
import type { State } from 'vibegame';

export interface OptionsConfig {
  /** LocalStorage key that enables the Save/Load option rows. */
  saveKey?: string;
  /** Fired after a successful save (e.g. play a SFX). */
  onSave?: () => void;
  /** Fired after a load attempt with whether it found a save. */
  onLoad?: (restored: boolean) => void;
  /** Fired for any other option row id (e.g. a game-specific button). */
  onAction?: (id: string) => void;
}

/**
 * Wire the engine <OptionsTab> rows to the audio mixer (and optional
 * Save/Load buttons). Uses the mixer API (not raw bus volume) so BOTH
 * declarative <MusicLayer> BGM and bank playSound clips follow the sliders.
 */
export function wireOptions(state: State, cfg: OptionsConfig = {}): void {
  onEvent(state, MODAL_OPTION_CHANGED, (payload) => {
    const p = payload as { id: string; value: number };
    if (p.id === 'music-volume') setMusicVolume(state, p.value / 100);
    else if (p.id === 'sfx-volume') setSfxVolume(state, p.value / 100);
    else if (p.id === 'save' && cfg.saveKey) {
      void saveToLocalStorage(state, cfg.saveKey)
        .then(() => cfg.onSave?.())
        .catch((err) => console.error('[options] save failed:', err));
    } else if (p.id === 'load' && cfg.saveKey) {
      void loadFromLocalStorage(state, cfg.saveKey)
        .then((restored) => cfg.onLoad?.(restored))
        .catch((err) => console.error('[options] load failed:', err));
    } else {
      cfg.onAction?.(p.id);
    }
  });
}
