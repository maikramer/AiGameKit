import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Equirectangular sky entity. The texture URL is kept in a side map (strings
 * don't fit in TypedArrays); the component holds the numeric/flag fields and the
 * `applied` latch so {@link EquirectSkyLoadSystem} only loads once.
 */
export const EquirectSky = {
  rotationDeg: new Float32Array(MAX_ENTITIES),
  setBackground: new Uint8Array(MAX_ENTITIES),
  applied: new Uint8Array(MAX_ENTITIES),
} as const;

const equirectSkyUrls = new Map<number, string>();

export function setEquirectSkyUrl(entity: number, url: string): void {
  equirectSkyUrls.set(entity, url);
}

export function getEquirectSkyUrl(entity: number): string | undefined {
  return equirectSkyUrls.get(entity);
}
