export {
  Health,
  ProjectileData,
  damageHealth,
  healHealth,
  isAlive,
  isDead,
  setMaxHealth,
  setProjectileOwner,
  incrementProjectileAge,
  isProjectileExpired,
} from './components';
export { CombatPlugin } from './plugin';
export { DamageResolutionSystem, ProjectileCleanupSystem } from './systems';
