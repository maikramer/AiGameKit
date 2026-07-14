import { describe, expect, it } from 'bun:test';
import { TerrainLodSelectSystem } from 'vibegame/terrain';

describe('TerrainLodSelectSystem', () => {
  it('is a draw-group system', () => {
    expect(TerrainLodSelectSystem).toBeDefined();
    expect(TerrainLodSelectSystem.group).toBe('draw');
    expect(typeof TerrainLodSelectSystem.update).toBe('function');
  });
});
