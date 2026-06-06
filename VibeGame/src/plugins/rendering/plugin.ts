import type { Plugin } from '../../core';
import {
  AmbientLight,
  DirectionalLight,
  DistanceCull,
  MainCamera,
  PointLight,
  RenderContext,
  MeshRenderer,
  SpotLight,
} from './components';
import { pointLightRecipe, rendererRecipe, spotLightRecipe } from './recipes';
import {
  CameraSyncSystem,
  DistanceCullSystem,
  LightSyncSystem,
  MeshInstanceSystem,
  PointSpotLightSyncSystem,
  RendererSetupSystem,
  SceneRenderSystem,
} from './systems';
import {
  TextureRecipeLoadSystem,
  TextureRecipeCleanupSystem,
} from './texture-recipe-system';
import { TextureRecipe, TextureRecipeLoaded } from './texture-recipe';

export const RenderingPlugin: Plugin = {
  recipes: [rendererRecipe, pointLightRecipe, spotLightRecipe],
  systems: [
    RendererSetupSystem,
    TextureRecipeLoadSystem,
    DistanceCullSystem,
    MeshInstanceSystem,
    LightSyncSystem,
    PointSpotLightSyncSystem,
    CameraSyncSystem,
    SceneRenderSystem,
    TextureRecipeCleanupSystem,
  ],
  components: {
    meshRenderer: MeshRenderer,
    DistanceCull,
    RenderContext,
    MainCamera,
    AmbientLight,
    DirectionalLight,
    PointLight,
    SpotLight,
    TextureRecipe,
    TextureRecipeLoaded,
  },
  config: {
    defaults: {
      ambientLight: {
        skyColor: 0x87ceeb,
        groundColor: 0x4a4a4a,
        intensity: 0.6,
      },
      directionalLight: {
        color: 0xffffff,
        intensity: 1,
        castShadow: 1,
        shadowMapSize: 4096,
        directionX: -1,
        directionY: 2,
        directionZ: -1,
        distance: 30,
      },
      meshRenderer: {
        visible: 1,
        sizeX: 1,
        sizeY: 1,
        sizeZ: 1,
        color: 0xffffff,
        unlit: 0,
      },
      distanceCull: {
        maxDistance: 0,
      },
      mainCamera: {
        projection: 0,
        fov: 75,
        orthoSize: 10,
      },
      pointLight: {
        color: 0xffffff,
        intensity: 1,
        distance: 0,
        decay: 2,
        castShadow: 0,
      },
      spotLight: {
        color: 0xffffff,
        intensity: 1,
        distance: 0,
        decay: 2,
        angle: Math.PI / 3,
        penumbra: 0,
        castShadow: 0,
      },
    },
    enums: {
      meshRenderer: {
        shape: {
          box: 0,
          sphere: 1,
        },
      },
      mainCamera: {
        projection: {
          perspective: 0,
          orthographic: 1,
        },
      },
    },
  },
};
