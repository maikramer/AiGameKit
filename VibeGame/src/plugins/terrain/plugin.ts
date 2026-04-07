import type { Adapter, Plugin } from '../../core';
import { Terrain, TerrainDebugInfo } from './components';
import { terrainRecipe } from './recipes';
import {
  TerrainBootstrapSystem,
  TerrainDebugSystem,
  TerrainPhysicsSystem,
  TerrainRenderSystem,
} from './systems';
import { setTerrainHeightmapUrl, setTerrainTextureUrl } from './utils';

export const TerrainPlugin: Plugin = {
  recipes: [terrainRecipe],
  systems: [
    TerrainBootstrapSystem,
    TerrainPhysicsSystem,
    TerrainRenderSystem,
    TerrainDebugSystem,
  ],
  components: {
    terrain: Terrain,
    terrainDebugInfo: TerrainDebugInfo,
  },
  config: {
    defaults: {
      terrain: {
        worldSize: 256,
        maxHeight: 50,
        levels: 6,
        resolution: 64,
        lodDistanceRatio: 2.0,
        lodHysteresis: 1.2,
        wireframe: 0,
        // Material
        roughness: 0.85,
        metalness: 0.0,
        normalStrength: 1.0,
        skirtDepth: 1.0,
        // Physics
        collisionResolution: 64,
        // Debug
        showChunkBorders: 0,
      },
    },
    adapters: {
      terrain: {
        heightmap: ((entity, value, state) => {
          setTerrainHeightmapUrl(state, entity, value);
        }) as Adapter,
        texture: ((entity, value, state) => {
          setTerrainTextureUrl(state, entity, value);
        }) as Adapter,
      },
    },
  },
};
