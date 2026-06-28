import { afterEach, describe, expect, it } from 'bun:test';
// Not re-exported from `vibegame`; deep relative import (see gltf-lod-level test).
import {
  type EffectDefinition,
  getEffectDefinitions,
  registerEffect,
  unregisterEffect,
} from '../../../src/plugins/postprocessing/effect-registry';

function makeDefinition(key: string): EffectDefinition {
  return { key, create: () => null };
}

describe('postprocessing effect registry', () => {
  const owned: string[] = [];

  afterEach(() => {
    for (const key of owned.splice(0)) unregisterEffect(key);
  });

  it('registerEffect adds a new definition and getEffectDefinitions exposes it', () => {
    registerEffect(makeDefinition('test-add'));
    owned.push('test-add');

    const defs = getEffectDefinitions();
    const keys = defs.map((d) => d.key);
    expect(keys).toContain('test-add');
    const added = defs.find((d) => d.key === 'test-add');
    expect(added).toBeDefined();
    expect(typeof added?.create).toBe('function');
  });

  it('registerEffect replaces an existing definition with the same key', () => {
    registerEffect({
      key: 'test-replace',
      create: () => null,
      position: 'first',
    });
    owned.push('test-replace');
    const before = getEffectDefinitions().filter(
      (d) => d.key === 'test-replace'
    );
    expect(before).toHaveLength(1);
    expect(before[0].position).toBe('first');

    const updated = makeDefinition('test-replace');
    registerEffect(updated);

    const after = getEffectDefinitions().filter(
      (d) => d.key === 'test-replace'
    );
    expect(after).toHaveLength(1);
    expect(after[0].position).toBeUndefined();
  });

  it('getEffectDefinitions returns a live view that reflects later registrations', () => {
    registerEffect(makeDefinition('test-live'));
    owned.push('test-live');
    const before = getEffectDefinitions();
    const lengthBefore = before.length;
    expect(before.map((d) => d.key)).toContain('test-live');

    registerEffect(makeDefinition('test-live-2'));
    owned.push('test-live-2');

    const after = getEffectDefinitions();
    expect(after).toHaveLength(lengthBefore + 1);
    expect(after.map((d) => d.key)).toContain('test-live-2');
  });

  it('unregisterEffect returns true for a registered key and removes it', () => {
    registerEffect(makeDefinition('test-remove'));
    expect(getEffectDefinitions().map((d) => d.key)).toContain('test-remove');

    expect(unregisterEffect('test-remove')).toBe(true);
    expect(getEffectDefinitions().map((d) => d.key)).not.toContain(
      'test-remove'
    );
  });

  it('unregisterEffect returns false for an unknown key', () => {
    expect(unregisterEffect('does-not-exist')).toBe(false);
  });
});
