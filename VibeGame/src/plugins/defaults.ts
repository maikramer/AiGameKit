import type { Plugin } from '../core';
import { YukaAiPlugin } from './ai-yuka/plugin';
import { AnimationPlugin } from './animation/plugin';
import { BvhPlugin } from './bvh/plugin';
import { CompositionPlugin } from './composition/plugin';
import { GroupPlugin } from './group/plugin';
import { ThirdPersonCameraPlugin } from './player-controller/plugin';
import { GltfAnimPlugin } from './gltf-anim/plugin';
import { EntityScriptPlugin } from './entity-script/plugin';
import { GltfXmlPlugin } from './gltf-xml/plugin';
import { InputPlugin } from './input/plugin';
import { NavMeshPlugin } from './navmesh/plugin';
import { OrbitCameraPlugin } from './orbit-camera/plugin';

import { PhysicsPlugin } from './physics/plugin';
import { PrecomputePlugin } from './asset-precompute/plugin';
import { HudPlugin } from './hud/plugin';
import { PlayerPlugin } from './player/plugin';
import { RaycastPlugin } from './raycast/plugin';
import { RenderingPlugin } from './rendering/plugin';
import { StartupPlugin } from './startup/plugin';
import { SpawnerPlugin } from './spawner/plugin';
import { TerrainPlugin } from './terrain/plugin';
import { TransformsPlugin } from './transforms';
import { AudioPlugin } from './audio/plugin';
import { EquirectSkyPlugin } from './sky/plugin';
import { ParticlesPlugin } from './particles/plugin';
import { FloatingTextPlugin } from './floating-text/plugin';
import { DestructiblePlugin } from './destructible/plugin';
import { TweeningPlugin } from './tweening/plugin';
import { PostprocessingPlugin } from './postprocessing/plugin';
import { AdaptiveQualityPlugin } from './adaptive-quality/plugin';
import { BiomesPlugin } from './biomes/plugin';
import { RoadPlugin } from './road/plugin';
import { WaterPlugin } from './water/plugin';
import { WeatherPlugin } from './weather/plugin';
import { VegetationPlugin } from './vegetation/plugin';
import { CityLayoutPlugin } from './city-layout/plugin';
import { RacingPlugin } from './racing/plugin';

export const DefaultPlugins: Plugin[] = [
  TransformsPlugin,
  GroupPlugin,
  GltfXmlPlugin,
  EntityScriptPlugin,
  GltfAnimPlugin,
  AnimationPlugin,
  InputPlugin,
  PhysicsPlugin,
  PrecomputePlugin,
  RaycastPlugin,
  RenderingPlugin,
  PostprocessingPlugin,
  AdaptiveQualityPlugin,
  HudPlugin,
  ThirdPersonCameraPlugin,
  OrbitCameraPlugin,
  PlayerPlugin,
  StartupPlugin,
  TerrainPlugin,
  BvhPlugin,
  SpawnerPlugin,
  CompositionPlugin,
  NavMeshPlugin,
  YukaAiPlugin,
  AudioPlugin,
  EquirectSkyPlugin,
  BiomesPlugin,
  WaterPlugin,
  RoadPlugin,
  CityLayoutPlugin,
  WeatherPlugin,
  VegetationPlugin,
  ParticlesPlugin,
  FloatingTextPlugin,
  DestructiblePlugin,
  TweeningPlugin,
  RacingPlugin,
];
