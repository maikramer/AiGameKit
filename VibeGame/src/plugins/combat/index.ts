export {
  FACTION_TAG_NAMES,
  FactionComponent,
  getDeathFlags,
  damageHealth,
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
} from './components';
export type { FactionHostilityMatrix } from './components';
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
  DamageResolutionSystem,
  ProjectileCleanupSystem,
} from './systems';
