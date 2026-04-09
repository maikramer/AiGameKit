import type { State } from '../../core';
import { internString } from '../hud/context';

const stateLocale = new WeakMap<State, string>();

const stateDictionaries = new WeakMap<State, Map<string, string>>();

export function setLocale(state: State, lang: string): void {
  stateLocale.set(state, lang);
}

export function getLocale(state: State): string {
  return stateLocale.get(state) ?? 'en';
}

export function loadDictionary(
  state: State,
  lang: string,
  dict: Record<string, string>
): void {
  let m = stateDictionaries.get(state);
  if (!m) {
    m = new Map();
    stateDictionaries.set(state, m);
  }
  for (const [k, v] of Object.entries(dict)) {
    m.set(`${lang}:${k}`, v);
  }
}

export function t(
  state: State,
  key: string,
  params?: Record<string, string>
): string {
  const lang = getLocale(state);
  const m = stateDictionaries.get(state);
  const full = `${lang}:${key}`;
  let s = m?.get(full) ?? key;
  if (params) {
    for (const [pk, pv] of Object.entries(params)) {
      s = s.replaceAll(`{${pk}}`, pv);
    }
  }
  return s;
}

export function resolveI18nKey(state: State, key: string): number {
  return internString(state, t(state, key));
}
