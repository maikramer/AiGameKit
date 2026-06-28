// Shared RPG data types. Pure type declarations — zero runtime, zero logic.
// All shapes are JSON-serializable so definitions round-trip through
// YAML/JSON registries and save snapshots.

// `(string & {})` is a TS idiom: it keeps literal autocomplete for the known
// kinds while still accepting arbitrary game-specific strings.
export type ResourceKind = 'gold' | 'wood' | 'stone' | (string & {});

export type FactionTag =
  'player' | 'enemy' | 'neutral' | 'merchant' | (string & {});

export interface ItemDef {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  maxStack: number;
  tags: readonly string[];
}

export interface ItemStack {
  itemId: string;
  qty: number;
}

export interface StatModifier {
  stat: string;
  magnitude: number;
  duration?: number;
  stackMode: 'replace' | 'stack' | 'max';
}

export interface SkillEffect {
  kind: 'stat-modifier' | 'event-trigger' | 'unlock';
  payload: unknown;
}

export interface SkillDef {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  maxRank: number;
  cost: number | number[];
  effect: SkillEffect;
}

export interface LootEntry {
  itemId?: string;
  resourceKind?: string;
  qtyMin: number;
  qtyMax: number;
  weight: number;
}

export interface LootTable {
  id: string;
  rolls: number;
  entries: LootEntry[];
}

export interface StatusEffectDef {
  id: string;
  name: string;
  duration: number;
  modifiers: StatModifier[];
  tickInterval?: number;
  tickEffect?: SkillEffect;
}
