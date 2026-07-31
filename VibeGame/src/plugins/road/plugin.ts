import type { Parser, Plugin, Recipe } from '../../core';
import { processRecipeChildElements } from '../../core/recipes/parser';
import { Transform } from '../transforms/components';
import { Road, setRoadData } from './components';
import {
  buildRoadNetworkGraph,
  expandRoadNetworkToRoads,
  parseRoadNetworkElement,
} from './network';
import { setRoadNetworkGraph } from './queries';
import { RoadApplySystem } from './systems';

/**
 * `<Road path="0 4 0 24 6 40" width="2" texture-url="/assets/x.png">` —
 * 1) prepara o leito no heightfield, 2) pinta o ribbon no terreno planado.
 * Path em coords de mundo (`x0 z0 x1 z1 ...`). Optional `widths="w0 w1 ..."`.
 */
export const roadRecipe: Recipe = {
  name: 'Road',
  components: ['transform', 'road'],
  parserAttributes: [
    'path',
    'widths',
    'texture-url',
    'normal-map-url',
    'roughness-map-url',
    'bridge',
    'bridge-url',
    'bridge-collision-url',
    'bridge-lod1-url',
    'bridge-lod2-url',
    'bridge-native-span',
  ],
};

/**
 * `<RoadNetwork default-width="2" texture-url="...">` with `<Way>` / `<Segment>`
 * children — expands to one `<Road>` per segment (programmatic junctions).
 */
export const roadNetworkRecipe: Recipe = {
  name: 'RoadNetwork',
  components: ['transform'],
  parserOwnsChildren: true,
  parserAttributes: [
    'default-width',
    'default-profile',
    'crossing-flare',
    'flatten',
    'flatten-falloff',
    'flatten-window',
    'flatten-max-grade',
    'texture-url',
    'normal-map-url',
    'roughness-map-url',
    'texture-scale',
    'edge-feather',
    'edge-noise',
    'station-spacing',
  ],
};

export const wayRecipe: Recipe = {
  name: 'Way',
  parserOnlyAsChild: true,
  parserAttributes: ['id', 'xz', 'pos', 'x', 'z', 'width'],
};

export const segmentRecipe: Recipe = {
  name: 'Segment',
  parserOnlyAsChild: true,
  parserAttributes: [
    'a',
    'b',
    'via',
    'width',
    'profile',
    'texture-url',
    'normal-map-url',
    'bridge-url',
    'bridge-collision-url',
    'bridge-lod1-url',
    'bridge-lod2-url',
    'bridge-native-span',
  ],
};

function parseFlatNumbers(raw: unknown): number[] {
  if (Array.isArray(raw)) {
    return raw.map(Number).filter((n) => Number.isFinite(n));
  }
  if (typeof raw === 'string') {
    return raw
      .trim()
      .split(/[\s,]+/)
      .map(Number)
      .filter((n) => Number.isFinite(n));
  }
  return [];
}

function strAttr(raw: unknown): string | null {
  if (raw === undefined || raw === null) return null;
  if (typeof raw === 'string') {
    const s = raw.trim();
    return s !== '' ? s : null;
  }
  // Expand / XMLValue edge cases — coerce numbers etc. only if useful as URL.
  if (typeof raw === 'number' && Number.isFinite(raw)) return null;
  const s = String(raw).trim();
  return s !== '' && s !== 'undefined' && s !== 'null' ? s : null;
}

const roadParser: Parser = ({ state, entity, element }) => {
  const attrs = element.attributes;
  const path = parseFlatNumbers(attrs.path);
  if (path.length >= 4) {
    Transform.posX[entity] = path[0]!;
    Transform.posZ[entity] = path[1]!;
    Transform.dirty[entity] = 1;
  }
  const pointCount = Math.floor(path.length / 2);
  let widths: number[] | undefined;
  if (attrs.widths !== undefined && attrs.widths !== null) {
    const parsed = parseFlatNumbers(attrs.widths);
    if (parsed.length === pointCount && pointCount >= 2) {
      widths = parsed.map((w) => Math.max(0.2, w));
    } else if (parsed.length > 0) {
      throw new Error(
        `[Road] widths= has ${parsed.length} values but path has ${pointCount} points`
      );
    }
  }
  setRoadData(state, entity, {
    path,
    widths,
    textureUrl: strAttr(attrs['texture-url']),
    normalMapUrl: strAttr(attrs['normal-map-url']),
    roughnessMapUrl: strAttr(attrs['roughness-map-url']),
    bridgeUrl: strAttr(attrs['bridge-url']),
    bridgeCollisionUrl: strAttr(attrs['bridge-collision-url']),
    bridgeLod1Url: strAttr(attrs['bridge-lod1-url']),
    bridgeLod2Url: strAttr(attrs['bridge-lod2-url']),
    bridgeNativeSpan: (() => {
      const raw = attrs['bridge-native-span'];
      if (raw === undefined || raw === null) return undefined;
      const n = typeof raw === 'number' ? raw : parseFloat(String(raw));
      return Number.isFinite(n) && n > 0 ? n : undefined;
    })(),
  });
  // Scalar width fallback = max of per-vertex widths when authored.
  if (widths && widths.length > 0) {
    Road.width[entity] = widths.reduce((a, b) => Math.max(a, b), 0);
  }
  const bridgeFlag = attrs.bridge;
  const isBridge =
    bridgeFlag === 1 ||
    bridgeFlag === true ||
    bridgeFlag === '1' ||
    bridgeFlag === 'true' ||
    !!strAttr(attrs['bridge-url']);
  if (isBridge) {
    Road.bridge[entity] = 1;
    // Deck Y filled at apply from bank samples; ribbon uses it immediately.
    Road.deckY[entity] = 0;
  }
};

function requireInsideRoadNetwork(tag: string): Parser {
  return () => {
    throw new Error(
      `[${tag}] must be a child of <RoadNetwork> (not used as a top-level recipe)`
    );
  };
}

const roadNetworkParser: Parser = ({ entity, element, state, context }) => {
  const def = parseRoadNetworkElement(element);
  setRoadNetworkGraph(state, entity, buildRoadNetworkGraph(def));
  const roads = expandRoadNetworkToRoads(def);
  if (roads.length > 0) {
    processRecipeChildElements(state, entity, 'RoadNetwork', roads, context);
  }
};

export const RoadPlugin: Plugin = {
  systems: [RoadApplySystem],
  recipes: [roadRecipe, roadNetworkRecipe, wayRecipe, segmentRecipe],
  components: { road: Road },
  config: {
    defaults: {
      road: {
        width: 2,
        textureScale: 16,
        edgeFeather: 1.1,
        edgeNoise: 0.45,
        // Solid tips by default: faded tip + flatten trough = “feet sink”.
        endFeatherStart: 0,
        endFeatherEnd: 0,
        // Decal sits on the heightfield (CCT). polygonOffset handles z-fight —
        // do not float the ribbon above the walk surface.
        yOffset: 0,
        // Dense base + densifyPathByHeight; never lift above sampler.
        stationSpacing: 0.35,
        smoothing: 2,
        opacity: 1,
        roughness: 1,
        metalness: 0,
        // Default ON: preparar leito → ribbon no sampler planado.
        // flatten="0" = só decal sobre o relevo cru (sem prep).
        flatten: 1,
        // Shoulder blend back to natural relief (m).
        flattenFalloff: 8,
        // Terrace smooth window (m); multi-pass in designRoadProfile.
        flattenWindow: 56,
        // Max |Δh/Δs| on terrace profile (~22%).
        flattenMaxGrade: 0.22,
        bridge: 0,
        deckY: 0,
        deckY0: 0,
        deckY1: 0,
        applied: 0,
      },
    },
    parsers: {
      Road: roadParser,
      RoadNetwork: roadNetworkParser,
      Way: requireInsideRoadNetwork('Way'),
      Segment: requireInsideRoadNetwork('Segment'),
    },
  },
};
