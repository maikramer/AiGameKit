import { describe, expect, it } from 'bun:test';
import {
  CompositionPending,
  CompositionPlugin,
  buildPrimitiveMesh,
  compositionRecipe,
  computePadAlphaData,
  isPrimitiveTag,
  parsePrimitiveSpec,
  type PrimitiveSpec,
} from 'vibegame/composition';

type StandardMat = {
  color: { r: number; g: number; b: number };
  side: number;
  roughness: number;
  metalness: number;
  map: unknown;
  normalMap: unknown;
  roughnessMap: unknown;
};

const FRONT_SIDE = 0;
const DOUBLE_SIDE = 2;

function makeSpec(overrides: Partial<PrimitiveSpec> = {}): PrimitiveSpec {
  return {
    kind: 'box',
    posX: 0,
    posY: 0,
    posZ: 0,
    rotX: 0,
    rotY: 0,
    rotZ: 0,
    sizeX: 1,
    sizeY: 1,
    sizeZ: 1,
    colorR: 0.8,
    colorG: 0.8,
    colorB: 0.8,
    textureUrl: null,
    textureRepeatX: 1,
    textureRepeatY: 1,
    textureRotation: 0,
    normalMapUrl: null,
    roughnessMapUrl: null,
    roughness: 1,
    metalness: 0,
    opacity: 1,
    edgeFeather: 0,
    cornerRadius: 0,
    edgeNoise: 0,
    ...overrides,
  };
}

describe('composition: isPrimitiveTag', () => {
  it('reconhece as primitivas canónicas (minúsculas)', () => {
    expect(isPrimitiveTag('box')).toBe(true);
    expect(isPrimitiveTag('sphere')).toBe(true);
    expect(isPrimitiveTag('cylinder')).toBe(true);
    expect(isPrimitiveTag('plane')).toBe(true);
  });

  it('é case-insensitive', () => {
    expect(isPrimitiveTag('Box')).toBe(true);
    expect(isPrimitiveTag('SPHERE')).toBe(true);
    expect(isPrimitiveTag('Cylinder')).toBe(true);
    expect(isPrimitiveTag('Plane')).toBe(true);
  });

  it('rejeita tags desconhecidas e vazias', () => {
    expect(isPrimitiveTag('cone')).toBe(false);
    expect(isPrimitiveTag('mesh')).toBe(false);
    expect(isPrimitiveTag('')).toBe(false);
    expect(isPrimitiveTag('boxy')).toBe(false);
  });
});

describe('composition: compositionRecipe', () => {
  it('expõe o nome e dependências declaradas', () => {
    expect(compositionRecipe.name).toBe('Composition');
    expect(compositionRecipe.components).toContain('transform');
    expect(compositionRecipe.components).toContain('compositionPending');
  });

  it('afirma posse dos filhos e dos atributos do parser', () => {
    expect(compositionRecipe.parserOwnsChildren).toBe(true);
    expect(compositionRecipe.parserAttributes).toEqual(
      expect.arrayContaining(['place', 'body', 'collider', 'collider-mode'])
    );
  });
});

describe('composition: CompositionPending', () => {
  it('usa Uint8Array para as flags de build em duas fases', () => {
    expect(CompositionPending.meshBuilt).toBeInstanceOf(Uint8Array);
    expect(CompositionPending.colliderBuilt).toBeInstanceOf(Uint8Array);
    expect(CompositionPending.meshBuilt.length).toBeGreaterThan(0);
    expect(CompositionPending.colliderBuilt.length).toBe(
      CompositionPending.meshBuilt.length
    );
  });

  it('inicia com as flags zeradas', () => {
    expect(CompositionPending.meshBuilt[0]).toBe(0);
    expect(CompositionPending.colliderBuilt[0]).toBe(0);
  });
});

describe('composition: CompositionPlugin', () => {
  it('registra recipe, componente e parser da Composition', () => {
    expect(CompositionPlugin.recipes).toContain(compositionRecipe);
    expect(CompositionPlugin.components?.compositionPending).toBe(
      CompositionPending
    );
    expect(CompositionPlugin.config?.parsers?.Composition).toBeTypeOf(
      'function'
    );
  });

  it('declara systems de setup, collider e sync', () => {
    const groups = (CompositionPlugin.systems ?? []).map((s) => s.group);
    expect(groups).toContain('setup');
    expect(groups).toContain('fixed');
    expect(groups).toContain('simulation');
  });
});

describe('composition: buildPrimitiveMesh', () => {
  it('posiciona e rotaciona o mesh conforme o spec do box', () => {
    const spec = makeSpec({
      posX: 5,
      posY: -2,
      posZ: 3.5,
      rotX: 0.1,
      rotY: 0.2,
      rotZ: 0.3,
    });
    const mesh = buildPrimitiveMesh(spec);
    expect(mesh.position.x).toBe(5);
    expect(mesh.position.y).toBe(-2);
    expect(mesh.position.z).toBe(3.5);
    expect(mesh.rotation.x).toBeCloseTo(0.1);
    expect(mesh.rotation.y).toBeCloseTo(0.2);
    expect(mesh.rotation.z).toBeCloseTo(0.3);
  });

  it('liga sombra por defeito', () => {
    const mesh = buildPrimitiveMesh(makeSpec());
    expect(mesh.castShadow).toBe(true);
    expect(mesh.receiveShadow).toBe(true);
  });

  it('mapeia cor RGB do spec para a cor do material', () => {
    const mesh = buildPrimitiveMesh(
      makeSpec({ colorR: 1, colorG: 0.5, colorB: 0.25 })
    );
    const mat = mesh.material as unknown as StandardMat;
    expect(mat.color.r).toBeCloseTo(1);
    expect(mat.color.g).toBeCloseTo(0.5);
    expect(mat.color.b).toBeCloseTo(0.25);
  });

  it('usa DoubleSide no plane e FrontSide no box', () => {
    const planeMesh = buildPrimitiveMesh(makeSpec({ kind: 'plane' }));
    const planeMat = planeMesh.material as unknown as StandardMat;
    expect(planeMat.side).toBe(DOUBLE_SIDE);

    const boxMesh = buildPrimitiveMesh(makeSpec({ kind: 'box' }));
    const boxMat = boxMesh.material as unknown as StandardMat;
    expect(boxMat.side).toBe(FRONT_SIDE);
  });

  it('produz geometria distinta por kind', () => {
    const boxGeo = buildPrimitiveMesh(makeSpec({ kind: 'box' })).geometry;
    const sphereGeo = buildPrimitiveMesh(
      makeSpec({ kind: 'sphere', sizeX: 2 })
    ).geometry;
    const planeGeo = buildPrimitiveMesh(makeSpec({ kind: 'plane' })).geometry;
    expect(boxGeo.type).toBe('BoxGeometry');
    expect(sphereGeo.type).toBe('SphereGeometry');
    expect(planeGeo.type).toBe('PlaneGeometry');
  });
});

describe('composition: parsePrimitiveSpec (texturas)', () => {
  it('sem atributos de textura devolve textureUrl null e defaults PBR', () => {
    const spec = parsePrimitiveSpec('box', { pos: '0 0 0', size: '1 1 1' });
    expect(spec.textureUrl).toBeNull();
    expect(spec.normalMapUrl).toBeNull();
    expect(spec.roughnessMapUrl).toBeNull();
    expect(spec.textureRepeatX).toBe(1);
    expect(spec.textureRepeatY).toBe(1);
    expect(spec.textureRotation).toBe(0);
    expect(spec.roughness).toBe(1);
    expect(spec.metalness).toBe(0);
  });

  it('parseia texture-url e texture-repeat "2 1"', () => {
    const spec = parsePrimitiveSpec('box', {
      'texture-url': '/assets/textures/wall.png',
      'texture-repeat': '2 1',
    });
    expect(spec.textureUrl).toBe('/assets/textures/wall.png');
    expect(spec.textureRepeatX).toBe(2);
    expect(spec.textureRepeatY).toBe(1);
  });

  it('aceita alias map-url e repeat numérico único', () => {
    const spec = parsePrimitiveSpec('plane', {
      'map-url': '/assets/textures/wood.png',
      'texture-repeat': '3',
    });
    expect(spec.textureUrl).toBe('/assets/textures/wood.png');
    expect(spec.textureRepeatX).toBe(3);
    expect(spec.textureRepeatY).toBe(3);
  });

  it('parseia normal-map-url e roughness-map-url', () => {
    const spec = parsePrimitiveSpec('box', {
      'normal-map-url': '/assets/textures/wall_normal.png',
      'roughness-map-url': '/assets/textures/wall_rough.png',
    });
    expect(spec.normalMapUrl).toBe('/assets/textures/wall_normal.png');
    expect(spec.roughnessMapUrl).toBe('/assets/textures/wall_rough.png');
  });

  it('clampa roughness e metalness a [0,1]', () => {
    const spec = parsePrimitiveSpec('box', {
      roughness: '1.5',
      metalness: '-0.2',
    });
    expect(spec.roughness).toBe(1);
    expect(spec.metalness).toBe(0);
  });
});

describe('composition: primitiva pad (decal de chão)', () => {
  it('isPrimitiveTag reconhece pad', () => {
    expect(isPrimitiveTag('pad')).toBe(true);
    expect(isPrimitiveTag('Pad')).toBe(true);
  });

  it('parseia size="W D" (2 componentes) como largura X × profundidade Z', () => {
    const spec = parsePrimitiveSpec('pad', { size: '16 12' });
    expect(spec.sizeX).toBe(16);
    expect(spec.sizeZ).toBe(12);
  });

  it('aceita size 2D já convertido pelo XMLValueParser ({x, y})', () => {
    const spec = parsePrimitiveSpec('pad', {
      size: { x: 16, y: 12 } as unknown as string,
    });
    expect(spec.sizeX).toBe(16);
    expect(spec.sizeZ).toBe(12);
  });

  it('pad tem edge-feather default 0.8; box default 0', () => {
    const pad = parsePrimitiveSpec('pad', {});
    expect(pad.edgeFeather).toBeCloseTo(0.8);
    const box = parsePrimitiveSpec('box', {});
    expect(box.edgeFeather).toBe(0);
  });

  it('parseia edge-feather, corner-radius e edge-noise', () => {
    const spec = parsePrimitiveSpec('pad', {
      'edge-feather': '1.5',
      'corner-radius': '3',
      'edge-noise': '0.4',
    });
    expect(spec.edgeFeather).toBeCloseTo(1.5);
    expect(spec.cornerRadius).toBeCloseTo(3);
    expect(spec.edgeNoise).toBeCloseTo(0.4);
  });

  it('mesh do pad: geometria plana, sem castShadow, material blendável', () => {
    const mesh = buildPrimitiveMesh(
      makeSpec({ kind: 'pad', sizeX: 8, sizeZ: 8, edgeFeather: 1 })
    );
    expect(mesh.geometry.type).toBe('PlaneGeometry');
    expect(mesh.castShadow).toBe(false);
    expect(mesh.receiveShadow).toBe(true);
    const mat = mesh.material as unknown as {
      transparent: boolean;
      depthWrite: boolean;
      alphaMap: unknown;
      polygonOffset: boolean;
    };
    expect(mat.transparent).toBe(true);
    expect(mat.depthWrite).toBe(false);
    expect(mat.alphaMap).not.toBeNull();
    expect(mat.polygonOffset).toBe(true);
  });

  it('pad com edge-feather=0 e sem noise/radius fica opaco (sem alphaMap)', () => {
    const mesh = buildPrimitiveMesh(makeSpec({ kind: 'pad' }));
    const mat = mesh.material as unknown as {
      transparent: boolean;
      alphaMap: unknown;
    };
    expect(mat.transparent).toBe(false);
    expect(mat.alphaMap).toBeNull();
  });

  it('computePadAlphaData: centro opaco, orla transparente', () => {
    const spec = makeSpec({
      kind: 'pad',
      sizeX: 10,
      sizeZ: 10,
      edgeFeather: 1.5,
    });
    const w = 64;
    const h = 64;
    const data = computePadAlphaData(spec, w, h);
    const center = data[(h / 2) * w + w / 2]!;
    expect(center).toBe(255);
    // 4 orlas: alpha ~0 (primeiro/último texel dentro da banda de feather)
    expect(data[0]!).toBeLessThan(30);
    expect(data[w - 1]!).toBeLessThan(30);
    expect(data[(h - 1) * w]!).toBeLessThan(30);
    expect(data[h * w - 1]!).toBeLessThan(30);
  });

  it('computePadAlphaData: corner-radius esvazia os cantos mais que os lados', () => {
    const spec = makeSpec({
      kind: 'pad',
      sizeX: 10,
      sizeZ: 10,
      edgeFeather: 1,
      cornerRadius: 3,
    });
    const w = 64;
    const h = 64;
    const data = computePadAlphaData(spec, w, h);
    // Ponto a ~1 m da orla no meio do lado (dentro do núcleo) vs canto
    const edgeMid = data[(h / 2) * w + 6]!; // x≈-4.0, z=0 → dentro
    const corner = data[6 * w + 6]!; // x≈-4.0, z≈-4.0 → cortado pelo raio
    expect(edgeMid).toBeGreaterThan(200);
    expect(corner).toBeLessThan(edgeMid);
  });

  it('computePadAlphaData é determinístico para o mesmo spec', () => {
    const spec = makeSpec({
      kind: 'pad',
      sizeX: 6,
      sizeZ: 6,
      edgeFeather: 1,
      edgeNoise: 0.5,
      posX: 3,
      posZ: -7,
    });
    const a = computePadAlphaData(spec, 32, 32);
    const b = computePadAlphaData(spec, 32, 32);
    expect(Array.from(a)).toEqual(Array.from(b));
  });

  it('edge-noise nunca torna a orla da geometria visível (alpha 0 mantém-se)', () => {
    const spec = makeSpec({
      kind: 'pad',
      sizeX: 8,
      sizeZ: 8,
      edgeFeather: 0.6,
      edgeNoise: 1.2,
    });
    const w = 48;
    const h = 48;
    const data = computePadAlphaData(spec, w, h);
    for (let i = 0; i < w; i++) {
      expect(data[i]!).toBeLessThan(40); // primeira linha
      expect(data[(h - 1) * w + i]!).toBeLessThan(40); // última linha
    }
  });
});

describe('composition: buildPrimitiveMesh (texturas)', () => {
  it('sem textureUrl mantém material sem map (retrocompatibilidade)', () => {
    const mesh = buildPrimitiveMesh(makeSpec());
    const mat = mesh.material as unknown as StandardMat;
    expect(mat.map).toBeNull();
    expect(mat.normalMap).toBeNull();
    expect(mat.roughnessMap).toBeNull();
  });

  it('respeita roughness e metalness do spec', () => {
    const mesh = buildPrimitiveMesh(
      makeSpec({ roughness: 0.3, metalness: 0.8 })
    );
    const mat = mesh.material as unknown as StandardMat;
    expect(mat.roughness).toBeCloseTo(0.3);
    expect(mat.metalness).toBeCloseTo(0.8);
  });
});
