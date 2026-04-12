import { describe, expect, it, mock } from 'bun:test';
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
    it('logs lake planes without throwing', () => {
      const consoleSpy = mock(() => {});
      const originalLog = console.log;
      console.log = consoleSpy as typeof console.log;

      try {
        spawnWaterEntitiesFromTerrainData(VALID_TERRAIN_JSON as TerrainData);
        expect(consoleSpy).toHaveBeenCalled();

        const calls = consoleSpy.mock.calls;
        const lakeCall = calls.find((c: string[]) =>
          c.some((s: string) => s.includes('lake_id=0'))
        );
        expect(lakeCall).toBeDefined();
      } finally {
        console.log = originalLog;
      }
    });

    it('handles empty terrain data without error', () => {
      const data: TerrainData = {
        version: '1.0',
        terrain: { size: 256, world_size: 64.0, max_height: 20.0 },
        rivers: [],
        lakes: [],
        lake_planes: [],
      };

      expect(() => spawnWaterEntitiesFromTerrainData(data)).not.toThrow();
    });

    it('logs river info for rivers with paths', () => {
      const consoleSpy = mock(() => {});
      const originalLog = console.log;
      console.log = consoleSpy as typeof console.log;

      try {
        spawnWaterEntitiesFromTerrainData(VALID_TERRAIN_JSON as TerrainData);

        const calls = consoleSpy.mock.calls;
        const riverCall = calls.find((c: string[]) =>
          c.some((s: string) => s.includes('river id=0'))
        );
        expect(riverCall).toBeDefined();
      } finally {
        console.log = originalLog;
      }
    });
  });
});
