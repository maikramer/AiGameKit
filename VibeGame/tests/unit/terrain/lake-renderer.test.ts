import { describe, expect, it } from 'bun:test';
import {
  createLakeWaterEntities,
  createRiverWaterEntities,
} from '../../../src/plugins/terrain/lake-renderer';
import type { TerrainData } from '../../../src/plugins/terrain/terrain-data-loader';

const BASE_DATA: TerrainData = {
  version: '1.0',
  terrain: { size: 1024, world_size: 256.0, max_height: 50.0 },
  rivers: [],
  lakes: [],
  lake_planes: [],
};

describe('lake-renderer', () => {
  describe('createLakeWaterEntities', () => {
    it('returns empty string for empty lake_planes', () => {
      const result = createLakeWaterEntities(BASE_DATA);
      expect(result).toBe('');
    });

    it('returns empty string for undefined lake_planes', () => {
      const data = { ...BASE_DATA, lake_planes: undefined as unknown as [] };
      const result = createLakeWaterEntities(data);
      expect(result).toBe('');
    });

    it('generates a single Water entity for one lake plane', () => {
      const data: TerrainData = {
        ...BASE_DATA,
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

      const result = createLakeWaterEntities(data);

      expect(result).toBe(
        `<Water pos="50 25 75" size="12.5" water-level="25" size-x="12.5" size-z="7.5"></Water>`
      );
    });

    it('generates multiple Water entities separated by newlines', () => {
      const data: TerrainData = {
        ...BASE_DATA,
        lake_planes: [
          { lake_id: 0, pos_x: 10, pos_y: 5, pos_z: 20, size_x: 4, size_z: 3 },
          { lake_id: 1, pos_x: 30, pos_y: 8, pos_z: 40, size_x: 6, size_z: 9 },
        ],
      };

      const result = createLakeWaterEntities(data);

      expect(result).toContain(
        `<Water pos="10 5 20" size="4" water-level="5" size-x="4" size-z="3"></Water>`
      );
      expect(result).toContain(
        `<Water pos="30 8 40" size="9" water-level="8" size-x="6" size-z="9"></Water>`
      );
      expect(result.split('\n')).toHaveLength(2);
    });

    it('produces valid XML attribute format', () => {
      const data: TerrainData = {
        ...BASE_DATA,
        lake_planes: [
          {
            lake_id: 0,
            pos_x: 1.5,
            pos_y: 2.5,
            pos_z: 3.5,
            size_x: 4.5,
            size_z: 5.5,
          },
        ],
      };

      const result = createLakeWaterEntities(data);

      expect(result).toMatch(
        /^<Water pos="[^"]+" size="[^"]+" water-level="[^"]+" size-x="[^"]+" size-z="[^"]+"><\/Water>$/
      );
    });
  });

  describe('createRiverWaterEntities', () => {
    it('returns empty string for empty rivers', () => {
      const result = createRiverWaterEntities(BASE_DATA);
      expect(result).toBe('');
    });

    it('returns empty string for undefined rivers', () => {
      const data = { ...BASE_DATA, rivers: undefined as unknown as [] };
      const result = createRiverWaterEntities(data);
      expect(result).toBe('');
    });

    it('returns empty string for rivers with path length < 2', () => {
      const data: TerrainData = {
        ...BASE_DATA,
        rivers: [{ id: 0, source: [10, 20], path: [[10, 20]], length: 0 }],
      };

      const result = createRiverWaterEntities(data);
      expect(result).toBe('');
    });

    it('generates a Water entity at river source for valid river', () => {
      const data: TerrainData = {
        ...BASE_DATA,
        rivers: [
          {
            id: 0,
            source: [512, 0],
            path: [
              [512, 0],
              [511, 1],
            ],
            length: 10,
          },
        ],
      };

      const result = createRiverWaterEntities(data);

      expect(result).toBe(
        `<Water pos="512 0 0" size-x="2" size-z="2"></Water>`
      );
    });

    it('generates Water entities for multiple rivers', () => {
      const data: TerrainData = {
        ...BASE_DATA,
        rivers: [
          {
            id: 0,
            source: [100, 200],
            path: [
              [100, 200],
              [101, 201],
            ],
            length: 5,
          },
          {
            id: 1,
            source: [300, 400],
            path: [
              [300, 400],
              [301, 401],
            ],
            length: 8,
          },
        ],
      };

      const result = createRiverWaterEntities(data);

      expect(result).toContain(
        `<Water pos="100 0 200" size-x="2" size-z="2"></Water>`
      );
      expect(result).toContain(
        `<Water pos="300 0 400" size-x="2" size-z="2"></Water>`
      );
      expect(result.split('\n')).toHaveLength(2);
    });

    it('skips rivers with short paths but includes valid ones', () => {
      const data: TerrainData = {
        ...BASE_DATA,
        rivers: [
          { id: 0, source: [10, 20], path: [[10, 20]], length: 0 },
          {
            id: 1,
            source: [50, 60],
            path: [
              [50, 60],
              [51, 61],
            ],
            length: 3,
          },
        ],
      };

      const result = createRiverWaterEntities(data);

      expect(result).not.toContain('pos="10 0 20"');
      expect(result).toContain(
        `<Water pos="50 0 60" size-x="2" size-z="2"></Water>`
      );
    });
  });
});
