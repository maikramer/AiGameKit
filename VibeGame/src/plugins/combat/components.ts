import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Health = {
  current: new Float32Array(MAX_ENTITIES),
  max: new Float32Array(MAX_ENTITIES),
} as const;

export const ProjectileData = {
  damage: new Float32Array(MAX_ENTITIES),
  ownerEid: new Int32Array(MAX_ENTITIES),
  lifetime: new Float32Array(MAX_ENTITIES),
  age: new Float32Array(MAX_ENTITIES),
} as const;

export function damageHealth(eid: number, amount: number): void {
  const newHp = Health.current[eid] - amount;
  Health.current[eid] = Math.max(0, newHp);
}

export function healHealth(eid: number, amount: number): void {
  const newHp = Health.current[eid] + amount;
  Health.current[eid] = Math.min(Health.max[eid], newHp);
}

export function isAlive(eid: number): boolean {
  return Health.current[eid] > 0;
}

export function isDead(eid: number): boolean {
  return Health.current[eid] <= 0;
}

export function setMaxHealth(eid: number, max: number): void {
  Health.max[eid] = max;
  Health.current[eid] = max;
}

export function setProjectileOwner(eid: number, ownerEid: number): void {
  ProjectileData.ownerEid[eid] = ownerEid;
}

export function incrementProjectileAge(eid: number, dt: number): void {
  ProjectileData.age[eid] += dt;
}

export function isProjectileExpired(eid: number): boolean {
  return ProjectileData.age[eid] >= ProjectileData.lifetime[eid];
}
