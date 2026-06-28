import { describe, expect, it } from 'bun:test';
import {
  CompositionPending,
  CompositionPlugin,
  buildPrimitiveMesh,
  compositionRecipe,
  isPrimitiveTag,
  type PrimitiveSpec,
} from 'vibegame/composition';

type StandardMat = {
  color: { r: number; g: number; b: number };
  side: number;
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
