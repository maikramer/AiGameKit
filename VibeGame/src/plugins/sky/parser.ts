import { logger } from '../../core/utils/logger';
import { parseBoolAttr, parseNumberAttr, type Parser } from '../../core';
import { EquirectSky, setEquirectSkyUrl } from './components';

export const equirectSkyParser: Parser = ({ entity, element }) => {
  if (element.tagName.toLowerCase() !== 'equirectsky') return;

  const url = element.attributes['url'];
  if (typeof url !== 'string' || !url.trim()) {
    logger.warn('[sky] <EquirectSky> requires a "url" attribute — skipped');
    EquirectSky.applied[entity] = 1;
    return;
  }

  setEquirectSkyUrl(entity, url.trim());
  EquirectSky.rotationDeg[entity] = parseNumberAttr(
    element.attributes['rotation-deg'],
    0
  );
  EquirectSky.setBackground[entity] = parseBoolAttr(
    element.attributes['set-background'],
    true
  )
    ? 1
    : 0;
  // 0 = "use loader default" (keeps backward compat). Positive overrides.
  EquirectSky.environmentIntensity[entity] = parseNumberAttr(
    element.attributes['environment-intensity'],
    0
  );
  EquirectSky.backgroundIntensity[entity] = parseNumberAttr(
    element.attributes['background-intensity'],
    0
  );
  EquirectSky.applied[entity] = 0;
};
