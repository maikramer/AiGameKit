import type { ParsedElement, XMLValue } from '../../core';
import {
  ROAD_CROSSING_WIDTH_FLARE,
  resolveRoadProfile,
  type RoadProfile,
  type RoadProfileName,
} from './profiles';

/**
 * `<RoadNetwork>` — graph of Ways + Segments expanded into `<Road>` ribbons.
 * Shared Way nodes (degree ≥ 3) become programmatic crossings via the existing
 * junction detector (exact endpoint coincidence → end-to-end stitch / T-dock).
 */

export type RoadWay = {
  id: string;
  x: number;
  z: number;
  /** Optional diameter at this node (m). */
  width?: number;
};

export type RoadSegmentDef = {
  a: string;
  b: string;
  /** Intermediate waypoints `[x0,z0,…]` between a and b (optional). */
  via: number[];
  /** Constant width along the arc (m); omit → lerp Way widths / profile. */
  width?: number;
  profile?: RoadProfileName;
  textureUrl?: string | null;
  normalMapUrl?: string | null;
  /** Bridge deck visual GLB (implies profile bridge if unset). */
  bridgeUrl?: string | null;
  bridgeCollisionUrl?: string | null;
  bridgeLod1Url?: string | null;
  bridgeLod2Url?: string | null;
  /** Mesh local +X length (m) before scale; default 18. */
  bridgeNativeSpan?: number;
};

export type RoadNetworkDef = {
  defaultWidth: number;
  defaultProfile: RoadProfile | null;
  flatten: boolean;
  flattenFalloff?: number;
  flattenWindow?: number;
  flattenMaxGrade?: number;
  textureUrl: string | null;
  normalMapUrl: string | null;
  roughnessMapUrl: string | null;
  textureScale?: number;
  edgeFeather?: number;
  edgeNoise?: number;
  stationSpacing?: number;
  /** Apply {@link ROAD_CROSSING_WIDTH_FLARE} at Ways with degree ≥ 3. */
  crossingFlare: boolean;
  ways: Map<string, RoadWay>;
  segments: RoadSegmentDef[];
};

/** Lightweight graph for pathfinding / analyze (Way ids). */
export type RoadNetworkGraph = {
  ways: Map<string, { x: number; z: number; width?: number }>;
  /** Undirected adjacency: wayId → neighbour way ids. */
  adj: Map<string, string[]>;
  segments: Array<{ a: string; b: string; path: number[] }>;
};

function attrString(value: XMLValue | undefined): string | null {
  if (value === undefined || value === null) return null;
  const s = String(value).trim();
  return s === '' ? null : s;
}

function attrNumber(value: XMLValue | undefined, fallback: number): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const n = parseFloat(String(value));
  return Number.isFinite(n) ? n : fallback;
}

function attrBool(value: XMLValue | undefined, fallback: boolean): boolean {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  const s = String(value).trim().toLowerCase();
  if (s === '0' || s === 'false' || s === 'no' || s === 'off') return false;
  if (s === '1' || s === 'true' || s === 'yes' || s === 'on') return true;
  return fallback;
}

/**
 * Parse `xz="x z"` / `pos=`. XMLValueParser turns `"0 0"` into `{x,y}` (2-float
 * vector) — must read that shape, not String(obj) which is useless.
 */
export function parseWayXZ(
  attrs: Record<string, XMLValue>
): { x: number; z: number } | null {
  const xz = attrs.xz ?? attrs.pos;
  if (xz !== undefined && xz !== null) {
    const flat = parseFlatXZList(xz);
    if (flat.length >= 2) return { x: flat[0]!, z: flat[1]! };
  }
  if (attrs.x !== undefined && attrs.z !== undefined) {
    const x = attrNumber(attrs.x, NaN);
    const z = attrNumber(attrs.z, NaN);
    if (Number.isFinite(x) && Number.isFinite(z)) return { x, z };
  }
  return null;
}

/**
 * Flatten XMLValue (string / number[] / {x,y} / {x,y,z} / {x,y,z,w}) into
 * world XZ pairs `[x0,z0,x1,z1,…]`.
 */
export function parseFlatXZList(raw: XMLValue | undefined): number[] {
  if (raw === undefined || raw === null) return [];
  if (Array.isArray(raw)) {
    return raw.map(Number).filter((n) => Number.isFinite(n));
  }
  if (typeof raw === 'object') {
    const v = raw as { x?: number; y?: number; z?: number; w?: number };
    if (
      typeof v.x === 'number' &&
      typeof v.y === 'number' &&
      v.z === undefined
    ) {
      // XMLValueParser 2-float: (x,y) ≡ world (x,z).
      return [v.x, v.y];
    }
    if (
      typeof v.x === 'number' &&
      typeof v.z === 'number' &&
      v.y === undefined
    ) {
      return [v.x, v.z];
    }
    if (
      typeof v.x === 'number' &&
      typeof v.y === 'number' &&
      typeof v.z === 'number' &&
      v.w === undefined
    ) {
      // Ambiguous 3-float — treat as single x,z and drop y (height unused).
      return [v.x, v.z];
    }
    if (
      typeof v.x === 'number' &&
      typeof v.y === 'number' &&
      typeof v.z === 'number' &&
      typeof v.w === 'number'
    ) {
      return [v.x, v.y, v.z, v.w];
    }
    return [];
  }
  if (typeof raw === 'string' || typeof raw === 'number') {
    return String(raw)
      .trim()
      .split(/[\s,]+/)
      .map(Number)
      .filter((n) => Number.isFinite(n));
  }
  return [];
}

export function parseRoadNetworkElement(
  element: ParsedElement
): RoadNetworkDef {
  const attrs = element.attributes;
  const defaultProfile = resolveRoadProfile(
    attrString(attrs['default-profile'])
  );
  const defaultWidth = Math.max(
    0.2,
    attrNumber(attrs['default-width'], defaultProfile?.width ?? 2)
  );
  const ways = new Map<string, RoadWay>();
  const segments: RoadSegmentDef[] = [];

  for (const child of element.children) {
    if (!child.tagName || child.tagName.toLowerCase() === 'parsererror') {
      continue;
    }
    const tag = child.tagName.toLowerCase();
    if (tag === 'way') {
      const id = attrString(child.attributes.id);
      if (!id) {
        throw new Error('[RoadNetwork] <Way> requires id=');
      }
      if (ways.has(id)) {
        throw new Error(`[RoadNetwork] duplicate Way id="${id}"`);
      }
      const xz = parseWayXZ(child.attributes);
      if (!xz) {
        throw new Error(`[RoadNetwork] <Way id="${id}"> requires xz="x z"`);
      }
      const wRaw = child.attributes.width;
      const way: RoadWay = { id, x: xz.x, z: xz.z };
      if (wRaw !== undefined && wRaw !== null && String(wRaw).trim() !== '') {
        way.width = Math.max(0.2, attrNumber(wRaw, defaultWidth));
      }
      ways.set(id, way);
      continue;
    }
    if (tag === 'segment') {
      const a = attrString(child.attributes.a);
      const b = attrString(child.attributes.b);
      if (!a || !b) {
        throw new Error('[RoadNetwork] <Segment> requires a= and b= Way ids');
      }
      if (a === b) {
        throw new Error(`[RoadNetwork] <Segment a="${a}" b="${b}"> is a loop`);
      }
      const viaRaw = parseFlatXZList(child.attributes.via);
      if (viaRaw.length % 2 !== 0) {
        throw new Error(
          `[RoadNetwork] <Segment a="${a}" b="${b}"> via= needs even count (x z pairs)`
        );
      }
      const profileName = attrString(child.attributes.profile);
      const bridgeUrl = attrString(child.attributes['bridge-url']);
      const profile = resolveRoadProfile(
        profileName ?? (bridgeUrl ? 'bridge' : null)
      );
      if (profileName && !profile) {
        throw new Error(
          `[RoadNetwork] unknown profile="${profileName}" (use artery|spur|plaza|bridge)`
        );
      }
      if ((profile?.name === 'bridge' || bridgeUrl) && !bridgeUrl) {
        throw new Error(
          `[RoadNetwork] <Segment a="${a}" b="${b}" profile="bridge"> requires bridge-url=`
        );
      }
      const seg: RoadSegmentDef = {
        a,
        b,
        via: viaRaw,
        profile: profile?.name ?? (bridgeUrl ? 'bridge' : undefined),
        textureUrl: attrString(child.attributes['texture-url']),
        normalMapUrl: attrString(child.attributes['normal-map-url']),
        bridgeUrl,
        bridgeCollisionUrl: attrString(
          child.attributes['bridge-collision-url']
        ),
        bridgeLod1Url: attrString(child.attributes['bridge-lod1-url']),
        bridgeLod2Url: attrString(child.attributes['bridge-lod2-url']),
      };
      const nativeSpanRaw = child.attributes['bridge-native-span'];
      if (nativeSpanRaw !== undefined && nativeSpanRaw !== null) {
        const ns = attrNumber(nativeSpanRaw, NaN);
        if (Number.isFinite(ns) && ns > 0) seg.bridgeNativeSpan = ns;
      }
      const wRaw = child.attributes.width;
      if (wRaw !== undefined && wRaw !== null && String(wRaw).trim() !== '') {
        seg.width = Math.max(0.2, attrNumber(wRaw, defaultWidth));
      }
      segments.push(seg);
      continue;
    }
    throw new Error(
      `[RoadNetwork] unknown child <${child.tagName}>. Use <Way> or <Segment>.`
    );
  }

  for (const seg of segments) {
    if (!ways.has(seg.a)) {
      throw new Error(
        `[RoadNetwork] Segment references unknown Way "${seg.a}"`
      );
    }
    if (!ways.has(seg.b)) {
      throw new Error(
        `[RoadNetwork] Segment references unknown Way "${seg.b}"`
      );
    }
  }

  return {
    defaultWidth,
    defaultProfile,
    flatten: attrBool(attrs.flatten, defaultProfile?.flatten ?? true),
    flattenFalloff:
      attrs['flatten-falloff'] !== undefined
        ? attrNumber(attrs['flatten-falloff'], 8)
        : undefined,
    flattenWindow:
      attrs['flatten-window'] !== undefined
        ? attrNumber(attrs['flatten-window'], 56)
        : undefined,
    flattenMaxGrade:
      attrs['flatten-max-grade'] !== undefined
        ? attrNumber(attrs['flatten-max-grade'], 0.22)
        : undefined,
    textureUrl: attrString(attrs['texture-url']),
    normalMapUrl: attrString(attrs['normal-map-url']),
    roughnessMapUrl: attrString(attrs['roughness-map-url']),
    textureScale:
      attrs['texture-scale'] !== undefined
        ? attrNumber(attrs['texture-scale'], 16)
        : undefined,
    edgeFeather:
      attrs['edge-feather'] !== undefined
        ? attrNumber(attrs['edge-feather'], 1.1)
        : undefined,
    edgeNoise:
      attrs['edge-noise'] !== undefined
        ? attrNumber(attrs['edge-noise'], 0.45)
        : undefined,
    stationSpacing:
      attrs['station-spacing'] !== undefined
        ? attrNumber(attrs['station-spacing'], 0.35)
        : undefined,
    crossingFlare: attrBool(attrs['crossing-flare'], true),
    ways,
    segments,
  };
}

/** Degree of each Way (segment incidences). */
export function wayDegrees(def: RoadNetworkDef): Map<string, number> {
  const deg = new Map<string, number>();
  for (const id of def.ways.keys()) deg.set(id, 0);
  for (const seg of def.segments) {
    deg.set(seg.a, (deg.get(seg.a) ?? 0) + 1);
    deg.set(seg.b, (deg.get(seg.b) ?? 0) + 1);
  }
  return deg;
}

function endpointWidth(
  way: RoadWay,
  segWidth: number | undefined,
  profileWidth: number,
  defaultWidth: number
): number {
  if (segWidth !== undefined) return segWidth;
  if (way.width !== undefined) return way.width;
  return profileWidth > 0 ? profileWidth : defaultWidth;
}

/** Build polyline `[ax,az, …via…, bx,bz]` and per-vertex widths (lerp by arc). */
export function buildSegmentPathAndWidths(
  wa: RoadWay,
  wb: RoadWay,
  via: number[],
  w0: number,
  w1: number
): { path: number[]; widths: number[] } {
  const path = [wa.x, wa.z, ...via, wb.x, wb.z];
  const n = path.length / 2;
  if (n < 2) return { path, widths: [w0, w1] };
  const arcs: number[] = [0];
  let total = 0;
  for (let i = 0; i < n - 1; i++) {
    total += Math.hypot(
      path[(i + 1) * 2]! - path[i * 2]!,
      path[(i + 1) * 2 + 1]! - path[i * 2 + 1]!
    );
    arcs.push(total);
  }
  const widths: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = total > 1e-6 ? arcs[i]! / total : i / (n - 1);
    widths.push(w0 + (w1 - w0) * t);
  }
  return { path, widths };
}

function el(
  tagName: string,
  attributes: Record<string, XMLValue>,
  children: ParsedElement[] = []
): ParsedElement {
  return { tagName, attributes, children };
}

/**
 * Expand a network into flat `<Road>` elements (one per Segment).
 * Endpoint widths become `widths=…`; vias densify the path; crossing Ways
 * get {@link ROAD_CROSSING_WIDTH_FLARE} when enabled.
 */
export function expandRoadNetworkToRoads(def: RoadNetworkDef): ParsedElement[] {
  const deg = wayDegrees(def);
  const out: ParsedElement[] = [];
  for (const seg of def.segments) {
    const profile =
      resolveRoadProfile(seg.profile) ?? def.defaultProfile ?? null;
    const wa = def.ways.get(seg.a)!;
    const wb = def.ways.get(seg.b)!;
    let w0 = endpointWidth(
      wa,
      seg.width,
      profile?.width ?? 0,
      def.defaultWidth
    );
    let w1 = endpointWidth(
      wb,
      seg.width,
      profile?.width ?? 0,
      def.defaultWidth
    );
    if (def.crossingFlare) {
      if ((deg.get(seg.a) ?? 0) >= 3) w0 *= ROAD_CROSSING_WIDTH_FLARE;
      if ((deg.get(seg.b) ?? 0) >= 3) w1 *= ROAD_CROSSING_WIDTH_FLARE;
    }
    const { path, widths } = buildSegmentPathAndWidths(wa, wb, seg.via, w0, w1);
    const paint = widths.reduce((a, b) => Math.max(a, b), def.defaultWidth);
    const isBridge = profile?.name === 'bridge' || !!seg.bridgeUrl;
    const flatten = isBridge ? false : profile ? profile.flatten : def.flatten;
    const attrs: Record<string, XMLValue> = {
      path: path.join(' '),
      widths: widths.map((w) => +w.toFixed(4)).join(' '),
      width: paint,
      flatten: flatten ? 1 : 0,
    };
    const tex = seg.textureUrl ?? def.textureUrl;
    const nrm = seg.normalMapUrl ?? def.normalMapUrl;
    if (tex) attrs['texture-url'] = tex;
    if (nrm) attrs['normal-map-url'] = nrm;
    if (def.roughnessMapUrl) attrs['roughness-map-url'] = def.roughnessMapUrl;
    if (def.flattenFalloff !== undefined) {
      attrs['flatten-falloff'] = def.flattenFalloff;
    }
    if (def.flattenWindow !== undefined) {
      attrs['flatten-window'] = def.flattenWindow;
    }
    if (def.flattenMaxGrade !== undefined) {
      attrs['flatten-max-grade'] = def.flattenMaxGrade;
    }
    attrs['texture-scale'] = def.textureScale ?? profile?.textureScale ?? 16;
    attrs['edge-feather'] = def.edgeFeather ?? profile?.edgeFeather ?? 1.1;
    attrs['edge-noise'] = def.edgeNoise ?? profile?.edgeNoise ?? 0.45;
    attrs['station-spacing'] =
      def.stationSpacing ?? profile?.stationSpacing ?? 0.35;
    attrs['end-feather-start'] = profile?.endFeatherStart ?? 0;
    attrs['end-feather-end'] = profile?.endFeatherEnd ?? 0;
    if (isBridge && seg.bridgeUrl) {
      attrs.bridge = 1;
      attrs['bridge-url'] = seg.bridgeUrl;
      if (seg.bridgeCollisionUrl) {
        attrs['bridge-collision-url'] = seg.bridgeCollisionUrl;
      }
      if (seg.bridgeLod1Url) attrs['bridge-lod1-url'] = seg.bridgeLod1Url;
      if (seg.bridgeLod2Url) attrs['bridge-lod2-url'] = seg.bridgeLod2Url;
      if (seg.bridgeNativeSpan !== undefined) {
        attrs['bridge-native-span'] = seg.bridgeNativeSpan;
      }
    }
    out.push(el('Road', attrs));
  }
  return out;
}

/** Build an undirected Way graph (for pathTo / analyze). */
export function buildRoadNetworkGraph(def: RoadNetworkDef): RoadNetworkGraph {
  const ways = new Map<string, { x: number; z: number; width?: number }>();
  for (const [id, w] of def.ways) {
    ways.set(id, { x: w.x, z: w.z, width: w.width });
  }
  const adj = new Map<string, string[]>();
  const link = (a: string, b: string) => {
    let la = adj.get(a);
    if (!la) {
      la = [];
      adj.set(a, la);
    }
    if (!la.includes(b)) la.push(b);
  };
  const segments: RoadNetworkGraph['segments'] = [];
  for (const seg of def.segments) {
    const wa = def.ways.get(seg.a)!;
    const wb = def.ways.get(seg.b)!;
    const path = [wa.x, wa.z, ...seg.via, wb.x, wb.z];
    segments.push({ a: seg.a, b: seg.b, path });
    link(seg.a, seg.b);
    link(seg.b, seg.a);
  }
  return { ways, adj, segments };
}

/** BFS shortest path on Way ids. */
export function pathBetweenWays(
  graph: RoadNetworkGraph,
  fromId: string,
  toId: string
): string[] | null {
  if (!graph.ways.has(fromId) || !graph.ways.has(toId)) return null;
  if (fromId === toId) return [fromId];
  const prev = new Map<string, string | null>();
  prev.set(fromId, null);
  const q = [fromId];
  for (let qi = 0; qi < q.length; qi++) {
    const cur = q[qi]!;
    for (const nb of graph.adj.get(cur) ?? []) {
      if (prev.has(nb)) continue;
      prev.set(nb, cur);
      if (nb === toId) {
        const out: string[] = [];
        let n: string | null = toId;
        while (n) {
          out.push(n);
          n = prev.get(n) ?? null;
        }
        out.reverse();
        return out;
      }
      q.push(nb);
    }
  }
  return null;
}
