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
} from './plugins/combat/components';
export type { FactionHostilityMatrix } from './plugins/combat/components';
export {
  PROJECTILE_TEMPLATE_KIND,
  spawnProjectile,
  spawnProjectileFromTemplate,
} from './plugins/combat/projectile';
export type {
  ProjectileSpawnConfig,
  ProjectileTarget,
  ProjectileTemplate,
} from './plugins/combat/projectile';
export { CombatPlugin } from './plugins/combat/plugin';
export {
  CombatDeathCleanupSystem,
  DamageResolutionSystem,
  ProjectileCleanupSystem,
} from './plugins/combat/systems';
