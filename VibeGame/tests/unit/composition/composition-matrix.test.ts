import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  buildPrimitiveMesh,
  computePadAlphaData,
  isPrimitiveTag,
  parsePrimitiveSpec,
  type PrimitiveSpec,
} from 'vibegame/composition';
import {
  deleteCompositionGroup,
  forEachCompositionGroup,
  getCompositionGroup,
  registerCompositionGroup,
} from '../../../src/plugins/composition/group-registry';
import {
  deleteCompositionData,
  getCompositionData,
  parseColorHex,
  setCompositionData,
} from '../../../src/plugins/composition/primitives';
import { Group } from 'three';
import type { XMLValue } from '../../../src/core/xml/types';

function padSpec(overrides: Partial<PrimitiveSpec> = {}): PrimitiveSpec {
  return parsePrimitiveSpec('pad', {
    size: '4 6',
    ...(overrides as Record<string, XMLValue>),
  });
}

describe('composition matrix: parseColorHex', () => {
  const cases: Array<{
    label: string;
    input: Parameters<typeof parseColorHex>[0];
    r: number;
    g: number;
    b: number;
  }> = [
    { label: 'black hex', input: '#000000', r: 0, g: 0, b: 0 },
    { label: 'white hex', input: '#ffffff', r: 1, g: 1, b: 1 },
    { label: 'red hex', input: '#ff0000', r: 1, g: 0, b: 0 },
    { label: 'green hex', input: '#00ff00', r: 0, g: 1, b: 0 },
    { label: 'blue hex', input: '#0000ff', r: 0, g: 0, b: 1 },
    { label: 'short hex f00', input: '#f00', r: 1, g: 0, b: 0 },
    { label: 'short hex 0f0', input: '#0f0', r: 0, g: 1, b: 0 },
    { label: 'short hex 00f', input: '#00f', r: 0, g: 0, b: 1 },
    {
      label: 'no hash',
      input: '808080',
      r: 128 / 255,
      g: 128 / 255,
      b: 128 / 255,
    },
    { label: 'numeric red', input: 0xff0000, r: 1, g: 0, b: 0 },
    { label: 'numeric blue', input: 255, r: 0, g: 0, b: 255 / 255 },
    { label: 'empty string default gray', input: '', r: 0.8, g: 0.8, b: 0.8 },
    {
      label: 'undefined default gray',
      input: undefined,
      r: 0.8,
      g: 0.8,
      b: 0.8,
    },
    {
      label: 'invalid hex default gray',
      input: 'not-a-color',
      r: 0.8,
      g: 0.8,
      b: 0.8,
    },
    {
      label: 'brown #6b4a2b',
      input: '#6b4a2b',
      r: 0x6b / 255,
      g: 0x4a / 255,
      b: 0x2b / 255,
    },
  ];

  for (const c of cases) {
    it(`parseColorHex: ${c.label}`, () => {
      const [r, g, b] = parseColorHex(c.input);
      expect(r).toBeCloseTo(c.r, 5);
      expect(g).toBeCloseTo(c.g, 5);
      expect(b).toBeCloseTo(c.b, 5);
    });
  }
});

describe('composition matrix: isPrimitiveTag', () => {
  const valid = ['box', 'sphere', 'cylinder', 'plane', 'pad', 'BOX', 'Pad'];
  const invalid = ['cone', 'mesh', 'capsule', '', 'padx', 'group'];

  for (const tag of valid) {
    it(`accepts primitive tag ${tag}`, () => {
      expect(isPrimitiveTag(tag)).toBe(true);
    });
  }
  for (const tag of invalid) {
    it(`rejects non-primitive tag ${tag || '(empty)'}`, () => {
      expect(isPrimitiveTag(tag)).toBe(false);
    });
  }
});

describe('composition matrix: parsePrimitiveSpec sizes', () => {
  it('box default size is 1 cube', () => {
    const s = parsePrimitiveSpec('box', {});
    expect(s.sizeX).toBe(1);
    expect(s.sizeY).toBe(1);
    expect(s.sizeZ).toBe(1);
  });

  it('pad two-component size maps to X and Z', () => {
    const s = parsePrimitiveSpec('pad', { size: '12 8' });
    expect(s.sizeX).toBe(12);
    expect(s.sizeY).toBe(1);
    expect(s.sizeZ).toBe(8);
  });

  it('pad object size without z uses x y as width depth', () => {
    const s = parsePrimitiveSpec('pad', {
      size: { x: 5, y: 7 } as unknown as string,
    });
    expect(s.sizeX).toBe(5);
    expect(s.sizeZ).toBe(7);
  });

  it('texture-scale on pad derives repeat from world size', () => {
    const s = parsePrimitiveSpec('pad', {
      size: '8 4',
      'texture-scale': 2,
    });
    expect(s.textureRepeatX).toBeCloseTo(4, 5);
    expect(s.textureRepeatY).toBeCloseTo(2, 5);
  });

  it('clamps roughness and metalness to 0..1', () => {
    const low = parsePrimitiveSpec('box', { roughness: -1, metalness: 2 });
    expect(low.roughness).toBe(0);
    expect(low.metalness).toBe(1);
  });

  it('opacity clamps to unit interval', () => {
    expect(parsePrimitiveSpec('plane', { opacity: 1.5 }).opacity).toBe(1);
    expect(parsePrimitiveSpec('plane', { opacity: -0.2 }).opacity).toBe(0);
  });

  it('map-url alias populates textureUrl', () => {
    const s = parsePrimitiveSpec('box', { 'map-url': '/tex.png' });
    expect(s.textureUrl).toBe('/tex.png');
  });

  it('pad default edgeFeather is 0.8 when omitted', () => {
    expect(parsePrimitiveSpec('pad', {}).edgeFeather).toBeCloseTo(0.8, 5);
  });

  it('box default edgeFeather is 0 when omitted', () => {
    expect(parsePrimitiveSpec('box', {}).edgeFeather).toBe(0);
  });
});

describe('composition matrix: composition data registry', () => {
  it('set/get/delete composition data per entity', () => {
    const state = new State();
    const eid = state.createEntity();
    expect(getCompositionData(state, eid)).toBeUndefined();
    setCompositionData(state, eid, { specs: [], colliderMode: 'auto' });
    expect(getCompositionData(state, eid)?.colliderMode).toBe('auto');
    deleteCompositionData(state, eid);
    expect(getCompositionData(state, eid)).toBeUndefined();
  });
});

describe('composition matrix: group registry', () => {
  it('register and retrieve composition group', () => {
    const state = new State();
    const eid = state.createEntity();
    const group = new Group();
    registerCompositionGroup(state, eid, group);
    expect(getCompositionGroup(state, eid)).toBe(group);
    deleteCompositionGroup(state, eid);
    expect(getCompositionGroup(state, eid)).toBeUndefined();
  });

  it('forEachCompositionGroup visits all entries', () => {
    const state = new State();
    const a = state.createEntity();
    const b = state.createEntity();
    registerCompositionGroup(state, a, new Group());
    registerCompositionGroup(state, b, new Group());
    const seen: number[] = [];
    forEachCompositionGroup(state, (eid) => seen.push(eid));
    expect(seen.sort()).toEqual([a, b].sort());
  });
});

describe('composition matrix: buildPrimitiveMesh kinds', () => {
  const kinds = ['box', 'sphere', 'cylinder', 'plane', 'pad'] as const;
  for (const kind of kinds) {
    it(`buildPrimitiveMesh creates geometry for ${kind}`, () => {
      const mesh = buildPrimitiveMesh(
        parsePrimitiveSpec(
          kind,
          kind === 'pad' ? { size: '2 3' } : { size: '1 1 1' }
        )
      );
      expect(mesh.geometry.attributes.position.count).toBeGreaterThan(0);
    });
  }
});

describe('composition matrix: computePadAlphaData center opaque', () => {
  const sizes: Array<[number, number]> = [
    [4, 4],
    [6, 8],
    [10, 10],
    [16, 12],
  ];
  for (const [sx, sz] of sizes) {
    it(`pad ${sx}x${sz} center pixel is near opaque`, () => {
      const spec = padSpec({
        sizeX: sx,
        sizeZ: sz,
        edgeFeather: 0.5,
        edgeNoise: 0,
      });
      const w = 32;
      const h = 32;
      const data = computePadAlphaData(spec, w, h);
      const center = data[(h / 2) * w + w / 2]!;
      expect(center).toBeGreaterThan(200);
    });
  }
});

describe('composition matrix: pad alpha edge fade', () => {
  for (let feather = 0.2; feather <= 1.0; feather += 0.2) {
    it(`edge pixels fade with feather=${feather.toFixed(1)}`, () => {
      const spec = padSpec({
        sizeX: 8,
        sizeZ: 8,
        edgeFeather: feather,
        edgeNoise: 0,
      });
      const data = computePadAlphaData(spec, 48, 48);
      const center = data[24 * 48 + 24]!;
      const corner = data[2 * 48 + 2]!;
      expect(center).toBeGreaterThan(corner);
    });
  }
});

describe('composition matrix: pad determinism', () => {
  for (const seed of [0, 1, 2, 3, 4]) {
    it(`identical alpha for repeated calls (noise=${seed})`, () => {
      const spec = padSpec({
        sizeX: 6,
        sizeZ: 6,
        edgeFeather: 0.8,
        edgeNoise: 0.3 + seed * 0.1,
        posX: seed,
        posZ: -seed,
      });
      const a = computePadAlphaData(spec, 24, 24);
      const b = computePadAlphaData(spec, 24, 24);
      expect(Array.from(a)).toEqual(Array.from(b));
    });
  }
});
