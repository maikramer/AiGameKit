import type { BuilderOptions } from './builder';
import { GameBuilder } from './builder';
import type { Component, Plugin, System } from './core';
import { disposeAllRuntimes } from './core/runtime-manager';

export * from './core';
export type { BuilderOptions };
export type { GameRuntime } from './runtime';
export {
  applyDefaultShadowFlags,
  clearGltfMasterCache,
  createGLTFLoader,
  disposeObject3DResources,
  evictGltfMaster,
  isGroupOwnedGpu,
  loadGltfAnimated,
  loadGltfLodToScene,
  loadGltfToScene,
  loadGltfToSceneWithAnimator,
  normalizeGltfMaterials,
  setKTX2TranscoderPath,
} from './extras/gltf-bridge';
export type { GltfLoadResult } from './extras/gltf-bridge';
export { validateGltf } from './extras/gltf-validator';
export type {
  GltfIssueSeverity,
  GltfValidationIssue,
  GltfValidationReport,
  ValidateGltfOptions,
} from './extras/gltf-validator';
export { GltfAnimator } from './extras/gltf-animator';
export type {
  GltfAnimatorOptions,
  LocomotionSet,
} from './extras/gltf-animator';
export {
  applyEquirectSkyEnvironment,
  autoLoadSkyEnvironment,
} from './extras/sky-env';
export type { EquirectSkyOptions } from './extras/sky-env';

export {
  PlayerController,
  PlayerGltfConfig,
  playerGltfRecipe,
  setPlayerAttackClip,
  setPlayerHeldItem,
  setPlayerFaceTarget,
} from './plugins/player';
export { ThirdPersonCamera } from './plugins/player-controller/components';
export { PlayerControllerPlugin } from './plugins/player-controller/plugin';
export { OrbitCamera, OrbitCameraPlugin } from './plugins/orbit-camera';
export { getScene } from './plugins/rendering';
export { MeshRenderer } from './plugins/rendering';
export { Transform, WorldTransform } from './plugins/transforms';
export { AnimatedCharacter, HasAnimator } from './plugins/animation';
export {
  animatorRegistry,
  GltfAnimationState,
  GltfAnimPlugin,
  GltfAnimationUpdateSystem,
  registerAnimator,
} from './plugins/gltf-anim';
export {
  isKeyDown,
  addInputMapping,
  InputState,
  setInputMovementSuppressed,
  isInputMovementSuppressed,
} from './plugins/input';
export {
  AudioSource,
  AudioListener,
  MusicLayerComponent,
  AudioPlugin,
  AudioSystem,
  SoundBankSystem,
  playAudioEmitter,
  registerAudioClip,
  resumeAudioContextIfSuspended,
  resumeAudioContextOnFirstUserGesture,
  NamedSfxResolverSystem,
  playNamedSfx,
  registerNamedSfx,
  defineSoundBank,
  getSoundDef,
  playSound,
  playSoundAt,
  playSoundOn,
  setBusVolume,
  getBusVolume,
  setBusMuted,
  isBusMuted,
  setAudioEnabled,
  addClipSound,
  getClipSounds,
} from './plugins/audio';
export type {
  SoundDef,
  PlayOptions,
  SoundHandle,
  ClipSoundMarker,
} from './plugins/audio';
export {
  MUSIC_ENTER_BATTLE,
  MUSIC_EXIT_BATTLE,
  MUSIC_LAYER_BATTLE,
  MUSIC_LAYER_CUSTOM,
  MUSIC_LAYER_EXPLORE,
  MusicMixerSystem,
  audioMixerParser,
  audioMixerRecipe,
  crossfadeMusicLayers,
  getAudioMix,
  getMasterVolume,
  getMusicVolume,
  getSfxVolume,
  musicLayerRecipe,
  playMusicLayer,
  registerMusicLayerName,
  resolveMusicLayer,
  setMasterVolume,
  setMusicVolume,
  setSfxVolume,
  wireMusicMixerEvents,
} from './plugins/audio';
export type { AudioMix } from './plugins/audio';
export {
  MonoBehaviour,
  EntityScriptPlugin,
  EntityScriptSystem,
  coerceMonoBehaviourModule,
  getCachedMonoBehaviourModule,
  setCachedMonoBehaviourModule,
  registerEntityScripts,
  resolveEntityScriptGlobKey,
} from './plugins/entity-script';
export type {
  MonoBehaviourContext,
  MonoBehaviourModule,
  GameObjectProxy,
  EntityScriptContext,
  EntityScriptModule,
} from './plugins/entity-script';

export { Terrain, TerrainPlugin } from './plugins/terrain';
export {
  ParticlesPlugin,
  ParticleEmitter,
  spawnParticleBurst,
} from './plugins/particles';
export type { ParticleBurstOptions } from './plugins/particles';
export {
  DestructiblePlugin,
  Destructible,
  onDestructibleDestroyed,
} from './plugins/destructible';
export {
  FloatingTextPlugin,
  FloatingText,
  ScreenFloatPool,
  disposeScreenFloatPool,
  getFloatingScreenPoolSize,
  getScreenFloatPool,
  spawnFloatingText,
  spawnFloatingTextScreen,
} from './plugins/floating-text';
export type {
  FloatingTextOptions,
  FloatingTextSpace,
  ScreenFloatingTextOptions,
} from './plugins/floating-text';
export {
  SpawnerPlugin,
  SpawnerPending,
  PlacePending,
  TerrainSpawnSystem,
  TerrainPlaceSystem,
  entityParser,
  entitySpawnerRecipe,
  getPlacementSpecs,
  isNormalWithinSlopeLimit,
  normalFromHeightSampler,
  partialAlignEuler,
  registerSpawnFootprint,
  isSpawnAreaFree,
  sampleMeshSurfaceHeight,
  sampleTerrainSurface,
  setPlacementSpec,
  spawnGroupRecipe,
  spawnTemplateAtTerrain,
} from './plugins/spawner';
export type {
  ChildTemplateProfileId,
  GroundAlignMode,
  GroupSpawnDefaults,
  PlacementSpec,
  ScaleDistributionMode,
  SpawnCountMode,
  SpawnGroupProfileId,
  SpawnGroupSpec,
  SpawnTemplateRole,
  SpawnTemplateSpec,
  TerrainSurfaceSample,
  YawDistributionMode,
} from './plugins/spawner';
export {
  applyChildTemplateProfile,
  getGroupSpawnDefaults,
  isKnownGroupProfileForTests,
  normalizeGroupProfileId,
  optBool,
  optNumber,
  parseSpaceSeparatedNumbers,
  resolveGroupSpawnFields,
  roleToProfile,
  yawAnglesFromStepDeg,
} from './plugins/spawner';
export type { TerrainEntityData } from './plugins/terrain';
export {
  getGltfLocalYBounds,
  prefetchGltfLocalYBounds,
} from './plugins/gltf-xml';

export { RaycastPlugin, RaycastHit, RaycastSource } from './plugins/raycast';
export {
  BvhPlugin,
  BvhTarget,
  castBvhRay,
  getBvhContext,
  getBvhSurfaceHeight,
  getBvhStats,
  registerBvhMesh,
  unregisterBvhMesh,
  unregisterBvhForEntity,
} from './plugins/bvh';
export type { BvhRaycastHit } from './plugins/bvh';

export {
  AiSteeringPlugin,
  SteeringAgent,
  SteeringTarget,
} from './plugins/ai-steering';

export {
  NavMeshPlugin,
  NavMeshSurface,
  NavMeshWalkable,
  NavMeshAgent,
  navMeshRecipe,
  navMeshWalkableRecipe,
  navMeshAgentRecipe,
  NavMeshInitSystem,
  NavMeshAgentSystem,
  collectNavmeshGeometry,
  isNavMeshReady,
  createAgent,
  setAgentTarget,
  clearAgentTarget,
  removeAgent,
  getAgentPosition,
  getNavMeshDebugMesh,
} from './plugins/navmesh';
export type { NavMeshGeometry, AgentConfig } from './plugins/navmesh';

export { HudPlugin, HudPanel } from './plugins/hud';
export {
  HudScreenUpdateSystem,
  getHudScreenLayer,
  registerHudWidget,
  registerHudWidgetFactory,
  unregisterHudWidget,
} from './plugins/hud';
export type { HudWidget, HudWidgetFactory, WidgetHandle } from './plugins/hud';
export {
  DEFAULT_MINIMAP_COLORS,
  DEFAULT_MINIMAP_RADII,
  DEFAULT_MINIMAP_RANGE,
  DEFAULT_MINIMAP_SIZE,
  MINIMAP_CATEGORY_VALUES,
  MINIMAP_WIDGET_TYPE,
  MinimapWidget,
  collectMinimapDots,
  drawMinimap,
  minimapParser,
  minimapRecipe,
  parseMinimapOptions,
  registerMinimapWidgetFactory,
  resolveMinimapCategory,
} from './plugins/hud';
export type {
  MinimapAnchor,
  MinimapCategory,
  MinimapCollection,
  MinimapDot,
  MinimapOptions,
  MinimapPlayerMarker,
} from './plugins/hud';
export {
  getInteractionTargets,
  interactionPromptParser,
  interactionPromptRecipe,
  interactionPromptWidgetFactory,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from './plugins/hud';
export type { InteractionTarget, PromptPosition } from './plugins/hud';
export {
  COMPASS_DEFAULT_FOV,
  COMPASS_DEFAULT_NORTH,
  COMPASS_DEFAULT_NORTH_COLOR,
  cameraAzimuth,
  cardinalAzimuths,
  compassParser,
  compassRecipe,
  createCompassWidget,
  markTransform,
  wrapAngle,
} from './plugins/hud';
export type { CardinalMark, CompassConfig, MarkTransform } from './plugins/hud';
export {
  bossBarFactory,
  controlsBarFactory,
  createBossBarWidget,
  createControlsBarWidget,
  createHealthBarWidget,
  createMissionWidget,
  createResourceChipWidget,
  createTimerWidget,
  createXpBarWidget,
  healthBarFactory,
  missionFactory,
  registerHudWidgetFactories,
  resourceChipFactory,
  timerFactory,
  widgetParsers,
  widgetRecipes,
  xpBarFactory,
} from './plugins/hud';
export {
  applyPosition,
  formatTime,
  injectWidgetCss,
  makeWidgetParser,
  readAttr,
  readPosition,
  resolveTargetEntity,
} from './plugins/hud';
export type { HudPosition } from './plugins/hud';
export {
  buildTabsFromChildren,
  closeModal,
  createInventoryTab,
  createOptionsTab,
  createSkillsTab,
  createTabbedModalWidget,
  getOptionValue,
  isModalOpen,
  MODAL_ACTION,
  MODAL_OPTION_CHANGED,
  openModal,
  parseOptionDef,
  registerModalTab,
  registerOptionDef,
  setOptionValue,
  TABBED_MODAL_TAG,
  TABBED_MODAL_TYPE,
  tabbedModalParser,
  tabbedModalRecipe,
  toggleModal,
} from './plugins/hud';
export type {
  InventoryTabConfig,
  OptionDef,
  OptionRowType,
  SkillsTabConfig,
  TabbedModalConfig,
  TabContent,
  TabDescriptor,
} from './plugins/hud';

export {
  LoadingPlugin,
  LoadingScreenSystem,
  getLoadingScreenText,
  mountLoadingScreen,
  setLoadingScreenText,
} from './plugins/loading';
export type { LoadingScreenText } from './plugins/loading';
export { getActiveGltfLoadCount } from './extras/gltf-bridge';

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

/**
 * Status effects plugin: timed status effects (buffs/debuffs) with stat
 * modifiers, periodic tick effects and a lifecycle contract that cancels every
 * active status when its entity dies ({@link COMBAT_DEATH}). Status defs are
 * data-driven via the {@link DataRegistry} (`status` kind).
 */
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
  SaveLoadPlugin,
  Serializable,
  deserializeAll,
  loadFromLocalStorage,
  loadSnapshot,
  registerRpgSaveSerializers,
  registerSaveSerializer,
  saveSnapshot,
  saveToLocalStorage,
  serializeAll,
} from './plugins/save-load';
export type {
  SaveSnapshot,
  SerializableEntitySnapshot,
  SaveSerializer,
} from './plugins/save-load';

export {
  ENGINE_DEFAULT_EN_DICTIONARY,
  ENGINE_DEFAULT_LOCALE,
  I18nPlugin,
  I18nText,
  getLocale,
  loadDictionary,
  loadEngineDefaultDictionary,
  setLocale,
  t,
} from './plugins/i18n';
export { initAssetHotReload } from './vite/hot-reload-client';
export { LoadingProgress, loadWithProgress } from './extras/loading-progress';

export {
  createInteractable,
  createPickup,
  InteractableBehaviour,
  PickupBehaviour,
  interactableRecipe,
  pickupRecipe,
  toMonoBehaviourModule,
} from './extras/interactable-base';
export type {
  InteractableConfig,
  PickupConfig,
  PickupTrigger,
} from './extras/interactable-base';

export {
  createMeleeAi,
  MeleeAiBehaviour,
  meleeAiScriptRecipe,
} from './extras/melee-ai-base';

export {
  createTurretAi,
  TurretAiBehaviour,
  turretAiScriptRecipe,
} from './extras/turret-ai-base';
export type { TurretAiConfig } from './extras/turret-ai-base';

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
  acquireTarget,
  aiRandom,
  BossAiBehaviour,
  createAiInstanceState,
  createBossAi,
  getMeleeAiConfig,
  getOrCreateAiInstanceState,
  isBossPreset,
  loadMeleeAiPreset,
  meleeAiRecipe,
  presetToMeleeAiConfig,
  removeAiInstanceState,
  removeMeleeAiConfig,
  resetAiRng,
  runMeleeAiFrame,
  setAiRng,
  setMeleeAiConfig,
} from './plugins/rpg-ai';
export type {
  AiInstanceState,
  AiMode,
  BossAiPreset,
  BossRoarConfig,
  CreatureAssets,
  CreatureClips,
  CreatureLoot,
  MeleeAiConfig,
  MeleeAiPreset,
} from './plugins/rpg-ai';

export type {
  FactionTag,
  ItemDef,
  ItemStack,
  LootEntry,
  LootTable,
  ResourceKind,
  SkillDef,
  SkillEffect,
  StatModifier,
  StatusEffectDef,
} from './plugins/rpg-core/types';

/**
 * Inventory plugin: slot-based item storage with stacking, capacity and
 * add/remove events. Items are data-driven via the {@link DataRegistry}
 * (`item` kind). Mutations (`addItem`/`removeItem`) queue events that the
 * {@link InventoryEventBridgeSystem} drains each simulation step.
 */
export {
  addItem,
  getInventory,
  getItemQty,
  InventoryComponent,
  InventoryEventBridgeSystem,
  InventoryPlugin,
  removeItem,
} from './plugins/rpg-inventory';

/**
 * Economy plugin: thin orchestration layer over Vault (T6) and Inventory (T7)
 * providing atomic gold-for-items transactions. {@link buyItem} and
 * {@link sellItem} validate gold + stock + inventory room read-only before any
 * mutation, so a rejected trade leaves both sides untouched. Prices are
 * data-driven via the {@link DataRegistry} (`price` kind, `{ buy, sell }`).
 */
export {
  buyItem,
  EconomyEventBridgeSystem,
  EconomyPlugin,
  getPrice,
  GOLD_KIND,
  sellItem,
} from './plugins/rpg-economy';
export type { PriceEntry, PriceKind } from './plugins/rpg-economy';

/**
 * Combat plugin: health tracking, damage/heal helpers and projectile data.
 *
 * Register via {@link CombatPlugin}; read/modify hit points with
 * {@link Health}, {@link damageHealth}, {@link healHealth} and {@link isDead}.
 */
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

/**
 * Physics components and rigidbody accessors.
 *
 * {@link Rigidbody}, {@link Collider} and {@link CollisionEvents} are the SOA
 * components backing the Rapier simulation. {@link getBodyForEntity} and
 * {@link getRapierWorld} return the underlying Rapier handles for an entity or
 * the whole world when gameplay code needs direct physics access.
 */
export { Rigidbody, Collider, CollisionEvents } from './plugins/physics';
export { getRapierWorld } from './plugins/physics';
export { getBodyForEntity, PhysicsStepSystem } from './plugins/physics/systems';

/**
 * Character ground-snap helpers for placing a body so its feet rest on a surface.
 */
export {
  GROUND_CONTACT_SKIN,
  getBodyYForFeetAt,
  getCharacterFeetY,
} from './plugins/physics/character-ground';

/**
 * Terrain height queries used to align gameplay objects to the ground.
 *
 * {@link getTerrainHeightAt} samples the terrain heightmap; {@link getBvhSurfaceHeight}
 * (re-exported above) raycasts the terrain BVH. {@link getTerrainContext} exposes
 * per-field runtime data; {@link isTerrainDynamicsBlocking} reports whether terrain
 * dynamics currently block spawning/placement.
 */
export { getTerrainHeightAt, getTerrainContext } from './plugins/terrain';
export { isTerrainDynamicsBlocking } from './plugins/terrain/utils';

/**
 * Post-processing component: bloom, vignette, tone mapping, DOF, SSAO, etc.
 */
export { Postprocessing } from './plugins/postprocessing/components';

/**
 * Debug overlay plugin (wireframes, stats) + PostFx debug toggle system
 * (keys Digit1-6 to cycle bloom/CA/vignette/AA/SSAO/toneMapping; opt-in via DebugPlugin).
 */
export { DebugPlugin, PostFxToggleSystem } from './plugins/debug';

/**
 * Spawn gate plugin: opt-in latch that holds entities at their spawn Y until
 * the terrain underneath is both heightmap-decoded and heightfield-backed,
 * then snaps them to the ground. {@link gateEntity} marks any entity for
 * gating; `<SpawnGate target-entity="hero" y-fallback="50"/>` is the
 * declarative form.
 */
export {
  SpawnGateComponent,
  SpawnGatePlugin,
  gateEntity,
} from './plugins/spawn-gate';

/*
 * ──────────────────────────────────────────────────────────────────────────
 * Internal escape-hatches (marked @internal).
 *
 * `getRenderingContext` and `threeCameras` are engine-rendering internals: they
 * expose the live WebGL renderer/scene graph and the THREE.Camera registry.
 * They are NOT part of the stable gameplay API and may change without notice.
 * They are re-exported here solely so engine examples/games that need
 * renderer-level access (disposing a post-processing pass, reading the active
 * camera for HUD projection) can do so through the public barrel instead of
 * reaching into `../../src/plugins/rendering/*`. Prefer the higher-level
 * helpers above whenever they suffice.
 * ──────────────────────────────────────────────────────────────────────────
 */
/** @internal live rendering context (renderer, scene, canvas, post-processing). */
export { getRenderingContext } from './plugins/rendering';
/** @internal registry of active THREE.Camera instances keyed by camera entity. */
export { threeCameras } from './plugins/rendering';

/**
 * Pause coordination plugin: modal stack that suppresses time (timeScale=0)
 * and gameplay input while any modal is open.
 *
 * Register via {@link PauseCoordinatorPlugin}; drive with {@link pushModal} /
 * {@link popModal} and query with {@link isPaused} / {@link getActiveModal}.
 */
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

let globalBuilder: GameBuilder | null = null;

export function getBuilder(): GameBuilder {
  if (!globalBuilder) {
    globalBuilder = new GameBuilder();
  }
  return globalBuilder;
}

export function resetBuilder(): void {
  disposeAllRuntimes();
  globalBuilder = null;
}

export function withPlugin(plugin: Plugin) {
  return getBuilder().withPlugin(plugin);
}

export function withPlugins(...plugins: Plugin[]) {
  return getBuilder().withPlugins(...plugins);
}

export function withoutDefaultPlugins() {
  return getBuilder().withoutDefaultPlugins();
}

export function withoutPlugins(...plugins: Plugin[]) {
  return getBuilder().withoutPlugins(...plugins);
}

export function withSystem(system: System) {
  return getBuilder().withSystem(system);
}

export function withComponent(name: string, component: Component) {
  return getBuilder().withComponent(name, component);
}

export function configure(options: BuilderOptions) {
  return getBuilder().configure(options);
}

export async function run() {
  const builder = getBuilder();
  disposeAllRuntimes();
  globalBuilder = null;
  return builder.run();
}
