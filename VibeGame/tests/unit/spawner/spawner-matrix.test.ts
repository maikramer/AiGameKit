import { describe, expect, it } from 'bun:test';
import {
  applyChildTemplateProfile,
  getGroupSpawnDefaults,
  isKnownGroupProfileForTests,
  normalizeGroupProfileId,
  optBool,
  optNumber,
  parseSpaceSeparatedNumbers,
  resolveGroupSpawnFields,
  roleToProfile,
  yawAnglesFromStepDeg,
} from 'vibegame';
import { normalizeChildTemplateProfileId } from '../../../src/plugins/spawner/profiles';
import {
  parseAt,
  parseSemicolonPlaceString,
} from '../../../src/plugins/spawner/place-fields';
import {
  composeSpawnRotation,
  defaultTransformParts,
  formatTransformAttr,
  parseTransformAttr,
} from '../../../src/plugins/spawner/transform-merge';
import {
  partialAlignEuler,
  sinkOffsetForSlope,
  slopeAngleRad,
} from '../../../src/plugins/spawner/surface';
import * as THREE from 'three';

describe('spawner matrix: normalizeGroupProfileId', () => {
  const cases: Array<[string, string | undefined, string]> = [
    ['empty', '', 'none'],
    ['none literal', 'none', 'none'],
    ['tree', 'tree', 'tree'],
    ['uppercase', 'TREE', 'tree'],
    ['unknown', 'alien', 'none'],
  ];
  for (const [label, raw, expected] of cases) {
    it(`normalizeGroupProfileId ${label}`, () => {
      expect(normalizeGroupProfileId(raw)).toBe(expected);
    });
  }
});

describe('spawner matrix: roleToProfile', () => {
  const map: Array<[string, string | null]> = [
    ['tree', 'tree'],
    ['enemy', 'physics-box'],
    ['dynamic', 'physics-box'],
    ['pickup', 'physics-box'],
    ['npc', 'physics-box'],
    ['prop', 'gltf-crate'],
    ['static', 'gltf-crate'],
    ['visual', 'gltf-crate'],
    ['building', 'none'],
    ['unknown-role', null],
  ];
  for (const [role, profile] of map) {
    it(`roleToProfile maps ${role}`, () => {
      expect(roleToProfile(role)).toBe(profile);
    });
  }
});

describe('spawner matrix: known profiles', () => {
  for (const id of [
    'none',
    'tree',
    'foliage',
    'physics-box',
    'gltf-crate',
    'place',
  ]) {
    it(`profile ${id} is known`, () => {
      expect(isKnownGroupProfileForTests(id)).toBe(true);
    });
  }
  it('bogus profile is not known', () => {
    expect(isKnownGroupProfileForTests('bogus')).toBe(false);
  });
});

describe('spawner matrix: getGroupSpawnDefaults tree', () => {
  const d = getGroupSpawnDefaults('tree');
  it('tree alignToTerrain true', () => expect(d.alignToTerrain).toBe(true));
  it('tree groundAlign aabb', () => expect(d.groundAlign).toBe('aabb'));
  it('tree avoidWater true', () => expect(d.avoidWater).toBe(true));
  it('tree scaleMin 0.7', () => expect(d.scaleMin).toBe(0.7));
  it('tree scaleMax 1.4', () => expect(d.scaleMax).toBe(1.4));
});

describe('spawner matrix: optNumber/optBool', () => {
  const numCases: Array<[string, unknown, number, number]> = [
    ['undefined uses fallback', undefined, 5, 5],
    ['string parses', '3.5', 5, 3.5],
    ['boolean true', true, 0, 1],
    ['boolean false', false, 9, 0],
    ['invalid string keeps fallback', 'abc', 2, 2],
  ];
  for (const [label, attr, fb, exp] of numCases) {
    it(`optNumber ${label}`, () => {
      expect(optNumber(attr as never, fb)).toBe(exp);
    });
  }
  it('optBool undefined uses profile', () => {
    expect(optBool(undefined, true)).toBe(true);
  });
  it('optBool zero is false', () => {
    expect(optBool('0', true)).toBe(false);
  });
  it('optBool one is true', () => {
    expect(optBool('1', false)).toBe(true);
  });
});

describe('spawner matrix: yawAnglesFromStepDeg', () => {
  it('step 90 yields four angles', () => {
    expect(yawAnglesFromStepDeg(90)).toEqual([0, 90, 180, 270]);
  });
  it('step 45 yields eight angles', () => {
    expect(yawAnglesFromStepDeg(45).length).toBe(8);
  });
  it('invalid step returns empty', () => {
    expect(yawAnglesFromStepDeg(0)).toEqual([]);
    expect(yawAnglesFromStepDeg(400)).toEqual([]);
  });
});

describe('spawner matrix: parseSpaceSeparatedNumbers', () => {
  it('parses three floats', () => {
    expect(parseSpaceSeparatedNumbers('1 2 3')).toEqual([1, 2, 3]);
  });
  it('empty returns []', () => {
    expect(parseSpaceSeparatedNumbers('')).toEqual([]);
  });
  it('filters NaN tokens', () => {
    expect(parseSpaceSeparatedNumbers('1 x 3')).toEqual([1, 3]);
  });
});

describe('spawner matrix: parseAt', () => {
  it('parses string pair', () => {
    expect(parseAt('12 -4')).toEqual([12, -4]);
  });
  it('parses object x z', () => {
    expect(parseAt({ x: 1, z: 2 })).toEqual([1, 2]);
  });
  it('parses array', () => {
    expect(parseAt([5, 6])).toEqual([5, 6]);
  });
  it('single number uses zero z', () => {
    expect(parseAt(7)).toEqual([7, 0]);
  });
});

describe('spawner matrix: parseSemicolonPlaceString', () => {
  it('splits key value pairs', () => {
    const out = parseSemicolonPlaceString('at: 1 2; base-y-offset: 0.5');
    expect(out.at).toBe('1 2');
    expect(out['base-y-offset']).toBe('0.5');
  });
  it('normalizes keys to kebab-case', () => {
    const out = parseSemicolonPlaceString('Base Y Offset: 1');
    expect(out['base-y-offset']).toBe('1');
  });
});

describe('spawner matrix: transform merge', () => {
  it('defaultTransformParts are identity-ish', () => {
    const p = defaultTransformParts();
    expect(p.pos).toEqual([0, 0, 0]);
    expect(p.euler).toEqual([0, 0, 0]);
  });
  it('parseTransformAttr roundtrips format', () => {
    const raw = 'pos: 1 2 3; euler: 0 0 0';
    const parts = parseTransformAttr(raw);
    expect(formatTransformAttr(parts)).toContain('pos:');
  });
  it('composeSpawnRotation returns euler object', () => {
    const n = new THREE.Vector3(0, 1, 0);
    const out = composeSpawnRotation(n, false, 0, [0, 0, 0]);
    expect(typeof out.x).toBe('number');
  });
});

describe('spawner matrix: slope helpers', () => {
  it('flat normal has zero slope angle', () => {
    const n = new THREE.Vector3(0, 1, 0);
    expect(slopeAngleRad(n)).toBeCloseTo(0, 5);
  });
  it('vertical wall has large slope', () => {
    const n = new THREE.Vector3(1, 0, 0);
    expect(slopeAngleRad(n)).toBeGreaterThan(1.5);
  });
  it('sinkOffsetForSlope increases with angle', () => {
    const flat = sinkOffsetForSlope(0, 0.5);
    const steep = sinkOffsetForSlope(Math.PI / 4, 0.5);
    expect(steep).toBeGreaterThan(flat);
  });
  it('partialAlignEuler returns euler tuple', () => {
    const n = new THREE.Vector3(0, 1, 0);
    const e = partialAlignEuler(n, 0, 0);
    expect(e.length).toBe(3);
  });
});

describe('spawner matrix: child template profiles', () => {
  it('physics-crate fills dynamic-part defaults', () => {
    const attrs: Record<string, string> = {};
    applyChildTemplateProfile('dynamic-part', attrs, 'physics-crate');
    expect(attrs.shape).toBe('box');
    expect(attrs.mass).toBe(1.2);
  });
  it('gltf-crate fills gltfdynamic defaults', () => {
    const attrs: Record<string, string> = {};
    applyChildTemplateProfile('gltfdynamic', attrs, 'gltf-crate');
    expect(attrs.mass).toBe(1.5);
    expect(attrs['collider-shape']).toBe('box');
  });
  it('empty child profile is no-op', () => {
    expect(normalizeChildTemplateProfileId(undefined)).toBe('');
  });
});

describe('spawner matrix: resolveGroupSpawnFields overrides', () => {
  for (const align of ['0', '1', 'false', 'true']) {
    it(`align-to-terrain=${align} parses`, () => {
      const r = resolveGroupSpawnFields({ 'align-to-terrain': align }, 'tree');
      expect(typeof r.alignToTerrain).toBe('boolean');
    });
  }
  it('explicit scale-min overrides tree profile', () => {
    const r = resolveGroupSpawnFields({ 'scale-min': '2' }, 'tree');
    expect(r.scaleMin).toBe(2);
  });
});
