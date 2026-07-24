import { defineSystem, defineQuery } from '../../core';
import type { State, System } from '../../core';
import { NULL_ENTITY } from '../../core/ecs/constants';
import { lerp } from '../../core/math';
import { logger } from '../../core/utils/logger';
import { Transform } from '../transforms/components';
import { WorldTransform } from '../transforms/components';
import { PlayerController } from '../player/components';
import { Postprocessing } from '../postprocessing/components';
import { AmbientLight } from '../rendering/components';
import { crossfadeMusicLayers } from '../audio/mixer';
import {
  getWeather,
  setEnvironmentClouds,
  setEnvironmentRain,
} from '../weather/state';
import { Terrain, setTerrainSplat } from '../terrain';
import { ActiveBiome, BiomeRegion } from './components';
import { findBiomeRegionAt, getBiomeRegions } from './parser';
import { packRgb } from './adapters';
import { buildBiomeSplat } from './splat';

/** Sentinel stored in ActiveBiome when the player is outside every region (vale). */
export const NO_BIOME = NULL_ENTITY;

/** Seconds for the fog/ambient/music crossfade when the active biome changes. */
export const BIOME_BLEND_DURATION = 0.5;

const playerQuery = defineQuery([PlayerController]);
const heightFogQuery = defineQuery([Postprocessing]);
const ambientQuery = defineQuery([AmbientLight]);
const terrainFieldQuery = defineQuery([Terrain]);

const initializedPlayer = new WeakMap<State, number>();
const splatBaked = new WeakMap<State, boolean>();
/** Last player XZ used for biome PIP — skip polygon test when barely moved. */
const lastBiomePip = new WeakMap<
  State,
  { px: number; pz: number; targetEid: number }
>();
let warnedNoFog = false;
let warnedNoAmbient = false;

/**
 * Bake the biome splat once, after both the biome regions and a terrain field
 * exist. Covers the field's world rectangle (centred on its position) so the
 * shader can map world XZ → splat UV. No-op after the first successful bake.
 */
function ensureBiomeSplat(state: State): void {
  if (splatBaked.get(state)) return;
  const regions = getBiomeRegions(state);
  if (regions.length === 0) return;
  const fields = terrainFieldQuery(state.world);
  if (fields.length === 0) return;

  const field = fields[0]!;
  const worldSize = Terrain.worldSize[field];
  if (!worldSize || worldSize <= 0) return;
  const offsetX = state.hasComponent(field, WorldTransform)
    ? WorldTransform.posX[field]
    : 0;
  const offsetZ = state.hasComponent(field, WorldTransform)
    ? WorldTransform.posZ[field]
    : 0;

  const splat = buildBiomeSplat(
    regions,
    offsetX - worldSize / 2,
    offsetZ - worldSize / 2,
    worldSize,
    worldSize
  );
  splatBaked.set(state, true); // mark done even when null (nothing to blend)
  if (!splat) return;

  setTerrainSplat(state, field, {
    splatTexture: splat.texture,
    layerUrls: splat.layerUrls,
    worldMinX: offsetX - worldSize / 2,
    worldMinZ: offsetZ - worldSize / 2,
    worldSizeX: worldSize,
    worldSizeZ: worldSize,
  });
}

interface BiomeBaselines {
  fogDensity: number;
  fogColor: number;
  ambientSky: number;
  ppExposure: number;
  ppBloomStrength: number;
  ppVignetteDarkness: number;
}
const baselines = new WeakMap<State, BiomeBaselines>();

/** Advance a 0..1 blend toward 1 at a fixed rate; clamps at 1. Pure (tested). */
export function advanceBlend(
  blend: number,
  dt: number,
  duration: number
): number {
  if (duration <= 0) return 1;
  const next = blend + dt / duration;
  return next > 1 ? 1 : next;
}

function unpackRgb(packed: number): { r: number; g: number; b: number } {
  return {
    r: ((packed >> 16) & 0xff) / 255,
    g: ((packed >> 8) & 0xff) / 255,
    b: (packed & 0xff) / 255,
  };
}

function firstHeightFogEntity(state: State): number | null {
  for (const eid of heightFogQuery(state.world)) {
    if (Postprocessing.heightFog[eid]) return eid;
  }
  return null;
}

function firstPostprocessingEntity(state: State): number | null {
  for (const eid of heightFogQuery(state.world)) {
    if (Postprocessing.enabled[eid]) return eid;
  }
  return null;
}

function firstAmbientEntity(state: State): number | null {
  const ids = ambientQuery(state.world);
  return ids.length > 0 ? ids[0] : null;
}

function ensureBaselines(state: State): BiomeBaselines {
  let b = baselines.get(state);
  if (b) return b;
  const fogEid = firstHeightFogEntity(state);
  const ambEid = firstAmbientEntity(state);
  const ppEid = firstPostprocessingEntity(state);
  b = {
    fogDensity: fogEid != null ? Postprocessing.fogDensity[fogEid] : 0,
    fogColor: fogEid != null ? Postprocessing.fogColor[fogEid] : 0,
    ambientSky: ambEid != null ? AmbientLight.skyColor[ambEid] : 0xffffff,
    ppExposure: ppEid != null ? Postprocessing.toneMappingExposure[ppEid] : 1,
    ppBloomStrength: ppEid != null ? Postprocessing.bloomStrength[ppEid] : 0,
    ppVignetteDarkness:
      ppEid != null ? Postprocessing.vignetteDarkness[ppEid] : 0,
  };
  baselines.set(state, b);
  return b;
}

function fogFor(
  entity: number,
  baseline: BiomeBaselines
): { density: number; color: number } {
  if (entity === NO_BIOME)
    return { density: baseline.fogDensity, color: baseline.fogColor };
  return {
    density: BiomeRegion.fogDensity[entity],
    color: BiomeRegion.fogColor[entity],
  };
}

function ambientSkyFor(entity: number, baseline: BiomeBaselines): number {
  if (entity === NO_BIOME) return baseline.ambientSky;
  return packRgb(
    BiomeRegion.ambientR[entity],
    BiomeRegion.ambientG[entity],
    BiomeRegion.ambientB[entity]
  );
}

function applyVisuals(
  state: State,
  fromEid: number,
  toEid: number,
  blend: number
): void {
  const baseline = ensureBaselines(state);

  const fogEid = firstHeightFogEntity(state);
  if (fogEid != null) {
    const from = fogFor(fromEid, baseline);
    const to = fogFor(toEid, baseline);
    Postprocessing.fogDensity[fogEid] = lerp(from.density, to.density, blend);
    const fc = unpackRgb(from.color);
    const tc = unpackRgb(to.color);
    Postprocessing.fogColor[fogEid] = packRgb(
      lerp(fc.r, tc.r, blend),
      lerp(fc.g, tc.g, blend),
      lerp(fc.b, tc.b, blend)
    );
  } else if (!warnedNoFog) {
    warnedNoFog = true;
    logger.warn(
      '[biomes] No Postprocessing height-fog entity — fog override disabled'
    );
  }

  const ambEid = firstAmbientEntity(state);
  if (ambEid != null) {
    const fromSky = ambientSkyFor(fromEid, baseline);
    const toSky = ambientSkyFor(toEid, baseline);
    const fc = unpackRgb(fromSky);
    const tc = unpackRgb(toSky);
    AmbientLight.skyColor[ambEid] = packRgb(
      lerp(fc.r, tc.r, blend),
      lerp(fc.g, tc.g, blend),
      lerp(fc.b, tc.b, blend)
    );
  } else if (!warnedNoAmbient) {
    warnedNoAmbient = true;
    logger.warn('[biomes] No AmbientLight entity — ambient override disabled');
  }

  // Per-biome post-processing (0 on the region = inherit the baseline).
  const ppEid = firstPostprocessingEntity(state);
  if (ppEid != null) {
    const pick = (eid: number, field: Float32Array, base: number): number =>
      eid === NO_BIOME || field[eid] === 0 ? base : field[eid]!;
    Postprocessing.toneMappingExposure[ppEid] = lerp(
      pick(fromEid, BiomeRegion.ppExposure, baseline.ppExposure),
      pick(toEid, BiomeRegion.ppExposure, baseline.ppExposure),
      blend
    );
    Postprocessing.bloomStrength[ppEid] = lerp(
      pick(fromEid, BiomeRegion.ppBloomStrength, baseline.ppBloomStrength),
      pick(toEid, BiomeRegion.ppBloomStrength, baseline.ppBloomStrength),
      blend
    );
    Postprocessing.vignetteDarkness[ppEid] = lerp(
      pick(
        fromEid,
        BiomeRegion.ppVignetteDarkness,
        baseline.ppVignetteDarkness
      ),
      pick(toEid, BiomeRegion.ppVignetteDarkness, baseline.ppVignetteDarkness),
      blend
    );
  }

  // Biome-driven precipitation → weather plugin (swamp drizzle etc.).
  const rainOf = (eid: number): number =>
    eid === NO_BIOME ? 0 : BiomeRegion.rain[eid]!;
  setEnvironmentRain(state, lerp(rainOf(fromEid), rainOf(toEid), blend));

  // Biome cloud coverage (−1 = inherit Weather cycle). While either side has
  // an explicit override, lerp toward baseline (Weather cloudsTarget) for the
  // inherit side so exits from dark-forest fade smoothly.
  const fromHas =
    fromEid !== NO_BIOME && (BiomeRegion.clouds[fromEid] ?? -1) >= 0;
  const toHas = toEid !== NO_BIOME && (BiomeRegion.clouds[toEid] ?? -1) >= 0;
  if (!fromHas && !toHas) {
    setEnvironmentClouds(state, -1);
  } else {
    const baseline = getWeather(state).cloudsTarget;
    const f = fromHas ? BiomeRegion.clouds[fromEid]! : baseline;
    const t = toHas ? BiomeRegion.clouds[toEid]! : baseline;
    setEnvironmentClouds(state, lerp(f, t, blend));
  }
}

function bgmLayerFor(entity: number): number {
  return entity === NO_BIOME ? 0 : BiomeRegion.bgmLayer[entity];
}

/**
 * Runs after player movement: detects the player's biome region (AABB broad
 * phase + point-in-polygon narrow phase), drives the ActiveBiome blend toward
 * the new target, and applies lerped fog/ambient plus a one-shot music
 * crossfade. Visual writes happen only while a blend is in progress.
 */
export const BiomeDetectionSystem: System = defineSystem({
  name: 'BiomeDetectionSystem',
  group: 'late',
  update(state: State): void {
    if (state.headless) return;

    ensureBiomeSplat(state);

    const players = playerQuery(state.world);
    if (players.length === 0) return;
    const player = players[0]!;

    const inited = initializedPlayer.get(state);
    if (inited !== player) {
      state.addComponent(player, ActiveBiome);
      ActiveBiome.current[player] = NO_BIOME;
      ActiveBiome.target[player] = NO_BIOME;
      ActiveBiome.blend[player] = 1;
      initializedPlayer.set(state, player);
    }

    const px = Transform.posX[player];
    const pz = Transform.posZ[player];
    let cache = lastBiomePip.get(state);
    let targetEid: number;
    if (
      cache &&
      Math.abs(cache.px - px) < 0.15 &&
      Math.abs(cache.pz - pz) < 0.15
    ) {
      targetEid = cache.targetEid;
    } else {
      const hit = findBiomeRegionAt(state, px, pz);
      targetEid = hit ? hit.entity : NO_BIOME;
      if (!cache) {
        cache = { px, pz, targetEid };
        lastBiomePip.set(state, cache);
      } else {
        cache.px = px;
        cache.pz = pz;
        cache.targetEid = targetEid;
      }
    }

    const prevTarget = ActiveBiome.target[player];
    if (targetEid !== prevTarget) {
      ActiveBiome.target[player] = targetEid;
      ActiveBiome.blend[player] = 0;
      crossfadeMusicLayers(
        state,
        bgmLayerFor(ActiveBiome.current[player]),
        bgmLayerFor(targetEid),
        BIOME_BLEND_DURATION
      );
      // Terrain texture is no longer swapped globally — biome ground textures
      // are blended spatially via the baked splat (see ensureBiomeSplat).
    }

    let blend = ActiveBiome.blend[player];
    if (blend >= 1) return;

    const dt = state.time.deltaTime;
    const currentEid = ActiveBiome.current[player];
    blend = advanceBlend(blend, dt, BIOME_BLEND_DURATION);
    applyVisuals(state, currentEid, ActiveBiome.target[player], blend);

    if (blend >= 1) {
      ActiveBiome.current[player] = ActiveBiome.target[player];
    }
    ActiveBiome.blend[player] = blend;
  },
});
