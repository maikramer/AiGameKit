import type { Adapter, Plugin } from '../../core';
import { Terrain } from './components';
import { terrainRecipe } from './recipes';
import {
  TerrainBootstrapSystem,
  TerrainPhysicsSystem,
  TerrainRenderSystem,
} from './systems';
import { setTerrainHeightmapUrl, setTerrainTextureUrl } from './utils';

export const TerrainPlugin: Plugin = {
  recipes: [terrainRecipe],
  systems: [TerrainBootstrapSystem, TerrainPhysicsSystem, TerrainRenderSystem],
  components: {
    terrain: Terrain,
  },
  config: {
    defaults: {
      terrain: {
        worldSize: 256,
        maxHeight: 50,
        levels: 6,
        resolution: 64,
        lodDistanceRatio: 2.0,
        wireframe: 0,
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
