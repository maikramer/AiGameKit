import { describe, expect, it } from 'bun:test';
import { DataRegistry } from 'aigamekit-vibegame';
import { LOOT_TABLE_KIND } from '../../../src/plugins/rpg-core/loot';

describe('rpg-core matrix: DataRegistry bulk', () => {
  it('register many items', () => {
    const reg = new DataRegistry();
    for (let i = 0; i < 20; i++) {
      reg.register('item', `id-${i}`, { n: i });
    }
    expect(reg.all('item').length).toBe(20);
  });

  for (let i = 0; i < 15; i++) {
    it(`has item id-${i} after register`, () => {
      const reg = new DataRegistry();
      reg.register('item', `id-${i}`, { v: i });
      expect(reg.has('item', `id-${i}`)).toBe(true);
    });
  }

  it('kinds are isolated', () => {
    const reg = new DataRegistry();
    reg.register('a', 'x', { k: 1 });
    reg.register('b', 'x', { k: 2 });
    expect(reg.get<{ k: number }>('a', 'x')!.k).toBe(1);
    expect(reg.get<{ k: number }>('b', 'x')!.k).toBe(2);
  });
});

describe('rpg-core matrix: loot table kind constant', () => {
  it('LOOT_TABLE_KIND is loot-table', () => {
    expect(LOOT_TABLE_KIND).toBe('loot-table');
  });
});

describe('rpg-core matrix: registry overwrite semantics', () => {
  for (const tier of [1, 2, 3, 4, 5]) {
    it(`overwrite tier ${tier}`, () => {
      const reg = new DataRegistry();
      reg.register('skill', 'fire', { tier: tier - 1 });
      reg.register('skill', 'fire', { tier });
      expect(reg.get<{ tier: number }>('skill', 'fire')!.tier).toBe(tier);
    });
  }
});

describe('rpg-core matrix: empty registry queries', () => {
  const reg = new DataRegistry();
  for (const kind of ['item', 'skill', 'quest', 'loot-table']) {
    it(`all(${kind}) empty`, () => {
      expect(reg.all(kind)).toEqual([]);
    });
    it(`has(${kind}, missing) false`, () => {
      expect(reg.has(kind, 'missing')).toBe(false);
    });
  }
});
