import type { Plugin } from '../core';
import { AiSteeringPlugin } from './ai-steering/plugin';
import { AnimationPlugin } from './animation/plugin';
import { BvhPlugin } from './bvh/plugin';
import { PlayerControllerPlugin } from './player-controller/plugin';
import { GltfAnimPlugin } from './gltf-anim/plugin';
import { EntityScriptPlugin } from './entity-script/plugin';
import { GltfXmlPlugin } from './gltf-xml/plugin';
import { InputPlugin } from './input/plugin';
import { OrbitCameraPlugin } from './orbit-camera/plugin';

import { PhysicsPlugin } from './physics/plugin';
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
import { TweeningPlugin } from './tweening/plugin';
import { PostprocessingPlugin } from './postprocessing/plugin';
import { VegetationPlugin } from './vegetation/plugin';

export const DefaultPlugins: Plugin[] = [
  TransformsPlugin,
  GltfXmlPlugin,
  EntityScriptPlugin,
  GltfAnimPlugin,
  AnimationPlugin,
  InputPlugin,
  PhysicsPlugin,
  RaycastPlugin,
  AiSteeringPlugin,
  RenderingPlugin,
  PostprocessingPlugin,
  HudPlugin,
  PlayerControllerPlugin,
  OrbitCameraPlugin,
  PlayerPlugin,
  StartupPlugin,
  TerrainPlugin,
  BvhPlugin,
  SpawnerPlugin,
  VegetationPlugin,
  AudioPlugin,
  EquirectSkyPlugin,
  ParticlesPlugin,
  FloatingTextPlugin,
  TweeningPlugin,
];
