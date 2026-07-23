import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  ENGINE_DEFAULT_EN_DICTIONARY,
  ENGINE_DEFAULT_LOCALE,
  I18nConfig,
  I18nText,
  loadEngineDefaultDictionary,
  loadDictionary,
  setLocale,
  getLocale,
  t,
} from '../../../src/plugins/i18n';

const DEFAULT_KEYS = Object.keys(ENGINE_DEFAULT_EN_DICTIONARY);

describe('i18n bulk: engine default dictionary', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    loadEngineDefaultDictionary(state);
  });

  for (const key of DEFAULT_KEYS) {
    it(`t() resolves default key "${key}"`, () => {
      expect(t(state, key)).toBe(ENGINE_DEFAULT_EN_DICTIONARY[key]);
    });
  }
});

describe('i18n bulk: locale isolation', () => {
  const LOCALES = [
    'en',
    'pt',
    'de',
    'fr',
    'es',
    'ja',
    'ko',
    'zh',
    'it',
    'nl',
  ] as const;

  for (const lang of LOCALES) {
    it(`setLocale/getLocale round-trip for "${lang}"`, () => {
      const state = new State();
      setLocale(state, lang);
      expect(getLocale(state)).toBe(lang);
    });
  }

  for (let i = 0; i < LOCALES.length; i++) {
    const lang = LOCALES[i]!;
    it(`loadDictionary scopes keys to locale ${lang} (#${i})`, () => {
      const state = new State();
      setLocale(state, lang);
      loadDictionary(state, lang, { 'test.key': `value-${lang}` });
      expect(t(state, 'test.key')).toBe(`value-${lang}`);
      setLocale(state, 'en');
      if (lang === 'en') {
        expect(t(state, 'test.key')).toBe(`value-${lang}`);
      } else {
        expect(t(state, 'test.key')).toBe('test.key');
      }
    });
  }
});

describe('i18n bulk: parameter interpolation', () => {
  const PARAM_CASES: Array<{
    template: string;
    params: Record<string, string>;
    want: string;
  }> = [
    { template: 'Hello {name}', params: { name: 'Ada' }, want: 'Hello Ada' },
    {
      template: '{a}+{b}={c}',
      params: { a: '1', b: '2', c: '3' },
      want: '1+2=3',
    },
    {
      template: '{n} skill points',
      params: { n: '5' },
      want: '5 skill points',
    },
    {
      template: 'Requires: {names}',
      params: { names: 'Vitality' },
      want: 'Requires: Vitality',
    },
    { template: 'no params', params: {}, want: 'no params' },
    { template: '{x}{x}', params: { x: 'Z' }, want: 'ZZ' },
    {
      template: 'edge {missing}',
      params: { other: 'ok' },
      want: 'edge {missing}',
    },
    { template: '{empty}', params: { empty: '' }, want: '' },
    { template: 'unicode {u}', params: { u: 'ção' }, want: 'unicode ção' },
    { template: 'num {n}', params: { n: '42' }, want: 'num 42' },
  ];

  for (let i = 0; i < 10; i++) {
    for (const c of PARAM_CASES) {
      it(`interpolation case ${i}-${c.template.slice(0, 12)}`, () => {
        const state = new State();
        loadDictionary(state, 'en', { [`k${i}`]: c.template });
        expect(t(state, `k${i}`, c.params)).toBe(c.want);
      });
    }
  }
});

describe('i18n bulk: missing keys and fallbacks', () => {
  for (let i = 0; i < 25; i++) {
    it(`returns raw key for missing entry #${i}`, () => {
      const state = new State();
      setLocale(state, 'en');
      const key = `missing.key.${i}`;
      expect(t(state, key)).toBe(key);
    });
  }
});

describe('i18n bulk: components', () => {
  const I18N_TEXT_FIELDS = ['keyIndex', 'resolved'] as const;
  const I18N_CONFIG_FIELDS = ['autoEngineDefaults', 'applied'] as const;

  for (let eid = 1; eid <= 10; eid++) {
    for (const field of I18N_TEXT_FIELDS) {
      it(`I18nText.${field}[${eid}] read/write`, () => {
        const state = new State();
        const entity = state.createEntity();
        state.addComponent(entity, I18nText);
        I18nText[field][entity] = eid;
        expect(I18nText[field][entity]).toBe(eid);
      });
    }
    for (const field of I18N_CONFIG_FIELDS) {
      it(`I18nConfig.${field}[${eid}] read/write`, () => {
        const state = new State();
        const entity = state.createEntity();
        state.addComponent(entity, I18nConfig);
        I18nConfig[field][entity] = eid % 2;
        expect(I18nConfig[field][entity]).toBe(eid % 2);
      });
    }
  }
});

describe('i18n bulk: constants', () => {
  it('ENGINE_DEFAULT_LOCALE is en', () => {
    expect(ENGINE_DEFAULT_LOCALE).toBe('en');
  });

  for (let i = 0; i < 5; i++) {
    it(`default locale before setLocale is en (run ${i})`, () => {
      const state = new State();
      expect(getLocale(state)).toBe('en');
    });
  }
});
