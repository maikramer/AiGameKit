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
  loadGltfToScene,
  loadGltfToSceneWithAnimator,
} from './extras/gltf-bridge';
export type { GltfLoadResult } from './extras/gltf-bridge';
export { GltfAnimator } from './extras/gltf-animator';
export type { GltfAnimatorOptions } from './extras/gltf-animator';
export {
  applyEquirectSkyEnvironment,
  autoLoadSkyEnvironment,
} from './extras/sky-env';
export type { EquirectSkyOptions } from './extras/sky-env';

export { Player, PlayerGltfConfig, playerGltfRecipe } from './plugins/player';
export {
  FollowCamera,
  FollowCameraPlugin,
  ZOOM_PRESETS,
} from './plugins/follow-camera';
export { OrbitCamera, OrbitCameraPlugin } from './plugins/orbit-camera';
export { getScene } from './plugins/rendering';
export { Renderer } from './plugins/rendering';
export { Transform, WorldTransform } from './plugins/transforms';
export { AnimatedCharacter, HasAnimator } from './plugins/animation';
export {
  animatorRegistry,
  GltfAnimationState,
  GltfAnimPlugin,
  GltfAnimationUpdateSystem,
  registerAnimator,
} from './plugins/gltf-anim';
export { isKeyDown } from './plugins/input';
export {
  AudioEmitter,
  AudioListener,
  AudioPlugin,
  AudioSystem,
  playAudioEmitter,
  registerAudioClip,
  resumeAudioContextIfSuspended,
  resumeAudioContextOnFirstUserGesture,
} from './plugins/audio';
export {
  EntityScript,
  EntityScriptPlugin,
  EntityScriptSystem,
  registerEntityScripts,
  resolveEntityScriptGlobKey,
} from './plugins/entity-script';
export type {
  EntityScriptContext,
  EntityScriptModule,
} from './plugins/entity-script';
export { Lod, LodPlugin, LodSystem } from './plugins/lod';
export { Sprite, SpritePlugin, SpriteSystem } from './plugins/sprite';
export {
  loadSceneManifest,
  SceneManifestPlugin,
} from './plugins/scene-manifest';
export type {
  SceneManifest,
  SceneManifestEntry,
} from './plugins/scene-manifest';
export { Terrain, TerrainPlugin } from './plugins/terrain';
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
  sampleTerrainSurface,
} from './plugins/spawner';
export type {
  ChildTemplateProfileId,
  GroundAlignMode,
  GroupSpawnDefaults,
  PlacementSpec,
  SpawnGroupProfileId,
  SpawnGroupSpec,
  SpawnTemplateRole,
  SpawnTemplateSpec,
  TerrainSurfaceSample,
} from './plugins/spawner';
export {
  applyChildTemplateProfile,
  getGroupSpawnDefaults,
  isKnownGroupProfileForTests,
  normalizeGroupProfileId,
  optBool,
  optNumber,
  resolveGroupSpawnFields,
} from './plugins/spawner';
export type { TerrainEntityData } from './plugins/terrain';
export {
  getGltfLocalYBounds,
  prefetchGltfLocalYBounds,
} from './plugins/gltf-xml';
export { Water, WaterPlugin } from './plugins/water';
export { Text3dModel, Text3dPlugin, text3dRecipe } from './plugins/text-3d';
export type { WaterEntityData } from './plugins/water';
export { DebugPlugin } from './plugins/debug';
export type { VibeGameDebugBridge } from './plugins/debug';
export { RaycastPlugin, RaycastResult, RaycastSource } from './plugins/raycast';
export { NavmeshPlugin, NavAgent, NavMesh } from './plugins/navmesh';
export {
  AiSteeringPlugin,
  SteeringAgent,
  SteeringTarget,
} from './plugins/ai-steering';
export {
  ParticlesPlugin,
  ParticlesBurst,
  ParticlesEmitter,
} from './plugins/particles';
export { HudPlugin, HudPanel } from './plugins/hud';
export { JointsPlugin, PhysicsJoint } from './plugins/joints';
export {
  SaveLoadPlugin,
  Serializable,
  loadFromLocalStorage,
  loadSnapshot,
  saveSnapshot,
  saveToLocalStorage,
} from './plugins/save-load';
export {
  NetworkPlugin,
  NetworkBuffer,
  Networked,
  setNetworkConfig,
} from './plugins/network';
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

function getBuilder(): GameBuilder {
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
