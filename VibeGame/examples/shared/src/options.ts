import {
  loadFromLocalStorage,
  onEvent,
  saveToLocalStorage,
  setMusicVolume,
  setSfxVolume,
  setQualityMode,
  MODAL_OPTION_CHANGED,
} from 'vibegame';
import type { QualityModeName, State } from 'vibegame';

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
/**
 * Clamp an option slider value (0–100) to a finite number. A malformed payload
 * (custom UI row, stale event) must never reach the mixer as NaN — a NaN gain
 * silently kills the whole bus instead of one slider.
 */
function sliderPercent(value: unknown): number {
  const v = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(v)) return 100;
  return Math.min(100, Math.max(0, v));
}

export function wireOptions(state: State, cfg: OptionsConfig = {}): void {
  onEvent(state, MODAL_OPTION_CHANGED, (payload) => {
    const p = payload as { id?: unknown; value?: unknown };
    const id = typeof p?.id === 'string' ? p.id : '';
    if (id === 'music-volume')
      setMusicVolume(state, sliderPercent(p.value) / 100);
    else if (id === 'sfx-volume')
      setSfxVolume(state, sliderPercent(p.value) / 100);
    else if (id === 'quality-mode') {
      const mode = String(p.value) as QualityModeName;
      setQualityMode(state, mode);
    } else if (p.id === 'save' && cfg.saveKey) {
      void saveToLocalStorage(state, cfg.saveKey)
        .then(() => cfg.onSave?.())
        .catch((err) => console.error('[options] save failed:', err));
    } else if (p.id === 'load' && cfg.saveKey) {
      void loadFromLocalStorage(state, cfg.saveKey)
        .then((restored) => cfg.onLoad?.(restored))
        .catch((err) => console.error('[options] load failed:', err));
    } else {
      cfg.onAction?.(id);
    }
  });
}
