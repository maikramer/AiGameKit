export {
  FACTION_TAG_NAMES,
  FactionComponent,
  getDeathFlags,
  damageHealth,
  grantInvulnerability,
  healHealth,
  isAlive,
  isDead,
  setMaxHealth,
  setProjectileOwner,
  incrementProjectileAge,
  isProjectileExpired,
  Health,
  ProjectileConfig,
  ProjectileData,
  bindCombatState,
  getFaction,
  setFaction,
  isHostile,
  registerDamageModifier,
  clearDamageModifiers,
} from './components';
export type { FactionHostilityMatrix, DamageModifier } from './components';
export {
  clearCombatTarget,
  getCombatTarget,
  getCombatTargetLabel,
  setCombatTarget,
  tickCombatTarget,
} from './combat-target';
export type { SetCombatTargetOptions } from './combat-target';
export {
  PROJECTILE_TEMPLATE_KIND,
  spawnProjectile,
  spawnProjectileFromTemplate,
} from './projectile';
export type {
  ProjectileSpawnConfig,
  ProjectileTarget,
  ProjectileTemplate,
} from './projectile';
export { CombatPlugin } from './plugin';
export {
  CombatDeathCleanupSystem,
  CombatInvulnSystem,
  DamageResolutionSystem,
  ProjectileCleanupSystem,
} from './systems';
