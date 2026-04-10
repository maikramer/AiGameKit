import type { Plugin } from '../../core';
import {
  AmbientLight,
  CsmConfig,
  DirectionalLight,
  MainCamera,
  PointLight,
  RenderContext,
  Renderer,
  SpotLight,
} from './components';
import { pointLightRecipe, rendererRecipe, spotLightRecipe } from './recipes';
import {
  CameraSyncSystem,
  LightSyncSystem,
  MeshInstanceSystem,
  PointSpotLightSyncSystem,
  WebGLRenderSystem,
} from './systems';
import {
  TextureRecipeLoadSystem,
  TextureRecipeCleanupSystem,
} from './texture-recipe-system';
import { TextureRecipe, TextureRecipeLoaded } from './texture-recipe';

export const RenderingPlugin: Plugin = {
  recipes: [rendererRecipe, pointLightRecipe, spotLightRecipe],
  systems: [
    TextureRecipeLoadSystem,
    MeshInstanceSystem,
    LightSyncSystem,
    PointSpotLightSyncSystem,
    CameraSyncSystem,
    WebGLRenderSystem,
    TextureRecipeCleanupSystem,
  ],
  components: {
    Renderer,
    RenderContext,
    MainCamera,
    AmbientLight,
    DirectionalLight,
    PointLight,
    SpotLight,
    CsmConfig,
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
      renderer: {
        visible: 1,
        sizeX: 1,
        sizeY: 1,
        sizeZ: 1,
        color: 0xffffff,
        unlit: 0,
      },
      mainCamera: {
        projection: 0,
        fov: 75,
        orthoSize: 10,
      },
      csmConfig: {
        cascades: 4,
        maxFar: 200,
        shadowMapSize: 2048,
        enabled: 1,
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
      renderer: {
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
