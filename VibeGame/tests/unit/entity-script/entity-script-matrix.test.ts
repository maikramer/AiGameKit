import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import {
  addActiveCollisionPair,
  coerceMonoBehaviourModule,
  deleteActiveCollisionPairsForEntity,
  deletePrevEnabled,
  getActiveCollisionPairs,
  getPrevEnabled,
  removeActiveCollisionPair,
  resolveEntityScriptGlobKey,
  setPrevEnabled,
} from '../../../src/plugins/entity-script/context';

const glob = {
  '/src/scripts/Hero.ts': () => Promise.resolve({ update: () => {} }),
  '/src/scripts/enemies/Slime.ts': () => Promise.resolve({ start: () => {} }),
  '/src/world/Tree.ts': () => Promise.resolve({ update: () => {} }),
};

describe('entity-script matrix: resolveEntityScriptGlobKey', () => {
  it('matches basename Hero.ts', () => {
    expect(resolveEntityScriptGlobKey(glob, 'Hero.ts')).toBe(
      '/src/scripts/Hero.ts'
    );
  });
  it('matches nested Slime.ts', () => {
    expect(resolveEntityScriptGlobKey(glob, 'Slime.ts')).toBe(
      '/src/scripts/enemies/Slime.ts'
    );
  });
  it('returns undefined for missing file', () => {
    expect(resolveEntityScriptGlobKey(glob, 'Missing.ts')).toBeUndefined();
  });
  it('empty file returns undefined', () => {
    expect(resolveEntityScriptGlobKey(glob, '')).toBeUndefined();
  });
  it('caches repeated lookups', () => {
    const a = resolveEntityScriptGlobKey(glob, 'Tree.ts');
    const b = resolveEntityScriptGlobKey(glob, 'Tree.ts');
    expect(a).toBe(b);
  });
});

describe('entity-script matrix: coerceMonoBehaviourModule', () => {
  it('null input returns null', () => {
    expect(coerceMonoBehaviourModule(null)).toBeNull();
  });
  it('primitive returns null', () => {
    expect(coerceMonoBehaviourModule(42)).toBeNull();
  });
  it('update-only module accepted', () => {
    const mod = coerceMonoBehaviourModule({ update: () => {} });
    expect(mod?.update).toBeDefined();
  });
  it('start-only module accepted', () => {
    const mod = coerceMonoBehaviourModule({ start: () => {} });
    expect(mod?.start).toBeDefined();
  });
  it('empty object rejected', () => {
    expect(coerceMonoBehaviourModule({})).toBeNull();
  });
  it('preserves collision hooks when present', () => {
    const fn = () => {};
    const mod = coerceMonoBehaviourModule({
      update: fn,
      onTriggerEnter: fn,
      onCollisionExit: fn,
    });
    expect(mod?.onTriggerEnter).toBe(fn);
    expect(mod?.onCollisionExit).toBe(fn);
  });
});

describe('entity-script matrix: prev enabled map', () => {
  let state: State;
  beforeEach(() => {
    state = new State();
  });
  it('set and get prev enabled', () => {
    const eid = state.createEntity();
    setPrevEnabled(state, eid, 1);
    expect(getPrevEnabled(state, eid)).toBe(1);
  });
  it('delete clears entry', () => {
    const eid = state.createEntity();
    setPrevEnabled(state, eid, 1);
    deletePrevEnabled(state, eid);
    expect(getPrevEnabled(state, eid)).toBeUndefined();
  });
});

describe('entity-script matrix: collision pairs', () => {
  let state: State;
  beforeEach(() => {
    state = new State();
  });

  it('add and remove pair', () => {
    const a = state.createEntity();
    const b = state.createEntity();
    addActiveCollisionPair(state, a, b, false);
    const pairs = getActiveCollisionPairs(state);
    expect(pairs.get(a)?.get(b)).toBe(false);
    expect(removeActiveCollisionPair(state, a, b)).toBe(true);
    expect(pairs.get(a)?.has(b)).toBeFalsy();
  });

  it('deleteActiveCollisionPairsForEntity clears entity bucket', () => {
    const a = state.createEntity();
    const b = state.createEntity();
    addActiveCollisionPair(state, a, b, true);
    deleteActiveCollisionPairsForEntity(state, a);
    expect(getActiveCollisionPairs(state).has(a)).toBe(false);
  });

  for (const trigger of [true, false]) {
    it(`stores trigger flag=${trigger}`, () => {
      const s = new State();
      const x = s.createEntity();
      const y = s.createEntity();
      addActiveCollisionPair(s, x, y, trigger);
      expect(getActiveCollisionPairs(s).get(x)?.get(y)).toBe(trigger);
    });
  }
});

describe('entity-script matrix: coerce lifecycle exports', () => {
  const hooks = [
    'awake',
    'onEnable',
    'onDisable',
    'fixedUpdate',
    'lateUpdate',
    'onDestroy',
  ] as const;
  for (const hook of hooks) {
    it(`coerces optional ${hook}`, () => {
      const mod = coerceMonoBehaviourModule({
        update: () => {},
        [hook]: () => {},
      });
      expect(mod?.[hook]).toBeDefined();
    });
  }
});
