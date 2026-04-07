import { describe, expect, it } from 'bun:test';
import {
  applyChildTemplateProfile,
  getGroupSpawnDefaults,
  optBool,
  optNumber,
  resolveGroupSpawnFields,
} from 'vibegame';

describe('spawn profiles', () => {
  it('tree define align, offset e escala', () => {
    const d = getGroupSpawnDefaults('tree');
    expect(d.alignToTerrain).toBe(true);
    expect(d.groundAlign).toBe('aabb');
    expect(d.baseYOffset).toBe(0.02);
    expect(d.randomYaw).toBe(true);
    expect(d.scaleMin).toBe(1.6);
    expect(d.scaleMax).toBe(2.2);
  });

  it('resolveGroupSpawnFields usa perfil quando attrs ausentes', () => {
    const r = resolveGroupSpawnFields({}, 'tree');
    expect(r.alignToTerrain).toBe(true);
    expect(r.groundAlign).toBe('aabb');
    expect(r.baseYOffset).toBe(0.02);
    expect(r.scaleMin).toBe(1.6);
    expect(r.maxSlopeDeg).toBe(45);
    expect(r.maxSlopePlacementAttempts).toBe(32);
  });

  it('resolveGroupSpawnFields XML explícito sobrescreve perfil', () => {
    const r = resolveGroupSpawnFields(
      {
        'align-to-terrain': '0',
        'base-y-offset': '2',
        'scale-min': '1',
        'scale-max': '1',
      },
      'tree'
    );
    expect(r.alignToTerrain).toBe(false);
    expect(r.baseYOffset).toBe(2);
    expect(r.scaleMin).toBe(1);
    expect(r.scaleMax).toBe(1);
  });

  it('optNumber/optBool respeitam ausência vs presença', () => {
    expect(optNumber(undefined, 7)).toBe(7);
    expect(optNumber('3', 7)).toBe(3);
    expect(optBool(undefined, true)).toBe(true);
    expect(optBool('0', true)).toBe(false);
  });

  it('applyChildTemplateProfile physics-crate em dynamic-part', () => {
    const attrs: Record<string, string | number> = {};
    applyChildTemplateProfile('dynamic-part', attrs, 'physics-crate');
    expect(attrs.shape).toBe('box');
    expect(attrs.size).toBe('0.85 0.85 0.85');
    expect(attrs.color).toBe('#8b6914');
    expect(attrs.mass).toBe(1.2);
    expect(attrs.restitution).toBe(0.15);
  });

  it('applyChildTemplateProfile não sobrescreve attrs existentes', () => {
    const attrs: Record<string, string | number> = { mass: 9 };
    applyChildTemplateProfile('dynamic-part', attrs, 'physics-crate');
    expect(attrs.mass).toBe(9);
  });

  it('applyChildTemplateProfile gltf-crate em gltf-dynamic', () => {
    const attrs: Record<string, string | number> = {};
    applyChildTemplateProfile('gltf-dynamic', attrs, 'gltf-crate');
    expect(attrs.mass).toBe(1.5);
    expect(attrs.friction).toBe(0.55);
    expect(attrs['collider-margin']).toBe(0.02);
  });
});
