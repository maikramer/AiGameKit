import { describe, expect, it } from 'bun:test';
import {
  getGroupSpawnDefaults,
  isKnownGroupProfileForTests,
  resolveGroupSpawnFields,
} from 'vibegame';

describe('place profile (entity place= + perfil place)', () => {
  it('place define align, escala fixa e declive permissivo', () => {
    const d = getGroupSpawnDefaults('place');
    expect(d.alignToTerrain).toBe(true);
    expect(d.groundAlign).toBe('aabb');
    expect(d.baseYOffset).toBe(0);
    expect(d.randomYaw).toBe(false);
    expect(d.scaleMin).toBe(1);
    expect(d.scaleMax).toBe(1);
    expect(d.maxSlopeDeg).toBe(90);
  });

  it('resolveGroupSpawnFields com perfil place e base-y-offset', () => {
    const r = resolveGroupSpawnFields({ 'base-y-offset': '0.6' }, 'place');
    expect(r.baseYOffset).toBe(0.6);
    expect(r.alignToTerrain).toBe(true);
  });

  it('perfil place é conhecido para testes', () => {
    expect(isKnownGroupProfileForTests('place')).toBe(true);
  });
});
