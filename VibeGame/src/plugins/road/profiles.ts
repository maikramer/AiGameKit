/**
 * Named road roles — fill flatten / edge / width defaults so authors pick a
 * profile instead of repeating 8 attributes per Segment.
 */

export type RoadProfileName = 'artery' | 'spur' | 'plaza' | 'bridge';

export interface RoadProfile {
  name: RoadProfileName;
  /** Default painted width (m) when Segment/Way omit width. */
  width: number;
  flatten: boolean;
  edgeFeather: number;
  edgeNoise: number;
  textureScale: number;
  endFeatherStart: number;
  endFeatherEnd: number;
  stationSpacing?: number;
}

export const ROAD_PROFILES: Record<RoadProfileName, RoadProfile> = {
  artery: {
    name: 'artery',
    width: 2,
    flatten: true,
    edgeFeather: 0.7,
    edgeNoise: 0.2,
    textureScale: 16,
    endFeatherStart: 0,
    endFeatherEnd: 0,
    stationSpacing: 0.35,
  },
  spur: {
    name: 'spur',
    width: 2,
    flatten: false,
    edgeFeather: 1.1,
    edgeNoise: 0.7,
    textureScale: 12,
    endFeatherStart: 1.5,
    endFeatherEnd: 3,
    stationSpacing: 0.4,
  },
  plaza: {
    name: 'plaza',
    width: 2.4,
    flatten: true,
    edgeFeather: 0.9,
    edgeNoise: 0.35,
    textureScale: 16,
    endFeatherStart: 0,
    endFeatherEnd: 0,
    stationSpacing: 0.35,
  },
  /**
   * River/gap span: ribbon lerps bank heights, approach-only flatten (never
   * fill channel), solid tips for stitch dock to bank roads. Requires
   * Segment `bridge-url`. Soft edge noise; denser stations for deck pitch.
   */
  bridge: {
    name: 'bridge',
    width: 2,
    flatten: false,
    edgeFeather: 0.55,
    edgeNoise: 0.12,
    textureScale: 16,
    endFeatherStart: 0,
    endFeatherEnd: 0,
    stationSpacing: 0.28,
  },
};

export function resolveRoadProfile(
  raw: string | null | undefined
): RoadProfile | null {
  if (!raw) return null;
  const key = raw.trim().toLowerCase() as RoadProfileName;
  return ROAD_PROFILES[key] ?? null;
}

/** Width multiplier at Ways with degree ≥ 3 (crossing flare). */
export const ROAD_CROSSING_WIDTH_FLARE = 1.45;
