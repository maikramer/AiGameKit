import { describe, expect, it } from 'bun:test';
import {
  Road,
  RoadPlugin,
  densifyPathByHeight,
  detectRoadJunctions,
  distanceToPolyline,
  extendPathEnds,
  makeJunctionGeometry,
  makeRoadGeometry,
  makeWidthAtFromVertexWidths,
  maxNeighborhoodHeight,
  resampleRoadPath,
  roadRecipe,
  smoothPath,
  stitchEndToEndChains,
  type RoadGeometryOptions,
  type RoadJunctionInput,
} from 'vibegame/road';

function opts(
  overrides: Partial<RoadGeometryOptions> = {}
): RoadGeometryOptions {
  return {
    width: 5,
    textureScale: 16,
    edgeFeather: 1,
    edgeNoise: 0,
    endFeatherStart: 0,
    endFeatherEnd: 0,
    ...overrides,
  };
}

describe('road: smoothPath (Chaikin)', () => {
  it('preserva os extremos e corta cantos', () => {
    const path = [0, 0, 10, 0, 10, 10];
    const out = smoothPath(path, 2);
    expect(out[0]).toBe(0);
    expect(out[1]).toBe(0);
    expect(out[out.length - 2]).toBe(10);
    expect(out[out.length - 1]).toBe(10);
    // O canto autorado (10,0) é cortado — nenhum ponto interior coincide.
    let hasCorner = false;
    for (let i = 2; i + 3 < out.length; i += 2) {
      if (out[i] === 10 && out[i + 1] === 0) hasCorner = true;
    }
    expect(hasCorner).toBe(false);
  });

  it('smoothing=0 devolve o path original', () => {
    const path = [0, 0, 5, 5];
    expect(smoothPath(path, 0)).toEqual(path);
  });

  it('cada iteração aumenta o número de pontos', () => {
    const path = [0, 0, 10, 0, 20, 10, 30, 10];
    const once = smoothPath(path, 1);
    const twice = smoothPath(path, 2);
    expect(once.length).toBeGreaterThan(path.length);
    expect(twice.length).toBeGreaterThan(once.length);
  });
});

describe('road: resampleRoadPath', () => {
  it('gera estações a ~spacing metros', () => {
    const out = resampleRoadPath([0, 0, 10, 0], 1);
    expect(out.length / 2).toBe(11); // 0..10 de 1 em 1
    expect(out[0]).toBe(0);
    expect(out[out.length - 2]).toBe(10);
  });
});

describe('road: makeRoadGeometry', () => {
  it('4 vértices por estação, indexado, com UV e color RGBA', () => {
    const path = resampleRoadPath([0, 0, 10, 0], 2);
    const geo = makeRoadGeometry(path, opts());
    const stations = path.length / 2;
    expect(geo.getAttribute('position').count).toBe(stations * 4);
    expect(geo.getAttribute('uv').count).toBe(stations * 4);
    expect(geo.getAttribute('color').itemSize).toBe(4);
    expect(geo.getIndex()!.count).toBe((stations - 1) * 3 * 6);
  });

  it('alpha: bordas 0, núcleo 1 (sem end-feather)', () => {
    const path = resampleRoadPath([0, 0, 10, 0], 2);
    const geo = makeRoadGeometry(path, opts());
    const c = geo.getAttribute('color');
    // Estação do meio: lanes [borda, núcleo, núcleo, borda]
    const s = 2 * 4;
    expect(c.getW(s)).toBe(0);
    expect(c.getW(s + 1)).toBe(1);
    expect(c.getW(s + 2)).toBe(1);
    expect(c.getW(s + 3)).toBe(0);
  });

  it('end-feather: núcleo 0 na ponta com feather, 1 na ponta sólida', () => {
    const path = resampleRoadPath([0, 0, 20, 0], 2);
    const geo = makeRoadGeometry(
      path,
      opts({ endFeatherStart: 0, endFeatherEnd: 4 })
    );
    const c = geo.getAttribute('color');
    const last = (path.length / 2 - 1) * 4;
    expect(c.getW(0 + 1)).toBe(1); // início sólido (enterrado)
    expect(c.getW(last + 1)).toBe(0); // fim desvanece a 0
  });

  it('UV.v cresce com o arc-length e UV.u cobre a largura', () => {
    const path = resampleRoadPath([0, 0, 16, 0], 4);
    const geo = makeRoadGeometry(path, opts({ width: 8, textureScale: 16 }));
    const uv = geo.getAttribute('uv');
    // Última estação: v = 16/16 = 1
    const last = (path.length / 2 - 1) * 4;
    expect(uv.getY(last)).toBeCloseTo(1);
    expect(uv.getY(0)).toBeCloseTo(0);
    // u da borda esquerda ~0, da direita ~width/scale
    expect(uv.getX(0)).toBeCloseTo(0, 1);
    expect(uv.getX(3)).toBeCloseTo(0.5, 1);
  });

  it('flat ribbon normals point +Y (not into the ground)', () => {
    const path = resampleRoadPath([0, 0, 10, 0], 2);
    const geo = makeRoadGeometry(path, opts());
    const n = geo.getAttribute('normal');
    for (let i = 0; i < n.count; i++) {
      expect(n.getY(i)).toBeGreaterThan(0.9);
    }
  });

  it('junction disc normals point +Y (not into the ground)', () => {
    const geo = makeJunctionGeometry(0, 0, {
      radius: 4,
      feather: 1,
      textureScale: 16,
      heightAt: () => 0,
    });
    const n = geo.getAttribute('normal');
    expect(n.count).toBeGreaterThan(10);
    for (let i = 0; i < n.count; i++) {
      expect(n.getY(i)).toBeGreaterThan(0.9);
    }
  });

  it('segue heightAt + yOffset', () => {
    const path = resampleRoadPath([0, 0, 10, 0], 5);
    const geo = makeRoadGeometry(
      path,
      opts({ heightAt: (x) => x * 2, yOffset: 0.1 })
    );
    const p = geo.getAttribute('position');
    // Vértice de núcleo da última estação: x=10 → y = 20 + 0.1
    const last = (path.length / 2 - 1) * 4 + 1;
    expect(p.getY(last)).toBeCloseTo(10 * 2 + 0.1);
  });

  it('cross-section uses centerline Y (ignores lateral height spikes)', () => {
    const path = resampleRoadPath([0, 0, 10, 0], 5);
    const geo = makeRoadGeometry(
      path,
      opts({
        width: 6,
        // Dune walls beside the road — must NOT lift ribbon edges.
        heightAt: (_x, z) => (Math.abs(z) > 1 ? 40 : 2),
      })
    );
    const p = geo.getAttribute('position');
    for (let i = 0; i < p.count; i++) {
      expect(p.getY(i)).toBeCloseTo(2, 5);
    }
  });

  it('edge-noise corrói para dentro e é determinístico', () => {
    const path = resampleRoadPath([0, 0, 30, 0], 1.5);
    const a = makeRoadGeometry(path, opts({ edgeNoise: 0.5, width: 6 }));
    const b = makeRoadGeometry(path, opts({ edgeNoise: 0.5, width: 6 }));
    const pa = a.getAttribute('position');
    const pb = b.getAttribute('position');
    expect(pa.array).toEqual(pb.array);
    // Borda esquerda (lane 0): |z| ≤ half e < half em pelo menos uma estação
    // (path ao longo de X → lateral é Z).
    let eroded = false;
    for (let i = 0; i < pa.count; i += 4) {
      const z = Math.abs(pa.getZ(i));
      expect(z).toBeLessThanOrEqual(3 + 1e-6);
      if (z < 3 - 0.05) eroded = true;
    }
    expect(eroded).toBe(true);
  });

  it('curva: estações seguem o path suavizado (miter sem gaps)', () => {
    const smoothed = resampleRoadPath(
      smoothPath([0, 0, 20, 0, 20, 20], 2),
      1.5
    );
    const geo = makeRoadGeometry(smoothed, opts({ width: 4 }));
    // Sem NaN e bounding box cobre ambos os braços da curva
    geo.computeBoundingBox();
    const bb = geo.boundingBox!;
    expect(Number.isFinite(bb.min.x)).toBe(true);
    expect(bb.max.x).toBeGreaterThan(18);
    expect(bb.max.z).toBeGreaterThan(18);
  });

  it('curva mantém a largura perpendicular (miter join, sem pinch)', () => {
    // Hard 90° corner: without the miter scale the bisector offset narrows the
    // ribbon to width·cos(45°) ≈ 0.71 there, which reads as a broken road.
    const path = [0, 0, 10, 0, 10, 10];
    const geo = makeRoadGeometry(resampleRoadPath(path, 1), opts({ width: 6 }));
    const pos = geo.getAttribute('position');
    let narrowest = Infinity;
    for (let i = 0; i + 3 < pos.count; i += 4) {
      const dx = pos.getX(i + 3) - pos.getX(i);
      const dz = pos.getZ(i + 3) - pos.getZ(i);
      narrowest = Math.min(narrowest, Math.hypot(dx, dz));
    }
    expect(narrowest).toBeGreaterThan(6 - 1e-6);
  });
});

describe('road: distanceToPolyline / extendPathEnds', () => {
  it('mede a distância ao segmento mais próximo, não aos nós', () => {
    const path = [0, 0, 10, 0];
    expect(distanceToPolyline(path, 5, 3)).toBeCloseTo(3, 6);
    expect(distanceToPolyline(path, -4, 0)).toBeCloseTo(4, 6);
  });

  it('prolonga as pontas ao longo das tangentes dos extremos', () => {
    const path = [0, 0, 10, 0, 10, 10];
    const out = extendPathEnds(path, 2, 3);
    expect(out.slice(0, 2)).toEqual([-2, 0]);
    expect(out.slice(-2)).toEqual([10, 13]);
    expect(extendPathEnds(path, 0, 0)).toEqual(path);
  });
});

describe('road: maxNeighborhoodHeight', () => {
  it('picks the crest in the neighborhood, not the center', () => {
    const sample = (x: number, z: number) => x + z * 0.1;
    expect(maxNeighborhoodHeight(sample, 0, 0, 2)).toBeCloseTo(2, 5);
  });
});

describe('road: fusion (junctions)', () => {
  const cityEast: RoadJunctionInput = {
    eid: 1,
    path: [3, 0, 32, -1, 52, 1, 62, 1],
    width: 5.4,
    edgeFeather: 0.45,
    textureUrl: '/cobble.png',
    normalMapUrl: null,
    textureScale: 16,
  };
  const desertArtery: RoadJunctionInput = {
    eid: 2,
    path: [62, 1, 70, 5, 88, 8, 145, -6],
    width: 4.2,
    edgeFeather: 0.45,
    textureUrl: '/cobble.png',
    normalMapUrl: null,
    textureScale: 14,
  };

  it('detecta end-to-end cidade↔deserto', () => {
    const junc = detectRoadJunctions([cityEast, desertArtery]);
    expect(junc.length).toBe(1);
    expect(junc[0]!.maxWidth).toBeCloseTo(5.4, 5);
    expect(junc[0]!.arms).toHaveLength(2);
  });

  it('stitch: um path contínuo cidade→deserto com width lerp', () => {
    const junc = detectRoadJunctions([cityEast, desertArtery]);
    const chains = stitchEndToEndChains([cityEast, desertArtery], junc);
    expect(chains).toHaveLength(1);
    const c = chains[0]!;
    expect([...c.memberEids].sort()).toEqual([1, 2]);
    expect(c.leaderEid).toBe(1);
    expect(c.path[0]).toBe(3);
    expect(c.path[c.path.length - 2]).toBe(145);
    let joinHits = 0;
    for (let i = 0; i < c.path.length; i += 2) {
      if (Math.hypot(c.path[i]! - 62, c.path[i + 1]! - 1) < 0.01) joinHits++;
    }
    expect(joinHits).toBe(1);

    const widthAt = makeWidthAtFromVertexWidths(c.path, c.widths, 8);
    expect(widthAt(0, 200)).toBeCloseTo(5.4, 5);
    expect(widthAt(200, 200)).toBeCloseTo(4.2, 5);
  });

  it('T-junction: sem stitch end-to-end', () => {
    const trunk: RoadJunctionInput = {
      eid: 10,
      path: [0, 0, 40, 0],
      width: 5,
      edgeFeather: 1,
      textureUrl: null,
      normalMapUrl: null,
      textureScale: 16,
    };
    const spur: RoadJunctionInput = {
      eid: 11,
      path: [20, 0, 20, 30],
      width: 3,
      edgeFeather: 1,
      textureUrl: null,
      normalMapUrl: null,
      textureScale: 16,
    };
    const junc = detectRoadJunctions([trunk, spur]);
    expect(junc.length).toBeGreaterThanOrEqual(1);
    expect(stitchEndToEndChains([trunk, spur], junc)).toHaveLength(0);
  });
});

describe('road: densifyPathByHeight', () => {
  it('inserts midpoints under a convex hump without leaving the segment', () => {
    const heightAt = (x: number, _z: number) => 1 - (x - 5) ** 2 / 25;
    const path = [0, 0, 10, 0];
    const dense = densifyPathByHeight(path, heightAt, 0.05, 4);
    expect(dense.length).toBeGreaterThan(path.length);
    expect(dense[0]).toBe(0);
    expect(dense[dense.length - 2]).toBe(10);
  });

  it('is a no-op on a planar slope', () => {
    const heightAt = (x: number, _z: number) => x * 0.1;
    const path = [0, 0, 5, 0, 10, 0];
    const dense = densifyPathByHeight(path, heightAt, 0.02, 3);
    expect(dense.length).toBe(path.length);
  });
});

describe('road: plugin/recipe', () => {
  it('regista recipe, componente e parser', () => {
    expect(roadRecipe.name).toBe('Road');
    expect(roadRecipe.components).toContain('road');
    expect(RoadPlugin.components?.road).toBe(Road);
    expect(RoadPlugin.config?.parsers?.Road).toBeTypeOf('function');
    expect(RoadPlugin.systems?.some((s) => s.name === 'RoadApplySystem')).toBe(
      true
    );
  });

  it('defaults cobrem largura/feather/scale + prep leito', () => {
    const d = RoadPlugin.config?.defaults?.road as Record<string, number>;
    expect(d.width).toBe(2);
    expect(d.textureScale).toBe(16);
    expect(d.edgeFeather).toBeCloseTo(1.1);
    expect(d.stationSpacing).toBeCloseTo(0.35);
    expect(d.endFeatherEnd).toBe(0);
    expect(d.flatten).toBe(1);
    expect(d.flattenFalloff).toBeCloseTo(8);
    expect(d.flattenWindow).toBeCloseTo(56);
    expect(d.flattenMaxGrade).toBeCloseTo(0.22);
  });
});
