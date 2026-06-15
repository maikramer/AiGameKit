import type { BuilderOptions } from './builder';
import { GameBuilder } from './builder';
import type { Component, Plugin, System } from './core';
import { disposeAllRuntimes } from './core/runtime-manager';

export * from './core';
export type { BuilderOptions };
export type { GameRuntime } from './runtime';
export {
  applyDefaultShadowFlags,
  createGLTFLoader,
  loadGltfAnimated,
  loadGltfLodToScene,
  loadGltfToScene,
  loadGltfToSceneWithAnimator,
  normalizeGltfMaterials,
  setKTX2TranscoderPath,
} from './extras/gltf-bridge';
export type { GltfLoadResult } from './extras/gltf-bridge';
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
export { isKeyDown, addInputMapping, InputState } from './plugins/input';
export {
  AudioSource,
  AudioListener,
  AudioPlugin,
  AudioSystem,
  playAudioEmitter,
  registerAudioClip,
  resumeAudioContextIfSuspended,
  resumeAudioContextOnFirstUserGesture,
} from './plugins/audio';
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
/** @deprecated Use coerceMonoBehaviourModule. */
export { coerceMonoBehaviourModule as coerceEntityScriptModule } from './plugins/entity-script';
/** @deprecated Use getCachedMonoBehaviourModule. */
export { getCachedMonoBehaviourModule as getCachedEntityScriptModule } from './plugins/entity-script';
/** @deprecated Use setCachedMonoBehaviourModule. */
export { setCachedMonoBehaviourModule as setCachedEntityScriptModule } from './plugins/entity-script';
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
  spawnFloatingText,
} from './plugins/floating-text';
export type { FloatingTextOptions } from './plugins/floating-text';
export {
  SpawnerPlugin,
  SpawnerPending,
  PlacePending,
  TerrainSpawnSystem,
  TerrainPlaceSystem,
  spawnGroupRecipe,
  entitySpawnerRecipe,
  entityParser,
  spawnTemplateAtTerrain,
  getPlacementSpecs,
  setPlacementSpec,
  isNormalWithinSlopeLimit,
  normalFromHeightSampler,
  partialAlignEuler,
  sampleMeshSurfaceHeight,
  sampleTerrainSurface,
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
  LoadingPlugin,
  LoadingScreenSystem,
  getLoadingScreenText,
  mountLoadingScreen,
  setLoadingScreenText,
} from './plugins/loading';
export type { LoadingScreenText } from './plugins/loading';
export { getActiveGltfLoadCount } from './extras/gltf-bridge';

export {
  SaveLoadPlugin,
  Serializable,
  loadFromLocalStorage,
  loadSnapshot,
  saveSnapshot,
  saveToLocalStorage,
} from './plugins/save-load';

export {
  I18nPlugin,
  I18nText,
  getLocale,
  loadDictionary,
  setLocale,
  t,
} from './plugins/i18n';
export { initAssetHotReload } from './vite/hot-reload-client';
export { LoadingProgress, loadWithProgress } from './extras/loading-progress';

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
