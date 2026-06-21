// Skill adapter → engine Progression. Skill points live on the hero's
// ProgressionComponent (granted on level-up by the engine, and by the rune
// pillar). Spending happens in the engine pause menu's SkillsTab; this module
// only (a) grants points and (b) registers the skill definitions the SkillsTab
// reads from the data registry.
import { ProgressionComponent, getDataRegistry } from 'vibegame';
import type { State } from 'vibegame';
import { engineState, heroEid } from './engine-bridge';

// Resolved hero progress shared across gameplay modules.
//   attackBonus — flat damage added to the hero's bombs (Strength ranks +
//                 merchant sword upgrades); recomputed each frame by
//                 HeroStatsSystem in main.ts, read by bombs.ts.
//   ringOwned   — set by the merchant; read by HeroStatsSystem to apply the
//                 speed multiplier. Persisted via the save-load serializer
//                 registered in main.ts so it survives save/load (otherwise
//                 re-buying the ring would compound the speed bonus).
//   swordLevel  — set by the merchant; folded into attackBonus.
export const heroStats = {
  attackBonus: 0,
  ringOwned: false,
  swordLevel: 0,
};

export const RING_SPEED_MULT = 1.15;

/** Grant skill points to the hero (e.g. the rune pillar). */
export function addSkillPoints(n: number): void {
  const h = heroEid();
  if (h) ProgressionComponent.unspentPoints[h] += n;
}

export function getSkillPoints(): number {
  const h = heroEid();
  return h ? (ProgressionComponent.unspentPoints[h] ?? 0) : 0;
}

/**
 * Register the three skills with the engine data registry so the SkillsTab can
 * list them. All three are stat-modifiers applied by HeroStatsSystem in
 * main.ts: Vitality → max HP, Strength → bomb attack damage (heroStats.
 * attackBonus), Agility → PlayerController.speed.
 */
export function registerGameSkills(state: State = engineState()!): void {
  if (!state) return;
  const reg = getDataRegistry(state);
  reg.register('skill', 'vitality', {
    id: 'vitality',
    name: 'Vitality',
    description: '+12 max HP per rank',
    maxRank: 5,
    cost: 1,
    effect: { kind: 'stat-modifier', payload: { stat: 'maxHp', magnitude: 12, stackMode: 'stack' } },
  });
  reg.register('skill', 'strength', {
    id: 'strength',
    name: 'Strength',
    description: '+attack power',
    maxRank: 5,
    cost: 1,
    effect: { kind: 'stat-modifier', payload: { stat: 'attack', magnitude: 5, stackMode: 'stack' } },
  });
  reg.register('skill', 'agility', {
    id: 'agility',
    name: 'Agility',
    description: '+move speed',
    maxRank: 5,
    cost: 1,
    effect: { kind: 'stat-modifier', payload: { stat: 'moveSpeed', magnitude: 0.4, stackMode: 'stack' } },
  });
}
