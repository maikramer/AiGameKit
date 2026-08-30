import { describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { TransformsPlugin } from 'aigamekit-vibegame/transforms';
import { WaterPlugin } from '../../../src/plugins/water/plugin';
import { getRiverPath } from '../../../src/plugins/water/components';
import {
  parseTerrainData,
  spawnWaterEntitiesFromTerrainData,
  type TerrainData,
} from '../../../src/plugins/terrain/terrain-data-loader';

const VALID_TERRAIN_JSON = {
  version: '1.0',
  terrain: { size: 1024, world_size: 256.0, max_height: 50.0 },
  rivers: [
    {
      id: 0,
      source: [512, 0] as [number, number],
      path: [
        [512, 0],
        [511, 1],
        [510, 2],
      ] as Array<[number, number]>,
      length: 30,
    },
  ],
  lakes: [
    {
      id: 0,
      center_pixel: [200, 300] as [number, number],
      surface_level: 0.5,
      surface_height: 25.0,
      area_pixels: 500,
    },
  ],
  lake_planes: [
    {
      lake_id: 0,
      pos_x: 50.0,
      pos_y: 25.0,
      pos_z: 75.0,
      size_x: 12.5,
      size_z: 7.5,
    },
  ],
};

describe('terrain-data-loader', () => {
  describe('parseTerrainData', () => {
    it('parses valid terrain JSON with all fields', () => {
      const result = parseTerrainData(VALID_TERRAIN_JSON);

      expect(result.version).toBe('1.0');
      expect(result.terrain.size).toBe(1024);
      expect(result.terrain.world_size).toBe(256.0);
      expect(result.terrain.max_height).toBe(50.0);
      expect(result.rivers).toHaveLength(1);
      expect(result.rivers[0].id).toBe(0);
      expect(result.rivers[0].path).toHaveLength(3);
      expect(result.lakes).toHaveLength(1);
      expect(result.lakes[0].surface_height).toBe(25.0);
      expect(result.lake_planes).toHaveLength(1);
      expect(result.lake_planes[0].pos_x).toBe(50.0);
      expect(result.lake_planes[0].pos_y).toBe(25.0);
    });

    it('handles missing optional fields (empty rivers and lakes)', () => {
      const minimal = {
        version: '1.0',
        terrain: { size: 512, world_size: 128.0, max_height: 30.0 },
      };

      const result = parseTerrainData(minimal);

      expect(result.rivers).toEqual([]);
      expect(result.lakes).toEqual([]);
      expect(result.lake_planes).toEqual([]);
    });

    it('handles empty arrays for rivers, lakes, and lake_planes', () => {
      const data = {
        version: '1.0',
        terrain: { size: 256, world_size: 64.0, max_height: 20.0 },
        rivers: [],
        lakes: [],
        lake_planes: [],
      };

      const result = parseTerrainData(data);

      expect(result.rivers).toEqual([]);
      expect(result.lakes).toEqual([]);
      expect(result.lake_planes).toEqual([]);
    });

    it('preserves optional height stats when present', () => {
      const data = {
        version: '1.0',
        terrain: {
          size: 1024,
          world_size: 256.0,
          max_height: 50.0,
          height_min: 0.01,
          height_max: 0.99,
          height_mean: 0.45,
        },
        rivers: [],
        lakes: [],
        lake_planes: [],
      };

      const result = parseTerrainData(data);

      expect(result.terrain.height_min).toBe(0.01);
      expect(result.terrain.height_max).toBe(0.99);
      expect(result.terrain.height_mean).toBe(0.45);
    });

    it('throws on non-object input', () => {
      expect(() => parseTerrainData(null)).toThrow('non-null object');
      expect(() => parseTerrainData('string')).toThrow('non-null object');
      expect(() => parseTerrainData(42)).toThrow('non-null object');
    });

    it('throws on missing version', () => {
      const data = { terrain: { size: 1, world_size: 1, max_height: 1 } };
      expect(() => parseTerrainData(data)).toThrow('"version"');
    });

    it('throws on missing terrain', () => {
      const data = { version: '1.0' };
      expect(() => parseTerrainData(data)).toThrow('"terrain"');
    });

    it('throws on missing terrain.size', () => {
      const data = {
        version: '1.0',
        terrain: { world_size: 1, max_height: 1 },
      };
      expect(() => parseTerrainData(data)).toThrow('"terrain.size"');
    });

    it('throws on missing terrain.world_size', () => {
      const data = { version: '1.0', terrain: { size: 1, max_height: 1 } };
      expect(() => parseTerrainData(data)).toThrow('"terrain.world_size"');
    });

    it('throws on missing terrain.max_height', () => {
      const data = { version: '1.0', terrain: { size: 1, world_size: 1 } };
      expect(() => parseTerrainData(data)).toThrow('"terrain.max_height"');
    });
  });

  describe('spawnWaterEntitiesFromTerrainData', () => {
    it('handles empty terrain data without error', () => {
      const data: TerrainData = {
        version: '1.0',
        terrain: { size: 256, world_size: 64.0, max_height: 20.0 },
        rivers: [],
        lakes: [],
        lake_planes: [],
      };

      expect(() =>
        spawnWaterEntitiesFromTerrainData({} as State, data)
      ).not.toThrow();
    });

    it('handles terrain data with empty lake_planes without error', () => {
      const data: TerrainData = {
        version: '1.0',
        terrain: { size: 1024, world_size: 256.0, max_height: 50.0 },
        rivers: [],
        lakes: [],
        lake_planes: [],
      };

      // No rivers → no entity creation → no State API needed.
      expect(() =>
        spawnWaterEntitiesFromTerrainData({} as State, data)
      ).not.toThrow();
    });
  });

  describe('heightmap_format', () => {
    it('defaults to "png" when the field is absent', () => {
      const data = {
        version: '1.0',
        terrain: { size: 1024, world_size: 256.0, max_height: 50.0 },
        rivers: [],
        lakes: [],
        lake_planes: [],
      };
      const result = parseTerrainData(data);
      expect(result.heightmap_format).toBe('png');
    });

    it('reads "ahgt" when declared', () => {
      const data = {
        version: '1.0',
        terrain: { size: 1024, world_size: 256.0, max_height: 50.0 },
        rivers: [],
        lakes: [],
        lake_planes: [],
        heightmap_format: 'ahgt',
      };
      const result = parseTerrainData(data);
      expect(result.heightmap_format).toBe('ahgt');
    });

    it('normalizes an unknown value back to "png"', () => {
      const data = {
        version: '1.0',
        terrain: { size: 1024, world_size: 256.0, max_height: 50.0 },
        rivers: [],
        lakes: [],
        lake_planes: [],
        heightmap_format: 'exr',
      };
      const result = parseTerrainData(data);
      expect(result.heightmap_format).toBe('png');
    });
  });

  describe('spawnWaterEntitiesFromTerrainData — rivers', () => {
    it('creates a River entity per river with world-coord path', () => {
      // terrain 4 px over 8 m world → pixel→world scale 2 m/px, centred.
      // world = (px/4)*8 - 4 = px*2 - 4.
      const data = {
        version: '1.0',
        terrain: { size: 4, world_size: 8, max_height: 5 },
        rivers: [
          {
            id: 0,
            source: [0, 0] as [number, number],
            path: [
              [0, 0],
              [2, 2],
              [4, 0],
            ] as Array<[number, number]>,
            length: 10,
          },
        ],
        lakes: [],
        lake_planes: [],
      };
      const state = new State();
      state.registerPlugin(TransformsPlugin);
      state.registerPlugin(WaterPlugin);
      const parsed = parseTerrainData(data);
      spawnWaterEntitiesFromTerrainData(state, parsed);

      // Find the River entity by scanning for a non-empty path in the
      // side-channel (entity ids are small integers starting near 0).
      let foundPath: number[] | null = null;
      for (let e = 0; e < 2000; e++) {
        const p = getRiverPath(state, e);
        if (p.length > 0) {
          foundPath = p;
          break;
        }
      }
      expect(foundPath).not.toBeNull();
      // pixel (0,0) → world (-4,-4); (2,2) → (0,0); (4,0) → (4,-4).
      expect(foundPath).toEqual([-4, -4, 0, 0, 4, -4]);
    });
  });

  describe('hostile inputs', () => {
    it('rejects size 0, negative and non-integer values', () => {
      for (const size of [0, -5, 2.5, '1024', null]) {
        const data = {
          version: '1.0',
          terrain: { size, world_size: 256, max_height: 50 },
        };
        expect(() => parseTerrainData(data)).toThrow('"terrain.size"');
      }
    });

    it('rejects world_size 0 and non-finite values (JSON can carry 1e400 → Infinity)', () => {
      const hostile = JSON.parse(
        '{"version":"1.0","terrain":{"size":1024,"world_size":1e400,"max_height":50}}'
      );
      expect(() => parseTerrainData(hostile)).toThrow('"terrain.world_size"');
      const zero = {
        version: '1.0',
        terrain: { size: 1024, world_size: 0, max_height: 50 },
      };
      expect(() => parseTerrainData(zero)).toThrow('"terrain.world_size"');
    });

    it('rejects negative max_height and drops non-finite height stats', () => {
      const negative = {
        version: '1.0',
        terrain: { size: 1024, world_size: 256, max_height: -1 },
      };
      expect(() => parseTerrainData(negative)).toThrow('"terrain.max_height"');

      const stats = parseTerrainData({
        version: '1.0',
        terrain: {
          size: 1024,
          world_size: 256,
          max_height: 50,
          height_min: 'oops',
          height_max: Infinity,
          height_mean: 0.4,
        },
      });
      expect(stats.terrain.height_min).toBeUndefined();
      expect(stats.terrain.height_max).toBeUndefined();
      expect(stats.terrain.height_mean).toBe(0.4);
    });

    it('drops malformed rivers instead of feeding NaN to geometry', () => {
      const data = {
        version: '1.0',
        terrain: { size: 1024, world_size: 256, max_height: 50 },
        rivers: [
          42, // not an object
          { id: 1, path: [[0, 0]] }, // path too short
          {
            id: 2,
            path: [
              [0, 0],
              [NaN, 5],
              [10, 10],
            ],
          }, // NaN point dropped
          {
            id: 3,
            path: [
              [0, 0],
              [5, 'x'],
              [10, 10],
              [20, 20],
            ],
          }, // mixed junk
        ],
      };
      const parsed = parseTerrainData(data);
      // river 1 dropped (<2 points), river 2 keeps 2 finite points,
      // river 3 keeps 3 finite points.
      expect(parsed.rivers).toHaveLength(2);
      expect(parsed.rivers.map((r) => r.id)).toEqual([2, 3]);
      expect(parsed.rivers[0].path).toEqual([
        [0, 0],
        [10, 10],
      ]);
      expect(parsed.rivers[1].path).toEqual([
        [0, 0],
        [10, 10],
        [20, 20],
      ]);
    });

    it('drops lakes with missing required numerics and orphan lake_planes', () => {
      const data = {
        version: '1.0',
        terrain: { size: 1024, world_size: 256, max_height: 50 },
        lakes: [
          {
            id: 0,
            center_pixel: [1, 2],
            surface_level: 0.5,
            surface_height: 10,
          },
          {
            id: 1,
            center_pixel: 'nope',
            surface_level: 0.5,
            surface_height: 10,
          },
          {
            id: 2,
            center_pixel: [1, 2],
            surface_level: null,
            surface_height: 10,
          },
        ],
        lake_planes: [
          { lake_id: 0, pos_x: 1, pos_y: 2, pos_z: 3, size_x: 4, size_z: 5 },
          { lake_id: 99, pos_x: 1, pos_y: 2, pos_z: 3, size_x: 4, size_z: 5 },
          { lake_id: 0, pos_x: 1, pos_y: 2, pos_z: 3, size_x: -4, size_z: 5 },
        ],
      };
      const parsed = parseTerrainData(data);
      expect(parsed.lakes).toHaveLength(1);
      expect(parsed.lakes[0].id).toBe(0);
      expect(parsed.lake_planes).toHaveLength(1);
      expect(parsed.lake_planes[0].lake_id).toBe(0);
    });

    it('non-array rivers/lakes/lake_planes never reach the output', () => {
      const parsed = parseTerrainData({
        version: '1.0',
        terrain: { size: 1024, world_size: 256, max_height: 50 },
        rivers: 'many',
        lakes: { id: 0 },
        lake_planes: 7,
      });
      expect(parsed.rivers).toEqual([]);
      expect(parsed.lakes).toEqual([]);
      expect(parsed.lake_planes).toEqual([]);
    });
  });
});
