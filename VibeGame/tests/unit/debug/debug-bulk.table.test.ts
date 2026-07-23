import { beforeEach, describe, expect, it } from 'bun:test';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';
import { State } from 'vibegame';
import {
  applyPostFxToggle,
  DEFAULT_POSTFX_BINDINGS,
  parsePostFxBindings,
} from '../../../src/plugins/debug/postfx-toggle';
import {
  getDebugRegistry,
  getDebugRegistryHandle,
  registerDebugAction,
  registerDebugVar,
} from '../../../src/plugins/debug/registry';

function makePostprocessing() {
  return {
    bloom: new Uint8Array(MAX_ENTITIES),
    chromaticAberration: new Uint8Array(MAX_ENTITIES),
    vignette: new Uint8Array(MAX_ENTITIES),
    aa: new Uint8Array(MAX_ENTITIES),
    toneMapping: new Uint8Array(MAX_ENTITIES),
    ssao: new Uint8Array(MAX_ENTITIES),
  };
}

describe('parsePostFxBindings — effect aliases', () => {
  const pairs: Array<[string, string]> = [
    ['bloom', 'bloom'],
    ['ca', 'chromaticAberration'],
    ['chromaticaberration', 'chromaticAberration'],
    ['vignette', 'vignette'],
    ['aa', 'aa'],
    ['tonemapping', 'toneMapping'],
    ['ssao', 'ssao'],
  ];

  for (const [alias, field] of pairs) {
    it(`Digit9:${alias}`, () => {
      const m = parsePostFxBindings(`Digit9:${alias}`);
      expect(m.get('Digit9')).toBe(field);
    });
  }

  for (let i = 0; i < 15; i += 1) {
    it(`ignores junk segment ${i}`, () => {
      const m = parsePostFxBindings(`Digit${i}:unknown,KeyQ:bloom`);
      expect(m.get(`Digit${i}`)).toBeUndefined();
      expect(m.get('KeyQ')).toBe('bloom');
    });
  }
});

describe('DEFAULT_POSTFX_BINDINGS keys', () => {
  for (const [code, effect] of DEFAULT_POSTFX_BINDINGS) {
    it(`${code} → ${effect}`, () => {
      expect(DEFAULT_POSTFX_BINDINGS.get(code)).toBe(effect);
    });
  }
});

describe('applyPostFxToggle modulus cycles', () => {
  const eid = 0;
  const debounce = new Set<string>();
  const fields = makePostprocessing();

  const mods: Record<string, number> = {
    bloom: 2,
    chromaticAberration: 2,
    vignette: 2,
    aa: 3,
    toneMapping: 5,
    ssao: 2,
  };

  for (const [field, mod] of Object.entries(mods)) {
    for (let start = 0; start < mod; start += 1) {
      it(`${field} from ${start}`, () => {
        debounce.clear();
        const pp = makePostprocessing();
        (pp as Record<string, Uint8Array>)[field]![eid] = start;
        const bindings = new Map([['KeyT', field as keyof typeof pp]]);
        const down: (code: string) => boolean = (code) => code === 'KeyT';
        applyPostFxToggle({
          bindings,
          isKeyDown: down,
          debounce,
          postprocessing: pp as never,
          eid,
        });
        expect(pp[field as keyof typeof pp][eid]).toBe((start + 1) % mod);
      });
    }
  }
});

describe('debug registry actions and vars', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  for (let i = 0; i < 25; i += 1) {
    it(`registers action cmd-${i}`, () => {
      const handle = getDebugRegistryHandle(state);
      registerDebugAction(state, `cmd-${i}`, () => i * 2);
      expect(handle.hasAction(`cmd-${i}`)).toBe(true);
      expect(handle.callAction(`cmd-${i}`)).toBe(i * 2);
    });
  }

  for (let i = 0; i < 25; i += 1) {
    it(`registers var v-${i}`, () => {
      let value = i;
      registerDebugVar(
        state,
        `v-${i}`,
        () => value,
        (n) => {
          value = Number(n);
        }
      );
      const handle = getDebugRegistryHandle(state);
      expect(handle.hasVar(`v-${i}`)).toBe(true);
      expect(handle.getVar(`v-${i}`)).toBe(i);
      expect(handle.setVar(`v-${i}`, i + 10)).toBe(true);
      expect(handle.getVar(`v-${i}`)).toBe(i + 10);
    });
  }

  it('getDebugRegistry returns same maps', () => {
    registerDebugAction(state, 'ping', () => 'pong');
    const reg = getDebugRegistry(state);
    expect(reg.actions.has('ping')).toBe(true);
    expect(getDebugRegistryHandle(state).actionNames()).toContain('ping');
  });
});
