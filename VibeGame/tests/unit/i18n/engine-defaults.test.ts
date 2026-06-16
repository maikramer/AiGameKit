import { afterEach, beforeEach, describe, expect, it, spyOn } from 'bun:test';
import { State } from 'vibegame';
import {
  ENGINE_DEFAULT_EN_DICTIONARY,
  loadEngineDefaultDictionary,
} from '../../../src/plugins/i18n/engine-defaults';
import { getLocale, t } from '../../../src/plugins/i18n/utils';

describe('I18n Engine Defaults', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  describe('ENGINE_DEFAULT_EN_DICTIONARY bundle', () => {
    it('includes HUD resource keys', () => {
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.health']).toBe('Health');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.xp']).toBe('XP');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.gold']).toBe('Gold');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.wood']).toBe('Wood');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.stone']).toBe('Stone');
    });

    it('includes HUD meta keys', () => {
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.boss']).toBe('Boss');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.timer']).toBe('Time');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hud.controls']).toBe('Controls');
    });

    it('includes harvest and merchant hint keys', () => {
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hint.harvest.wood']).toBe(
        'Chop Tree'
      );
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hint.harvest.stone']).toBe(
        'Mine Rock'
      );
      expect(ENGINE_DEFAULT_EN_DICTIONARY['hint.merchant']).toBe(
        'Talk to Merchant'
      );
    });

    it('includes banner keys', () => {
      expect(ENGINE_DEFAULT_EN_DICTIONARY['banner.level-up']).toBe('Level Up!');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['banner.victory']).toBe('Victory!');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['banner.defeat']).toBe('Defeat');
    });

    it('includes pause menu tab keys', () => {
      expect(ENGINE_DEFAULT_EN_DICTIONARY['menu.skills']).toBe('Skills');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['menu.inventory']).toBe('Inventory');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['menu.options']).toBe('Options');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['menu.resume']).toBe('Resume');
    });

    it('includes skill name keys', () => {
      expect(ENGINE_DEFAULT_EN_DICTIONARY['skill.vitality']).toBe('Vitality');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['skill.strength']).toBe('Strength');
      expect(ENGINE_DEFAULT_EN_DICTIONARY['skill.agility']).toBe('Agility');
    });

    it('is frozen (immutable)', () => {
      expect(Object.isFrozen(ENGINE_DEFAULT_EN_DICTIONARY)).toBe(true);
    });
  });

  describe('loadEngineDefaultDictionary', () => {
    it('populates keys so t() resolves to English defaults', () => {
      loadEngineDefaultDictionary(state);
      expect(getLocale(state)).toBe('en');
      expect(t(state, 'hud.health')).toBe('Health');
      expect(t(state, 'banner.level-up')).toBe('Level Up!');
      expect(t(state, 'hint.merchant')).toBe('Talk to Merchant');
      expect(t(state, 'menu.resume')).toBe('Resume');
      expect(t(state, 'skill.vitality')).toBe('Vitality');
    });

    it('loads every key from ENGINE_DEFAULT_EN_DICTIONARY', () => {
      loadEngineDefaultDictionary(state);
      for (const [key, expected] of Object.entries(
        ENGINE_DEFAULT_EN_DICTIONARY
      )) {
        expect(t(state, key)).toBe(expected);
      }
    });

    it('is idempotent (calling twice does not throw or overwrite with undefined)', () => {
      expect(() => {
        loadEngineDefaultDictionary(state);
        loadEngineDefaultDictionary(state);
      }).not.toThrow();
      expect(t(state, 'hud.gold')).toBe('Gold');
    });
  });

  describe('fallback for missing keys', () => {
    let warnSpy: ReturnType<typeof spyOn>;

    beforeEach(() => {
      warnSpy = spyOn(console, 'warn').mockImplementation(() => undefined);
    });

    afterEach(() => {
      warnSpy.mockRestore();
    });

    it('returns the key string itself when key is missing', () => {
      const out = t(state, 'nonexistent.key');
      expect(out).toBe('nonexistent.key');
      expect(out).not.toBe('');
    });

    it('emits a console.warn in dev when key is missing', () => {
      t(state, 'another.missing.key');
      expect(warnSpy).toHaveBeenCalledTimes(1);
      const firstCallArg = String(warnSpy.mock.calls[0][0]);
      expect(firstCallArg).toContain('another.missing.key');
    });

    it('does not warn when the key exists', () => {
      loadEngineDefaultDictionary(state);
      t(state, 'hud.health');
      expect(warnSpy).not.toHaveBeenCalled();
    });
  });
});
