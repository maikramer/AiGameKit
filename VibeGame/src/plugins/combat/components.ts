import { defineComponent, Types } from 'bitecs';

export const Health = defineComponent({
  current: Types.f32,
  max: Types.f32,
});

export const ProjectileData = defineComponent({
  damage: Types.f32,
  ownerEid: Types.i32,
  lifetime: Types.f32,
  age: Types.f32,
});

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
