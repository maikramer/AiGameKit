import { logger } from '../../core/utils/logger';
import type { Parser } from '../../core';
import { EquirectSky, setEquirectSkyUrl } from './components';

function toBool(value: unknown, fallback: boolean): boolean {
  if (value === undefined || value === null || value === '') return fallback;
  const s = String(value).trim().toLowerCase();
  return s === '1' || s === 'true' || s === 'yes';
}

function toNumber(value: unknown, fallback: number): number {
  if (value === undefined || value === null) return fallback;
  const n = parseFloat(String(value));
  return Number.isNaN(n) ? fallback : n;
}

export const equirectSkyParser: Parser = ({ entity, element }) => {
  if (element.tagName.toLowerCase() !== 'equirectsky') return;

  const url = element.attributes['url'];
  if (typeof url !== 'string' || !url.trim()) {
    logger.warn('[sky] <EquirectSky> requires a "url" attribute — skipped');
    EquirectSky.applied[entity] = 1;
    return;
  }

  setEquirectSkyUrl(entity, url.trim());
  EquirectSky.rotationDeg[entity] = toNumber(
    element.attributes['rotation-deg'],
    0
  );
  EquirectSky.setBackground[entity] = toBool(
    element.attributes['set-background'],
    true
  )
    ? 1
    : 0;
  EquirectSky.applied[entity] = 0;
};
