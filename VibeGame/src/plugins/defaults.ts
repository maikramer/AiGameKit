import type { Plugin } from '../core';
import { AiSteeringPlugin } from './ai-steering/plugin';
import { AnimationPlugin } from './animation/plugin';
import { FollowCameraPlugin } from './follow-camera/plugin';
import { GltfAnimPlugin } from './gltf-anim/plugin';
import { GltfXmlPlugin } from './gltf-xml/plugin';
import { InputPlugin } from './input/plugin';
import { JointsPlugin } from './joints/plugin';
import { NavmeshPlugin } from './navmesh/plugin';
import { OrbitCameraPlugin } from './orbit-camera/plugin';
import { ParticlesPlugin } from './particles/plugin';
import { PhysicsPlugin } from './physics/plugin';
import { HudPlugin } from './hud/plugin';
import { PlayerPlugin } from './player/plugin';
import { RaycastPlugin } from './raycast/plugin';
import { RenderingPlugin } from './rendering/plugin';
import { RespawnPlugin } from './respawn/plugin';
import { StartupPlugin } from './startup/plugin';
import { SpawnerPlugin } from './spawner/plugin';
import { TerrainPlugin } from './terrain/plugin';
import { TransformsPlugin } from './transforms';
import { TweenPlugin } from './tweening';
import { DebugPlugin } from './debug/plugin';
import { FogPlugin } from './fog/plugin';
import { Text3dPlugin } from './text-3d/plugin';
import { SkyPlugin } from './sky/plugin';
import { AudioPlugin } from './audio/plugin';
import { WaterPlugin } from './water/plugin';
import { PostprocessingPlugin } from './postprocessing/plugin';

export const DefaultPlugins: Plugin[] = [
  TweenPlugin,
  TransformsPlugin,
  GltfXmlPlugin,
  GltfAnimPlugin,
  AnimationPlugin,
  InputPlugin,
  PhysicsPlugin,
  RaycastPlugin,
  NavmeshPlugin,
  AiSteeringPlugin,
  JointsPlugin,
  RenderingPlugin,
  ParticlesPlugin,
  HudPlugin,
  FollowCameraPlugin,
  OrbitCameraPlugin,
  PlayerPlugin,
  StartupPlugin,
  RespawnPlugin,
  TerrainPlugin,
  SpawnerPlugin,
  FogPlugin,
  PostprocessingPlugin,
  SkyPlugin,
  WaterPlugin,
  AudioPlugin,
  Text3dPlugin,
  DebugPlugin,
];
