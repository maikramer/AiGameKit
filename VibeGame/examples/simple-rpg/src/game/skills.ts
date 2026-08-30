// Skill adapter → engine Progression. Skill points live on the player's
// ProgressionComponent (granted on level-up by the engine, and by the rune
// pillar). Spending happens in the engine pause menu's SkillsTab; this module
// only (a) grants points and (b) registers the skill definitions the SkillsTab
// reads from the data registry.
import { ProgressionComponent, getDataRegistry } from 'vibegame';
import type { SkillDef, State } from 'vibegame';
import { engineState, playerEid } from './engine-bridge';

// Resolved player progress shared across gameplay modules.
//   attackBonus — flat damage added to the player's bombs (Strength ranks +
//                 merchant sword upgrades); recomputed each frame by
//                 PlayerStatsSystem in main.ts, read by bombs.ts.
//   ringOwned   — set by the merchant; read by PlayerStatsSystem to apply the
//                 speed multiplier. Persisted via the save-load serializer
//                 registered in main.ts so it survives save/load (otherwise
//                 re-buying the ring would compound the speed bonus).
//   swordLevel  — set by the merchant; folded into attackBonus.
export const playerStats = {
  attackBonus: 0,
  ringOwned: false,
  swordLevel: 0,
  /** Temp attack bonus from the War Cry skill [U]; timer ticked by skill-bar. */
  buffAttackBonus: 0,
  buffAttackTimer: 0,
  /** Temp attack bonus from a Perfect Dodge (fury); ticked by combat-mechanics. */
  furyBonus: 0,
  furyTimer: 0,
  /** True while the player holds guard [L] — PlayerStatsSystem slows movement. */
  blocking: false,
};

/** Total flat attack power the player adds to every hit right now. */
export function playerAttackPower(): number {
  return playerStats.attackBonus + playerStats.furyBonus;
}

export const RING_SPEED_MULT = 1.15;

/** Grant skill points to the player (e.g. the rune pillar). */
export function addSkillPoints(n: number): void {
  if (!Number.isFinite(n) || n === 0) return;
  const h = playerEid();
  if (!h) return;
  // `undefined + n` is NaN and would lock the SkillsTab forever — seed first.
  ProgressionComponent.unspentPoints[h] =
    (ProgressionComponent.unspentPoints[h] ?? 0) + n;
}

export function getSkillPoints(): number {
  const h = playerEid();
  return h ? (ProgressionComponent.unspentPoints[h] ?? 0) : 0;
}

const GAME_SKILLS: readonly SkillDef[] = [
  {
    id: 'vitality',
    name: 'Vitality',
    description: '+12 max HP per rank',
    icon: '♥',
    maxRank: 5,
    cost: 1,
    tier: 0,
    column: 0,
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'maxHp', magnitude: 12, stackMode: 'stack' },
    },
  },
  {
    id: 'strength',
    name: 'Strength',
    description: '+5 attack per rank',
    icon: '⚔',
    maxRank: 5,
    cost: 1,
    tier: 0,
    column: 1,
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'attack', magnitude: 5, stackMode: 'stack' },
    },
  },
  {
    id: 'agility',
    name: 'Agility',
    description: '+0.4 move speed per rank',
    icon: '✧',
    maxRank: 5,
    cost: 1,
    tier: 0,
    column: 2,
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'moveSpeed', magnitude: 0.4, stackMode: 'stack' },
    },
  },
  {
    id: 'fortitude',
    name: 'Fortitude',
    description: '+18 max HP per rank (requires Vitality)',
    icon: '🛡',
    maxRank: 3,
    cost: 1,
    tier: 1,
    column: 0,
    requires: ['vitality'],
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'maxHp', magnitude: 18, stackMode: 'stack' },
    },
  },
  {
    id: 'power',
    name: 'Power',
    description: '+8 attack per rank (requires Strength)',
    icon: '⚡',
    maxRank: 3,
    cost: 1,
    tier: 1,
    column: 1,
    requires: ['strength'],
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'attack', magnitude: 8, stackMode: 'stack' },
    },
  },
  {
    id: 'swift',
    name: 'Swift',
    description: '+0.55 move speed per rank (requires Agility)',
    icon: '➳',
    maxRank: 3,
    cost: 1,
    tier: 1,
    column: 2,
    requires: ['agility'],
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'moveSpeed', magnitude: 0.55, stackMode: 'stack' },
    },
  },
  {
    id: 'titan',
    name: 'Titan',
    description: '+10 attack per rank (requires Fortitude + Power)',
    icon: '♛',
    maxRank: 2,
    cost: 2,
    tier: 2,
    column: 0,
    requires: ['fortitude', 'power'],
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'attack', magnitude: 10, stackMode: 'stack' },
    },
  },
  {
    id: 'windwalker',
    name: 'Windwalker',
    description: '+0.7 move speed (requires Swift)',
    icon: '☁',
    maxRank: 2,
    cost: 2,
    tier: 2,
    column: 2,
    requires: ['swift'],
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'moveSpeed', magnitude: 0.7, stackMode: 'stack' },
    },
  },
];

/**
 * Register the skill tree with the engine data registry so the SkillsTab can
 * list them. Stat-modifiers are applied by PlayerStatsSystem in main.ts via
 * getStatModifiers (maxHp / attack / moveSpeed).
 */
export function registerGameSkills(state: State = engineState()!): void {
  if (!state) return;
  const reg = getDataRegistry(state);
  for (const skill of GAME_SKILLS) {
    reg.register('skill', skill.id, skill);
  }
}
