import { afterEach, beforeEach, describe, expect, it, mock } from 'bun:test';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { DataRegistry, RpgCorePlugin, State, getDataRegistry } from 'vibegame';

describe('DataRegistry', () => {
  let reg: DataRegistry;

  beforeEach(() => {
    reg = new DataRegistry();
  });

  describe('register / get / all / has / clear', () => {
    it('register then get returns the definition', () => {
      reg.register('item', 'sword', { id: 'sword', name: 'Sword' });

      expect(reg.get<{ id: string; name: string }>('item', 'sword')).toEqual({
        id: 'sword',
        name: 'Sword',
      });
    });

    it('get on missing kind/id returns undefined', () => {
      expect(reg.get('item', 'nope')).toBeUndefined();
      expect(reg.get('unknown', 'x')).toBeUndefined();
    });

    it('all returns every definition of a kind', () => {
      reg.register('item', 'sword', { name: 'Sword' });
      reg.register('item', 'shield', { name: 'Shield' });

      const all = reg.all('item');
      expect(all.length).toBe(2);
      expect(all.map((d) => (d as { name: string }).name).sort()).toEqual([
        'Shield',
        'Sword',
      ]);
    });

    it('all on an empty kind returns an empty readonly array', () => {
      expect(reg.all('nothing')).toEqual([]);
    });

    it('has reports presence', () => {
      reg.register('item', 'sword', { name: 'Sword' });

      expect(reg.has('item', 'sword')).toBe(true);
      expect(reg.has('item', 'missing')).toBe(false);
      expect(reg.has('other', 'sword')).toBe(false);
    });

    it('register overwrites a prior definition with the same id', () => {
      reg.register('item', 'sword', { tier: 1 });
      reg.register('item', 'sword', { tier: 5 });

      expect(reg.get<{ tier: number }>('item', 'sword')!.tier).toBe(5);
      expect(reg.all('item').length).toBe(1);
    });

    it('clear empties every kind', () => {
      reg.register('item', 'sword', { name: 'Sword' });
      reg.register('skill', 'fire', { name: 'Fire' });

      reg.clear();

      expect(reg.all('item')).toEqual([]);
      expect(reg.all('skill')).toEqual([]);
      expect(reg.has('item', 'sword')).toBe(false);
    });

    it('isolates kinds from each other', () => {
      reg.register('item', 'x', { where: 'item' });
      reg.register('skill', 'x', { where: 'skill' });

      expect(reg.get<{ where: string }>('item', 'x')!.where).toBe('item');
      expect(reg.get<{ where: string }>('skill', 'x')!.where).toBe('skill');
    });
  });

  describe('loadYaml', () => {
    it('parses multiple kinds and injects id when missing', () => {
      const yaml = [
        'item:',
        '  sword:',
        '    name: Sword',
        '    maxStack: 1',
        '  potion:',
        '    name: Potion',
        '    maxStack: 99',
        'skill:',
        '  vitality:',
        '    maxRank: 5',
      ].join('\n');

      reg.loadYaml(yaml);

      const sword = reg.get<{ id: string; name: string; maxStack: number }>(
        'item',
        'sword'
      );
      expect(sword).toBeDefined();
      expect(sword!.name).toBe('Sword');
      expect(sword!.maxStack).toBe(1);
      expect(sword!.id).toBe('sword');

      const vit = reg.get<{ id: string; maxRank: number }>('skill', 'vitality');
      expect(vit).toBeDefined();
      expect(vit!.maxRank).toBe(5);
      expect(vit!.id).toBe('vitality');

      expect(reg.all('item').length).toBe(2);
    });

    it('preserves an explicitly-declared id field', () => {
      reg.loadYaml('item:\n  x:\n    id: custom-id\n    n: 1');

      expect(reg.get<{ id: string }>('item', 'x')!.id).toBe('custom-id');
    });

    it('an empty YAML document is a no-op', () => {
      reg.loadYaml('');
      reg.loadYaml('---');

      expect(reg.kinds()).toEqual([]);
    });

    it('a non-object kind value is skipped with a warning', () => {
      const warn = mock(() => {});
      const original = console.warn;
      console.warn = warn;

      reg.loadYaml('item: "not-an-object"');

      console.warn = original;
      expect(warn).toHaveBeenCalled();
      expect(reg.all('item')).toEqual([]);
    });

    it('throws an Error mentioning "Failed to parse YAML" on malformed input', () => {
      // Unbalanced flow mapping bracket is a YAML syntax error.
      const bad = 'item: { sword: "unterminated';

      expect(() => reg.loadYaml(bad)).toThrow(/Failed to parse YAML/);
    });

    it('a YAML parse failure leaves the registry empty', () => {
      try {
        reg.loadYaml('item: { x: "bad');
      } catch {
        /* expected — YAML parse failure leaves registry empty */
      }

      expect(reg.all('item')).toEqual([]);
      expect(reg.kinds()).toEqual([]);
    });

    it('a top-level array is rejected', () => {
      expect(() => reg.loadYaml('- a\n- b')).toThrow(/top-level/);
    });
  });

  describe('loadJson', () => {
    it('parses JSON and registers kinds', () => {
      reg.loadJson(
        JSON.stringify({
          item: { sword: { name: 'Sword', maxStack: 1 } },
        })
      );

      const sword = reg.get<{ name: string }>('item', 'sword');
      expect(sword).toBeDefined();
      expect(sword!.name).toBe('Sword');
    });

    it('throws mentioning "Failed to parse JSON" on malformed JSON', () => {
      expect(() => reg.loadJson('{ not json')).toThrow(/Failed to parse JSON/);
    });
  });

  describe('loadDirectory', () => {
    const tmpDir = join(import.meta.dir, '.tmp-registry-dir-' + process.pid);

    beforeEach(() => {
      rmSync(tmpDir, { recursive: true, force: true });
      mkdirSync(tmpDir, { recursive: true });
    });

    afterEach(() => {
      rmSync(tmpDir, { recursive: true, force: true });
    });

    it('loads all .yaml/.yml/.json files in a directory', async () => {
      writeFileSync(
        join(tmpDir, 'items.yaml'),
        'item:\n  sword:\n    name: Sword\n'
      );
      writeFileSync(
        join(tmpDir, 'skills.yml'),
        'skill:\n  fire:\n    maxRank: 3\n'
      );
      writeFileSync(
        join(tmpDir, 'tables.json'),
        JSON.stringify({ lootTable: { goblin: { rolls: 1 } } })
      );

      await reg.loadDirectory(tmpDir);

      expect(reg.get<{ name: string }>('item', 'sword')!.name).toBe('Sword');
      expect(reg.get<{ maxRank: number }>('skill', 'fire')!.maxRank).toBe(3);
      expect(reg.get<{ rolls: number }>('lootTable', 'goblin')!.rolls).toBe(1);
    });

    it('is a no-op on an empty directory', async () => {
      await reg.loadDirectory(tmpDir);
      expect(reg.kinds()).toEqual([]);
    });
  });
});

describe('getDataRegistry (per-State)', () => {
  it('returns the same registry for the same State', () => {
    const state = new State();

    expect(getDataRegistry(state)).toBe(getDataRegistry(state));
  });

  it('returns distinct registries for distinct States', () => {
    const a = new State();
    const b = new State();

    expect(getDataRegistry(a)).not.toBe(getDataRegistry(b));
  });

  it('RpgCorePlugin.initialize eagerly creates the registry', () => {
    const state = new State();
    state.registerPlugin(RpgCorePlugin);

    expect(getDataRegistry(state)).toBeDefined();
    expect(getDataRegistry(state).all('item')).toEqual([]);
  });
});
