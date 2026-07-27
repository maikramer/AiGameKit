import type { Parser, Plugin, Recipe } from '../../core';
import { Transform } from '../transforms/components';
import { Road, setRoadData } from './components';
import { RoadApplySystem } from './systems';

/**
 * `<Road path="0 4 0 24 6 40" width="5.4" texture-url="/assets/x.png">` —
 * 1) prepara o leito no heightfield (carve mínimo), 2) pinta o ribbon no
 * terreno já planado. Path em coords de mundo (`x0 z0 x1 z1 ...`).
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
        flattenFalloff: 5,
        // Longitudinal profile smooth (m) — follows hills but kills bumps.
        flattenWindow: 16,
        // Max |Δh/Δs| on design profile (~22% — a bit more cut/fill OK).
        flattenMaxGrade: 0.22,
        applied: 0,
      },
    },
    parsers: {
      Road: roadParser,
    },
  },
};
