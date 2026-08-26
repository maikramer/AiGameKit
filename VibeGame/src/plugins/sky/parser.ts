import { logger } from '../../core/utils/logger';
import { parseBoolAttr, parseNumberAttr, type Parser } from '../../core';
import { EquirectSky, ProceduralSky, setEquirectSkyUrl } from './components';

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

export const proceduralSkyParser: Parser = ({ entity, element }) => {
  if (element.tagName.toLowerCase() !== 'sky') return;

  ProceduralSky.turbidity[entity] = parseNumberAttr(
    element.attributes['turbidity'],
    2.8
  );
  ProceduralSky.rayleigh[entity] = parseNumberAttr(
    element.attributes['rayleigh'],
    1.6
  );
  ProceduralSky.mieCoefficient[entity] = parseNumberAttr(
    element.attributes['mie-coefficient'],
    0.004
  );
  ProceduralSky.mieDirectionalG[entity] = parseNumberAttr(
    element.attributes['mie-directional-g'],
    0.85
  );
  ProceduralSky.sunElevation[entity] = parseNumberAttr(
    element.attributes['sun-elevation'],
    35
  );
  ProceduralSky.sunAzimuth[entity] = parseNumberAttr(
    element.attributes['sun-azimuth'],
    160
  );
  ProceduralSky.cloudCoverage[entity] = parseNumberAttr(
    element.attributes['cloud-coverage'],
    0.3
  );
  ProceduralSky.cloudDensity[entity] = parseNumberAttr(
    element.attributes['cloud-density'],
    0.35
  );
  ProceduralSky.cloudElevation[entity] = parseNumberAttr(
    element.attributes['cloud-elevation'],
    0.5
  );
  ProceduralSky.environmentIntensity[entity] = parseNumberAttr(
    element.attributes['environment-intensity'],
    0
  );
  ProceduralSky.sunIntensity[entity] = parseNumberAttr(
    element.attributes['sun-intensity'],
    0
  );
  ProceduralSky.driveLight[entity] = parseBoolAttr(
    element.attributes['drive-light'],
    true
  )
    ? 1
    : 0;
};
