import type { Adapter, Plugin, State } from '../../core';
import { parseColor } from '../../core/validation/schemas';
import {
  Terrain,
  TerrainChunk,
  TerrainDebugInfo,
  TerrainPad,
} from './components';
import { terrainPadRecipe, terrainRecipe } from './recipes';
import { TerrainPadApplySystem, terrainPadParser } from './pad-systems';
import {
  TerrainDebugSystem,
  TerrainFieldBootstrapSystem,
  TerrainHeightColorSyncSystem,
  TerrainLodSelectSystem,
  TerrainMeshSystem,
  TerrainChunkColliderSystem,
} from './systems';
import { TerrainReadyGateSystem } from './ready-gate';
import { setTerrainHeightmapUrl, setTerrainTextureUrl } from './utils';

function terrainColorAdapter(field: keyof typeof Terrain): Adapter {
  return ((entity: number, value: string, _state: State) => {
    Terrain[field][entity] = parseColor(value) >>> 0;
  }) as Adapter;
}

export const TerrainPlugin: Plugin = {
  recipes: [terrainRecipe, terrainPadRecipe],
  systems: [
    TerrainFieldBootstrapSystem,
    TerrainPadApplySystem,
    TerrainChunkColliderSystem,
    TerrainLodSelectSystem,
    TerrainMeshSystem,
    TerrainHeightColorSyncSystem,
    TerrainDebugSystem,
    TerrainReadyGateSystem,
  ],
  components: {
    terrain: Terrain,
    terrainChunk: TerrainChunk,
    terrainDebugInfo: TerrainDebugInfo,
    'terrain-pad': TerrainPad,
  },
  config: {
    defaults: {
      'terrain-pad': {
        halfX: 8,
        halfZ: 8,
        height: 0,
        falloff: 8,
        cornerRadius: 4,
        applied: 0,
      },
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
        textureTileSize: 0,
        skirtDepth: 1.0,
        skirtWidth: 0.015625,
        baseColor: 0x4a7a3a,
        // 1 = Catmull-Rom no sampler (normais contínuas); 0 = bilinear.
        heightSmoothing: 1,
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
        // 0.35 keeps the texture albedo dominant; the height/slope tint is a
        // subtle ambient layer (cumes mais frias, vales mais quentes, rocha em
        // encostas) rather than overriding biome colours.
        heightBlendStrength: 0.35,
        aoStrength: 0.85,
        // Subtle valley/midland sand patches via world-XZ fBm — breaks up
        // uniform grass without needing a 5th splat channel. 0 disables.
        noiseSandStrength: 0.4,
        noiseSandScale: 0.014,
        noiseSandThreshold: 0.58,
        noiseSandHeightMin: 0.02,
        noiseSandHeightMax: 0.48,
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
    parsers: {
      TerrainPad: terrainPadParser,
    },
  },
};
