import { describe, expect, it } from 'bun:test';
import type { ParsedElement, XMLValue } from '../../../src/core';
import {
  buildRoadNetworkGraph,
  buildSegmentPathAndWidths,
  detectRoadJunctions,
  expandRoadNetworkToRoads,
  makeWidthAtFromVertexWidths,
  parseRoadNetworkElement,
  parseWayXZ,
  pathBetweenWays,
  planRoadFusion,
  ROAD_CROSSING_WIDTH_FLARE,
  ROADBED_OVERHANG,
  roadNetworkRecipe,
  stitchEndToEndChains,
  wayDegrees,
  type RoadJunctionInput,
} from '../../../src/plugins/road';

function el(
  tagName: string,
  attributes: Record<string, XMLValue>,
  children: ParsedElement[] = []
): ParsedElement {
  return { tagName, attributes, children };
}

describe('RoadNetwork expand', () => {
  it('parses Ways/Segments and expands to Roads with widths=', () => {
    const xml = el(
      'RoadNetwork',
      {
        'default-width': 2,
        'texture-url': '/t.png',
        flatten: 1,
        'crossing-flare': 0,
      },
      [
        el('Way', { id: 'plaza', xz: '0 0', width: 2.4 }),
        el('Way', { id: 'gate_e', xz: '62 1' }),
        el('Way', { id: 'desert', xz: '145 -2' }),
        el('Segment', { a: 'plaza', b: 'gate_e' }),
        el('Segment', { a: 'gate_e', b: 'desert', width: 2 }),
      ]
    );
    const def = parseRoadNetworkElement(xml);
    expect(def.ways.size).toBe(3);
    expect(def.segments).toHaveLength(2);
    expect(def.defaultWidth).toBe(2);

    const deg = wayDegrees(def);
    expect(deg.get('plaza')).toBe(1);
    expect(deg.get('gate_e')).toBe(2);
    expect(deg.get('desert')).toBe(1);

    const roads = expandRoadNetworkToRoads(def);
    expect(roads).toHaveLength(2);
    expect(roads[0]!.attributes.path).toBe('0 0 62 1');
    expect(String(roads[0]!.attributes.widths).startsWith('2.4')).toBe(true);
    expect(roads[0]!.attributes['texture-url']).toBe('/t.png');
    expect(roads[1]!.attributes.widths).toBe('2 2');
  });

  it('via= densifies segment path', () => {
    const xml = el('RoadNetwork', { 'default-width': 2, 'crossing-flare': 0 }, [
      el('Way', { id: 'a', xz: '0 0' }),
      el('Way', { id: 'b', xz: '30 0' }),
      el('Segment', { a: 'a', b: 'b', via: '10 0  20 0' }),
    ]);
    const roads = expandRoadNetworkToRoads(parseRoadNetworkElement(xml));
    expect(roads[0]!.attributes.path).toBe('0 0 10 0 20 0 30 0');
    const widths = String(roads[0]!.attributes.widths).split(/\s+/).map(Number);
    expect(widths).toHaveLength(4);
    expect(widths[0]).toBeCloseTo(2);
    expect(widths[3]).toBeCloseTo(2);
  });

  it('default-profile=artery + segment profile=spur sets flatten', () => {
    const xml = el(
      'RoadNetwork',
      { 'default-profile': 'artery', 'texture-url': '/c.png' },
      [
        el('Way', { id: 'a', xz: '0 0' }),
        el('Way', { id: 'b', xz: '10 0' }),
        el('Segment', { a: 'a', b: 'b', profile: 'spur' }),
      ]
    );
    const roads = expandRoadNetworkToRoads(parseRoadNetworkElement(xml));
    expect(roads[0]!.attributes.flatten).toBe(0);
    expect(roads[0]!.attributes['end-feather-end']).toBe(3);
  });

  it('crossing flare widens plaza tips (degree ≥ 3)', () => {
    const xml = el('RoadNetwork', { 'default-width': 2, 'crossing-flare': 1 }, [
      el('Way', { id: 'plaza', xz: '0 0' }),
      el('Way', { id: 'n', xz: '0 40' }),
      el('Way', { id: 's', xz: '0 -40' }),
      el('Way', { id: 'e', xz: '40 0' }),
      el('Segment', { a: 'plaza', b: 'n' }),
      el('Segment', { a: 'plaza', b: 's' }),
      el('Segment', { a: 'plaza', b: 'e' }),
    ]);
    const roads = expandRoadNetworkToRoads(parseRoadNetworkElement(xml));
    const w0 = Number(String(roads[0]!.attributes.widths).split(/\s+/)[0]);
    expect(w0).toBeCloseTo(2 * ROAD_CROSSING_WIDTH_FLARE, 5);
  });

  it('4-way cross: plaza degree 3+, four Roads meet at origin', () => {
    const xml = el(
      'RoadNetwork',
      { 'default-width': 2, 'texture-url': '/cobble.png', 'crossing-flare': 0 },
      [
        el('Way', { id: 'plaza', xz: '0 0', width: 2.4 }),
        el('Way', { id: 'n', xz: '0 40' }),
        el('Way', { id: 's', xz: '0 -40' }),
        el('Way', { id: 'e', xz: '40 0' }),
        el('Way', { id: 'w', xz: '-40 0' }),
        el('Segment', { a: 'plaza', b: 'n' }),
        el('Segment', { a: 'plaza', b: 's' }),
        el('Segment', { a: 'plaza', b: 'e' }),
        el('Segment', { a: 'plaza', b: 'w' }),
      ]
    );
    const def = parseRoadNetworkElement(xml);
    expect(wayDegrees(def).get('plaza')).toBe(4);
    const roads = expandRoadNetworkToRoads(def);
    expect(roads).toHaveLength(4);

    const inputs: RoadJunctionInput[] = roads.map((r, i) => {
      const path = String(r.attributes.path).split(/\s+/).map(Number);
      const widths = String(r.attributes.widths).split(/\s+/).map(Number);
      return {
        eid: i + 1,
        path,
        width: Number(r.attributes.width),
        widths,
        edgeFeather: 1,
        textureUrl: '/cobble.png',
        normalMapUrl: null,
        textureScale: 16,
      };
    });
    const junc = detectRoadJunctions(inputs);
    const cross = junc.find((j) => j.arms.length >= 3);
    expect(cross).toBeDefined();
    expect(cross!.arms.length).toBe(4);
    expect(stitchEndToEndChains(inputs, junc)).toHaveLength(0);
    const plans = planRoadFusion(inputs, junc);
    const p0 = plans.get(1)!;
    expect(p0.startSolid || p0.endSolid).toBe(true);
  });

  it('pathBetweenWays BFS on graph', () => {
    const xml = el('RoadNetwork', { 'default-width': 2 }, [
      el('Way', { id: 'a', xz: '0 0' }),
      el('Way', { id: 'b', xz: '10 0' }),
      el('Way', { id: 'c', xz: '20 0' }),
      el('Segment', { a: 'a', b: 'b' }),
      el('Segment', { a: 'b', b: 'c' }),
    ]);
    const g = buildRoadNetworkGraph(parseRoadNetworkElement(xml));
    expect(pathBetweenWays(g, 'a', 'c')).toEqual(['a', 'b', 'c']);
    expect(pathBetweenWays(g, 'a', 'missing')).toBeNull();
  });

  it('cross + periurban ring: mid_* degree 4 and ring reaches all arms', () => {
    // Topology mirrors simple-rpg paths/network.xml (scaled).
    const xml = el('RoadNetwork', { 'default-width': 2, 'crossing-flare': 0 }, [
      el('Way', { id: 'plaza', xz: '0 0' }),
      el('Way', { id: 'mid_n', xz: '0 28' }),
      el('Way', { id: 'mid_e', xz: '28 0' }),
      el('Way', { id: 'mid_s', xz: '0 -28' }),
      el('Way', { id: 'mid_w', xz: '-28 0' }),
      el('Way', { id: 'ring_ne', xz: '24 24' }),
      el('Way', { id: 'ring_se', xz: '24 -24' }),
      el('Way', { id: 'ring_sw', xz: '-24 -24' }),
      el('Way', { id: 'ring_nw', xz: '-24 24' }),
      el('Way', { id: 'n_end', xz: '0 80' }),
      el('Way', { id: 'desert_end', xz: '80 0' }),
      el('Segment', { a: 'plaza', b: 'mid_n' }),
      el('Segment', { a: 'plaza', b: 'mid_e' }),
      el('Segment', { a: 'plaza', b: 'mid_s' }),
      el('Segment', { a: 'plaza', b: 'mid_w' }),
      el('Segment', { a: 'mid_n', b: 'n_end' }),
      el('Segment', { a: 'mid_e', b: 'desert_end' }),
      el('Segment', { a: 'mid_n', b: 'ring_ne' }),
      el('Segment', { a: 'ring_ne', b: 'mid_e' }),
      el('Segment', { a: 'mid_e', b: 'ring_se' }),
      el('Segment', { a: 'ring_se', b: 'mid_s' }),
      el('Segment', { a: 'mid_s', b: 'ring_sw' }),
      el('Segment', { a: 'ring_sw', b: 'mid_w' }),
      el('Segment', { a: 'mid_w', b: 'ring_nw' }),
      el('Segment', { a: 'ring_nw', b: 'mid_n' }),
    ]);
    const def = parseRoadNetworkElement(xml);
    const deg = wayDegrees(def);
    expect(deg.get('plaza')).toBe(4);
    expect(deg.get('mid_n')).toBe(4);
    expect(deg.get('mid_e')).toBe(4);
    expect(expandRoadNetworkToRoads(def).length).toBeGreaterThan(8);
    const g = buildRoadNetworkGraph(def);
    expect(g.adj.get('mid_n')).toEqual(
      expect.arrayContaining(['plaza', 'n_end', 'ring_ne', 'ring_nw'])
    );
    expect(g.adj.get('ring_ne')).toEqual(
      expect.arrayContaining(['mid_n', 'mid_e'])
    );
    expect(g.adj.get('ring_se')).toEqual(
      expect.arrayContaining(['mid_e', 'mid_s'])
    );
    expect(g.adj.get('ring_sw')).toEqual(
      expect.arrayContaining(['mid_s', 'mid_w'])
    );
    expect(g.adj.get('ring_nw')).toEqual(
      expect.arrayContaining(['mid_w', 'mid_n'])
    );
    expect(pathBetweenWays(g, 'plaza', 'desert_end')).toEqual([
      'plaza',
      'mid_e',
      'desert_end',
    ]);
  });

  it('buildSegmentPathAndWidths lerps width along via', () => {
    const { path, widths } = buildSegmentPathAndWidths(
      { id: 'a', x: 0, z: 0 },
      { id: 'b', x: 30, z: 0 },
      [10, 0, 20, 0],
      2,
      4
    );
    expect(path).toEqual([0, 0, 10, 0, 20, 0, 30, 0]);
    expect(widths[0]).toBeCloseTo(2);
    expect(widths[3]).toBeCloseTo(4);
    expect(widths[1]!).toBeGreaterThan(2);
    expect(widths[1]!).toBeLessThan(4);
  });

  it('degree-2 chain stitches with per-vertex widths', () => {
    const a: RoadJunctionInput = {
      eid: 1,
      path: [0, 0, 10, 0],
      width: 2.4,
      widths: [2.4, 2],
      edgeFeather: 1,
      textureUrl: '/c.png',
      normalMapUrl: null,
      textureScale: 16,
    };
    const b: RoadJunctionInput = {
      eid: 2,
      path: [10, 0, 30, 0],
      width: 2,
      widths: [2, 2],
      edgeFeather: 1,
      textureUrl: '/c.png',
      normalMapUrl: null,
      textureScale: 16,
    };
    const junc = detectRoadJunctions([a, b]);
    const chains = stitchEndToEndChains([a, b], junc);
    expect(chains).toHaveLength(1);
    const c = chains[0]!;
    expect(c.widths[0]).toBeCloseTo(2.4);
    expect(c.widths[c.widths.length - 1]).toBeCloseTo(2);
    const widthAt = makeWidthAtFromVertexWidths(c.path, c.widths, 4);
    expect(widthAt(0, 30)).toBeGreaterThan(2.1);
    expect(widthAt(30, 30)).toBeCloseTo(2, 5);
  });

  it('rejects unknown Way id on Segment', () => {
    expect(() =>
      parseRoadNetworkElement(
        el('RoadNetwork', {}, [
          el('Way', { id: 'a', xz: '0 0' }),
          el('Segment', { a: 'a', b: 'missing' }),
        ])
      )
    ).toThrow(/unknown Way/);
  });

  it('recipe RoadNetwork owns children', () => {
    expect(roadNetworkRecipe.name).toBe('RoadNetwork');
    expect(roadNetworkRecipe.parserOwnsChildren).toBe(true);
  });

  it('ROADBED_OVERHANG is 2 m (1 m each side)', () => {
    expect(ROADBED_OVERHANG).toBe(2);
  });

  it('parseWayXZ accepts XMLValueParser 2-float vector {x,y} as world xz', () => {
    expect(parseWayXZ({ xz: { x: 62, y: 1 } })).toEqual({ x: 62, z: 1 });
    expect(parseWayXZ({ xz: { x: -4, z: 128 } })).toEqual({ x: -4, z: 128 });
    expect(parseWayXZ({ xz: '80 3' })).toEqual({ x: 80, z: 3 });
  });
});
