import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

/**
 * Equirectangular sky entity. The texture URL is kept in a side map (strings
 * don't fit in TypedArrays); the component holds the numeric/flag fields and the
 * `applied` latch so {@link EquirectSkyLoadSystem} only loads once.
 */
export const EquirectSky = defineComponent({
  rotationDeg: F32,
  setBackground: U8,
  applied: U8,
  /** IBL intensity applied to `scene.environmentIntensity`. 0 = use fallback. */
  environmentIntensity: F32,
  /** Sky background intensity applied to `scene.backgroundIntensity`. 0 = use fallback. */
  backgroundIntensity: F32,
});

const equirectSkyUrls = new Map<number, string>();

export function setEquirectSkyUrl(entity: number, url: string): void {
  equirectSkyUrls.set(entity, url);
}

export function getEquirectSkyUrl(entity: number): string | undefined {
  return equirectSkyUrls.get(entity);
}

/**
 * Procedural atmospheric sky (Preetham scattering + shader clouds + sun disc).
 * The sun direction, color and intensity derived from the elevation drive the
 * first `directional-light` entity, so shadows, god rays and the sky share one
 * sun. `environmentIntensity` scales the PMREM IBL generated from this sky.
 */
export const ProceduralSky = defineComponent({
  /** Atmospheric haze (2 = clear, 10 = murky). */
  turbidity: F32,
  /** Rayleigh scattering strength — higher deepens the blue. */
  rayleigh: F32,
  mieCoefficient: F32,
  /** Mie lobe direction (0.8–0.9 = broad sun halo). */
  mieDirectionalG: F32,
  /** Sun elevation in degrees above the horizon. */
  sunElevation: F32,
  /** Sun azimuth in degrees around the horizon. */
  sunAzimuth: F32,
  /** Shader cloud coverage (0 = none, 1 = overcast). */
  cloudCoverage: F32,
  /** Shader cloud opacity. */
  cloudDensity: F32,
  /** Cloud plane height cue (0 = near horizon, 1 = overhead). */
  cloudElevation: F32,
  /** IBL intensity for `scene.environmentIntensity`. 0 = use fallback. */
  environmentIntensity: F32,
  /** Directional light intensity override. 0 = keep the light entity's value. */
  sunIntensity: F32,
  /** 1 = drive the first directional light from the sun position. */
  driveLight: U8,
});
