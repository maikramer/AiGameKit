import type { Adapter, Plugin, State } from '../../core';
import { parseColor } from '../../core/validation/schemas';
import { Terrain, TerrainDebugInfo } from './components';
import { terrainRecipe } from './recipes';
import {
  TerrainBootstrapSystem,
  TerrainDebugSystem,
  TerrainPhysicsSystem,
  TerrainRenderSystem,
} from './systems';
import { setTerrainHeightmapUrl, setTerrainTextureUrl } from './utils';

function terrainColorAdapter(
  field: keyof typeof Terrain
): Adapter {
  return ((entity: number, value: string, _state: State) => {
    Terrain[field][entity] = parseColor(value) >>> 0;
  }) as Adapter;
}

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
        roughness: 0.85,
        metalness: 0.0,
        normalStrength: 1.0,
        skirtDepth: 1.0,
        skirtWidth: 0.015625,
        baseColor: 0x4a7a3a,
        heightSmoothing: 0.35,
        heightSmoothingSpread: 1.25,
        collisionResolution: 64,
        showChunkBorders: 0,
        snowHeight: 0.75,
        colorHigh: 0xffffff,
        colorMid: 0x7a9a4a,
        colorLow: 0x4a6a2a,
        colorRock: 0x808080,
        slopeThreshold: 0.55,
        slopeSoftness: 0.1,
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
        'base-color': terrainColorAdapter('baseColor') as Adapter,
        'color-high': terrainColorAdapter('colorHigh') as Adapter,
        'color-mid': terrainColorAdapter('colorMid') as Adapter,
        'color-low': terrainColorAdapter('colorLow') as Adapter,
        'color-rock': terrainColorAdapter('colorRock') as Adapter,
      },
    },
  },
};
