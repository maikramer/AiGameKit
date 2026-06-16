// Skill-point store. Pure data + a decoupled effect hook so this module never
// touches the ECS directly — main.ts registers what spending a point actually
// does (e.g. Vitality → hero max HP). Localized labels are referenced by dict
// key and resolved by the UI layer.

export type SkillKey = 'vitality' | 'strength' | 'agility';

export interface SkillDef {
  key: SkillKey;
  nameKey: string;
  descKey: string;
  color: string;
}

export const SKILL_POINTS_PER_LEVEL = 3;

export const SKILL_DEFS: SkillDef[] = [
  {
    key: 'vitality',
    nameKey: 'pause.skill.vitality',
    descKey: 'pause.skill.vitality.desc',
    color: '#9be37a',
  },
  {
    key: 'strength',
    nameKey: 'pause.skill.strength',
    descKey: 'pause.skill.strength.desc',
    color: '#ff8a6a',
  },
  {
    key: 'agility',
    nameKey: 'pause.skill.agility',
    descKey: 'pause.skill.agility.desc',
    color: '#7ad0ff',
  },
];

let points = 0;
const levels: Record<SkillKey, number> = {
  vitality: 0,
  strength: 0,
  agility: 0,
};

type SpendHandler = (key: SkillKey, newLevel: number) => void;
let spendHandler: SpendHandler | null = null;

/** main.ts registers the gameplay effect of spending a point in a skill. */
export function setSkillEffectHandler(fn: SpendHandler): void {
  spendHandler = fn;
}

export function getSkillPoints(): number {
  return points;
}

export function getSkillLevel(key: SkillKey): number {
  return levels[key];
}

export function addSkillPoints(n: number): void {
  points += n;
}

/** Spend one point in a skill; returns false if no points are available. */
export function spendSkillPoint(key: SkillKey): boolean {
  if (points <= 0) return false;
  points -= 1;
  levels[key] += 1;
  spendHandler?.(key, levels[key]);
  return true;
}
