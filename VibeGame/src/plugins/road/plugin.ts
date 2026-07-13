import type { Parser, Plugin, Recipe } from '../../core';
import { Transform } from '../transforms/components';
import { Road, setRoadData } from './components';
import { RoadApplySystem } from './systems';

/**
 * `<Road path="0 4 0 24 6 40" width="5.4" texture-url="/assets/x.png">` —
 * estrada pintada sobre o terreno ao longo do path (coordenadas de mundo,
 * lista plana `x0 z0 x1 z1 ...`). A textura orienta-se e curva com a estrada;
 * bordas com feather + ruído orgânico; pontas com end-feather (0 = sólida,
 * para enterrar sob praças/pads).
 */
export const roadRecipe: Recipe = {
  name: 'Road',
  components: ['transform', 'road'],
  parserAttributes: [
    'path',
    'texture-url',
    'normal-map-url',
    'roughness-map-url',
  ],
};

function parseFlatPath(raw: unknown): number[] {
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
  return typeof raw === 'string' && raw.trim() !== '' ? raw.trim() : null;
}

const roadParser: Parser = ({ state, entity, element }) => {
  const attrs = element.attributes;
  const path = parseFlatPath(attrs.path);
  if (path.length >= 4) {
    Transform.posX[entity] = path[0]!;
    Transform.posZ[entity] = path[1]!;
    Transform.dirty[entity] = 1;
  }
  setRoadData(state, entity, {
    path,
    textureUrl: strAttr(attrs['texture-url']),
    normalMapUrl: strAttr(attrs['normal-map-url']),
    roughnessMapUrl: strAttr(attrs['roughness-map-url']),
  });
};

export const RoadPlugin: Plugin = {
  systems: [RoadApplySystem],
  recipes: [roadRecipe],
  components: { road: Road },
  config: {
    defaults: {
      road: {
        width: 5,
        textureScale: 16,
        edgeFeather: 1.1,
        edgeNoise: 0.45,
        endFeatherStart: 2,
        endFeatherEnd: 2,
        yOffset: 0.12,
        stationSpacing: 1.5,
        smoothing: 2,
        opacity: 1,
        roughness: 1,
        metalness: 0,
        // Default ON: estradas sobre terreno precisam do corredor esculpido
        // (+ density boost) para o mesh LOD acompanhar; flatten="0" desliga.
        flatten: 1,
        flattenFalloff: 6,
        flattenWindow: 24,
        applied: 0,
      },
    },
    parsers: {
      Road: roadParser,
    },
  },
};
