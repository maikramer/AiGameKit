export { RpgPlugins } from './plugins/rpg-bundle';
export { HudRpgPlugin } from './plugins/hud/rpg-plugin';

export {
  COMBAT_DAMAGED,
  COMBAT_DEATH,
  COMBAT_HEALED,
  COMBAT_KILLED,
  DataRegistry,
  ECONOMY_GAINED,
  ECONOMY_SPENT,
  EventBus,
  EventBusCleanupSystem,
  emitEvent,
  getDataRegistry,
  getEventBus,
  INVENTORY_ADDED,
  INVENTORY_REMOVED,
  LOOT_DROPPED,
  LOOT_ROLLED,
  LOOT_TABLE_KIND,
  onEvent,
  PROGRESSION_LEVEL_UP,
  PROGRESSION_SKILL_PURCHASED,
  PROGRESSION_XP_GAINED,
  RpgCoreEventsPlugin,
  RpgCorePlugin,
  wireMusicMixerEvents,
  STATUS_APPLIED,
  STATUS_CANCELLED,
  STATUS_EXPIRED,
  applyLootResult,
  rollLoot,
} from './plugins/rpg-core';
export type { EventHandler, SubscriptionOptions } from './plugins/rpg-core';
export type { LootResult, RngFn } from './plugins/rpg-core';

export {
  addXp,
  getProgressionConfig,
  getSkillRank,
  getStatModifiers,
  getXpToNextLevel,
  levelUp,
  ProgressionComponent,
  ProgressionEventBridgeSystem,
  ProgressionPlugin,
  setProgressionConfig,
  spendSkillPoint,
} from './plugins/rpg-progression';

export {
  applyStatus,
  cancelAllStatuses,
  cancelStatus,
  getActiveStatuses,
  getStatusModifiers,
  STATUS_KIND,
  StatusEffectComponent,
  StatusEffectEventBridgeSystem,
  StatusEffectsPlugin,
  StatusEffectTickSystem,
} from './plugins/rpg-status';
export type {
  ActiveStatusEffect,
  StackMode,
  StatusApplyOptions,
} from './plugins/rpg-status';

export {
  harvest,
  isDepleted,
  isResourceNode,
  getResourceNodeKind,
  NODE_HARVESTED,
  NODE_RESPAWNED,
  ResourceNode,
  ResourceNodePlugin,
  ResourceNodeRespawnSystem,
  resolveResourceNodeKind,
} from './plugins/rpg-resource-node';
export type {
  NodeHarvestedPayload,
  NodeRespawnedPayload,
} from './plugins/rpg-resource-node';

export {
  RpgVaultPlugin,
  VaultComponent,
  VaultEventBridgeSystem,
  addResource,
  getCapacity,
  getResource,
  pruneVaults,
  registerResourceKind,
  setCapacity,
  spendResource,
} from './plugins/rpg-vault';

export {
  buyItem,
  EconomyPlugin,
  EconomyEventBridgeSystem,
  getPrice,
  GOLD_KIND,
  sellItem,
} from './plugins/rpg-economy';
export type { PriceEntry, PriceKind } from './plugins/rpg-economy';

export {
  addItem,
  getInventory,
  getItemQty,
  InventoryComponent,
  InventoryEventBridgeSystem,
  InventoryPlugin,
  removeItem,
} from './plugins/rpg-inventory';

export {
  getActiveModal,
  getPauseState,
  isPaused,
  PAUSE_CHANGED,
  PauseCoordinatorPlugin,
  PauseSystem,
  PAUSE_POPPED,
  popModal,
  PAUSE_PUSHED,
  pushModal,
  setTimeScale,
  suppressInput,
} from './plugins/rpg-pause';
export type { PauseState } from './plugins/rpg-pause';

export {
  AI_MODE_ATTACK,
  AI_MODE_CHASE,
  AI_MODE_DEAD,
  AI_MODE_DETECT,
  AI_MODE_IDLE,
  AI_MODE_LUNGE,
  AiStateComponent,
  MELEE_AI_KIND,
  RpgAiPlugin,
  RpgAiSystem,
} from './plugins/rpg-ai';

export {
  acceptQuest,
  DialogueData,
  QuestGiver,
  QUEST_COMPLETED,
  QuestProgressSystem,
  QuestState,
  QuestTriggerSystem,
  QuestsPlugin,
  registerQuest,
  showDialogue,
} from './plugins/quests';

export {
  bindCombatState,
  CombatDeathCleanupSystem,
  CombatPlugin,
  FACTION_TAG_NAMES,
  FactionComponent,
  getFaction,
  Health,
  isHostile,
  PROJECTILE_TEMPLATE_KIND,
  ProjectileConfig,
  ProjectileData,
  damageHealth,
  healHealth,
  isDead,
  setFaction,
  spawnProjectile,
  spawnProjectileFromTemplate,
} from './plugins/combat';
export type {
  FactionHostilityMatrix,
  ProjectileSpawnConfig,
  ProjectileTarget,
  ProjectileTemplate,
} from './plugins/combat';
