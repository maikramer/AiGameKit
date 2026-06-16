import { describe, expect, it } from 'bun:test';
import type {
  FactionTag,
  ItemDef,
  ItemStack,
  LootEntry,
  LootTable,
  ResourceKind,
  SkillDef,
  SkillEffect,
  StatModifier,
  StatusEffectDef,
} from '../../../src/plugins/rpg-core/types';

function roundTrip<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

describe('rpg-core types JSON round-trip', () => {
  it('preserves ResourceKind and FactionTag string values', () => {
    const gold: ResourceKind = 'gold';
    const custom: ResourceKind = 'essence';
    const enemy: FactionTag = 'enemy';
    const boss: FactionTag = 'boss-faction';

    expect(roundTrip(gold)).toBe('gold');
    expect(roundTrip(custom)).toBe('essence');
    expect(roundTrip(enemy)).toBe('enemy');
    expect(roundTrip(boss)).toBe('boss-faction');
  });

  it('preserves ItemDef with optional fields', () => {
    const item: ItemDef = {
      id: 'sword',
      name: 'Iron Sword',
      description: 'A basic blade.',
      icon: '/icons/sword.png',
      maxStack: 1,
      tags: ['weapon', 'melee'],
    };

    expect(roundTrip(item)).toEqual(item);
  });

  it('preserves ItemDef without optional fields', () => {
    const item: ItemDef = {
      id: 'potion',
      name: 'Health Potion',
      maxStack: 99,
      tags: ['consumable'],
    };

    expect(roundTrip(item)).toEqual(item);
  });

  it('preserves ItemStack', () => {
    const stack: ItemStack = { itemId: 'arrow', qty: 50 };

    expect(roundTrip(stack)).toEqual(stack);
  });

  it('preserves StatModifier variants', () => {
    const instant: StatModifier = {
      stat: 'hp',
      magnitude: -10,
      stackMode: 'replace',
    };
    const timed: StatModifier = {
      stat: 'speed',
      magnitude: 1.3,
      duration: 10,
      stackMode: 'max',
    };

    expect(roundTrip(instant)).toEqual(instant);
    expect(roundTrip(timed)).toEqual(timed);
  });

  it('preserves SkillEffect payload (unknown data)', () => {
    const stat: SkillEffect = {
      kind: 'stat-modifier',
      payload: { stat: 'strength', amount: 5 },
    };
    const event: SkillEffect = {
      kind: 'event-trigger',
      payload: 'on-level-up',
    };
    const unlock: SkillEffect = {
      kind: 'unlock',
      payload: { feature: 'double-jump' },
    };

    expect(roundTrip(stat)).toEqual(stat);
    expect(roundTrip(event)).toEqual(event);
    expect(roundTrip(unlock)).toEqual(unlock);
  });

  it('preserves SkillDef with scalar and array cost', () => {
    const scalarCost: SkillDef = {
      id: 'vitality',
      name: 'Vitality',
      maxRank: 10,
      cost: 1,
      effect: {
        kind: 'stat-modifier',
        payload: { stat: 'maxHp', perRank: 10 },
      },
    };
    const arrayCost: SkillDef = {
      id: 'strength',
      name: 'Strength',
      description: 'Increases melee damage.',
      icon: '/icons/str.png',
      maxRank: 5,
      cost: [1, 2, 3, 5, 8],
      effect: {
        kind: 'stat-modifier',
        payload: { stat: 'attack', perRank: 2 },
      },
    };

    expect(roundTrip(scalarCost)).toEqual(scalarCost);
    expect(roundTrip(arrayCost)).toEqual(arrayCost);
  });

  it('preserves LootEntry variants', () => {
    const itemDrop: LootEntry = {
      itemId: 'gem',
      qtyMin: 1,
      qtyMax: 3,
      weight: 10,
    };
    const resourceDrop: LootEntry = {
      resourceKind: 'gold',
      qtyMin: 5,
      qtyMax: 20,
      weight: 50,
    };

    expect(roundTrip(itemDrop)).toEqual(itemDrop);
    expect(roundTrip(resourceDrop)).toEqual(resourceDrop);
  });

  it('preserves LootTable with entries', () => {
    const table: LootTable = {
      id: 'goblin-loot',
      rolls: 2,
      entries: [
        { itemId: 'rusty-knife', qtyMin: 1, qtyMax: 1, weight: 5 },
        { resourceKind: 'gold', qtyMin: 0, qtyMax: 15, weight: 80 },
      ],
    };

    expect(roundTrip(table)).toEqual(table);
  });

  it('preserves StatusEffectDef with tick config', () => {
    const poison: StatusEffectDef = {
      id: 'poison',
      name: 'Poison',
      duration: 8,
      modifiers: [
        { stat: 'hp', magnitude: -2, duration: 8, stackMode: 'stack' },
      ],
      tickInterval: 1,
      tickEffect: {
        kind: 'stat-modifier',
        payload: { stat: 'hp', amount: -2 },
      },
    };

    expect(roundTrip(poison)).toEqual(poison);
  });

  it('preserves StatusEffectDef without tick config', () => {
    const buff: StatusEffectDef = {
      id: 'haste',
      name: 'Haste',
      duration: 5,
      modifiers: [{ stat: 'speed', magnitude: 1.5, stackMode: 'replace' }],
    };

    expect(roundTrip(buff)).toEqual(buff);
  });

  it('preserves a composite registry snapshot', () => {
    const snapshot = {
      items: [
        { id: 'sword', name: 'Sword', maxStack: 1, tags: ['weapon'] },
        { id: 'potion', name: 'Potion', maxStack: 99, tags: ['consumable'] },
      ] satisfies ItemDef[],
      skills: [
        {
          id: 'vitality',
          name: 'Vitality',
          maxRank: 5,
          cost: 1,
          effect: { kind: 'stat-modifier', payload: { stat: 'maxHp' } },
        },
      ] satisfies SkillDef[],
      loot: [
        {
          id: 'chest-common',
          rolls: 1,
          entries: [
            { resourceKind: 'gold', qtyMin: 1, qtyMax: 10, weight: 100 },
          ],
        },
      ] satisfies LootTable[],
    };

    expect(roundTrip(snapshot)).toEqual(snapshot);
  });
});
