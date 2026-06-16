import { beforeEach, describe, expect, it } from 'bun:test';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';
import {
  applyPostFxToggle,
  parsePostFxBindings,
  DEFAULT_POSTFX_BINDINGS,
} from '../../../src/plugins/debug/postfx-toggle';
import type {
  PostFxKeyBindings,
  IsKeyDownFn,
} from '../../../src/plugins/debug/postfx-toggle';

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

describe('PostFx Debug Toggle', () => {
  describe('parsePostFxBindings', () => {
    it('parses key:effect pairs', () => {
      const m = parsePostFxBindings('Digit1:bloom,Digit2:ca');
      expect(m.get('Digit1')).toBe('bloom');
      expect(m.get('Digit2')).toBe('chromaticAberration');
    });

    it('maps short aliases to full field names', () => {
      const m = parsePostFxBindings('Digit2:ca,Digit4:aa');
      expect(m.get('Digit2')).toBe('chromaticAberration');
      expect(m.get('Digit4')).toBe('aa');
    });

    it('accepts full field names verbatim', () => {
      const m = parsePostFxBindings('Digit3:chromaticAberration');
      expect(m.get('Digit3')).toBe('chromaticAberration');
    });

    it('ignores unknown effect names', () => {
      const m = parsePostFxBindings('Digit1:bogus,Digit2:bloom');
      expect(m.has('Digit1')).toBe(false);
      expect(m.get('Digit2')).toBe('bloom');
    });

    it('returns empty map for empty string', () => {
      expect(parsePostFxBindings('').size).toBe(0);
    });
  });

  describe('DEFAULT_POSTFX_BINDINGS', () => {
    it('maps Digit1-6 to the six effects', () => {
      expect(DEFAULT_POSTFX_BINDINGS.get('Digit1')).toBe('bloom');
      expect(DEFAULT_POSTFX_BINDINGS.get('Digit2')).toBe('chromaticAberration');
      expect(DEFAULT_POSTFX_BINDINGS.get('Digit3')).toBe('vignette');
      expect(DEFAULT_POSTFX_BINDINGS.get('Digit4')).toBe('aa');
      expect(DEFAULT_POSTFX_BINDINGS.get('Digit5')).toBe('ssao');
      expect(DEFAULT_POSTFX_BINDINGS.get('Digit6')).toBe('toneMapping');
      expect(DEFAULT_POSTFX_BINDINGS.size).toBe(6);
    });
  });

  describe('applyPostFxToggle — binary effects (bloom)', () => {
    let pp: ReturnType<typeof makePostprocessing>;
    let debounce: Set<string>;
    const eid = 5;
    const noKeys: IsKeyDownFn = () => false;

    beforeEach(() => {
      pp = makePostprocessing();
      debounce = new Set();
      pp.bloom[eid] = 1;
    });

    it('toggles bloom off when Digit1 pressed', () => {
      const keys: IsKeyDownFn = (c) => c === 'Digit1';
      const res = applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: keys,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.bloom[eid]).toBe(0);
      expect(res.toggled).toEqual(['bloom']);
    });

    it('toggles bloom back on when pressed again', () => {
      const keysDown: IsKeyDownFn = (c) => c === 'Digit1';
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: keysDown,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.bloom[eid]).toBe(0);
      // Release key (debounce clears)
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: noKeys,
        debounce,
        postprocessing: pp,
        eid,
      });
      // Press again
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: keysDown,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.bloom[eid]).toBe(1);
    });

    it('does not toggle while key is held (debounce)', () => {
      const keysDown: IsKeyDownFn = (c) => c === 'Digit1';
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: keysDown,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.bloom[eid]).toBe(0);
      // Still held — no second toggle
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: keysDown,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.bloom[eid]).toBe(0);
    });

    it('does nothing when no keys are pressed', () => {
      const res = applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: noKeys,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.bloom[eid]).toBe(1);
      expect(res.toggled).toEqual([]);
    });
  });

  describe('applyPostFxToggle — cycling effects (aa, toneMapping)', () => {
    let pp: ReturnType<typeof makePostprocessing>;
    let debounce: Set<string>;
    const eid = 3;

    beforeEach(() => {
      pp = makePostprocessing();
      debounce = new Set();
    });

    it('cycles aa through 0 → 1 → 2 → 0', () => {
      const press: IsKeyDownFn = (c) => c === 'Digit4';
      expect(pp.aa[eid]).toBe(0);
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: press,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.aa[eid]).toBe(1);
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: () => false,
        debounce,
        postprocessing: pp,
        eid,
      });
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: press,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.aa[eid]).toBe(2);
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: () => false,
        debounce,
        postprocessing: pp,
        eid,
      });
      applyPostFxToggle({
        bindings: DEFAULT_POSTFX_BINDINGS,
        isKeyDown: press,
        debounce,
        postprocessing: pp,
        eid,
      });
      expect(pp.aa[eid]).toBe(0);
    });

    it('cycles toneMapping through 0 → 1 → 2 → 3 → 4 → 0', () => {
      const press: IsKeyDownFn = (c) => c === 'Digit6';
      const release: IsKeyDownFn = () => false;
      const expected = [1, 2, 3, 4, 0];
      for (const val of expected) {
        applyPostFxToggle({
          bindings: DEFAULT_POSTFX_BINDINGS,
          isKeyDown: press,
          debounce,
          postprocessing: pp,
          eid,
        });
        expect(pp.toneMapping[eid]).toBe(val);
        applyPostFxToggle({
          bindings: DEFAULT_POSTFX_BINDINGS,
          isKeyDown: release,
          debounce,
          postprocessing: pp,
          eid,
        });
      }
    });
  });

  describe('applyPostFxToggle — custom bindings', () => {
    it('uses custom bindings map', () => {
      const custom: PostFxKeyBindings = new Map([['KeyA', 'bloom']]);
      const pp = makePostprocessing();
      pp.bloom[0] = 1;
      const res = applyPostFxToggle({
        bindings: custom,
        isKeyDown: (c) => c === 'KeyA',
        debounce: new Set(),
        postprocessing: pp,
        eid: 0,
      });
      expect(pp.bloom[0]).toBe(0);
      expect(res.toggled).toEqual(['bloom']);
    });
  });
});
