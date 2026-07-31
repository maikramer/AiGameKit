import { splitNumbers } from '../../core';
import { defineSystem, defineQuery } from '../../core';
import type { Parser, State, System } from '../../core';
import { Transform } from '../transforms/components';
import { Terrain, TerrainPad } from './components';
import { registerGroundBrush } from './brush-registry';
import { flattenRect } from './flatten';
import { applyFeatureDensity, densityLeafPad } from './ground-mutation';
import { rebuildTerrainDerivatives } from './height-brush';
import { sampleHeightAt } from './height-sampler';
import { refreshChunkResolutions } from './systems';
import { getTerrainContext } from './utils';
import { logger } from '../../core/utils/logger';

const padQuery = defineQuery([TerrainPad, Transform]);

/**
 * Applies each `<TerrainPad>` once the terrain heightmap is decoded: levels
 * the rounded-rect pad into the shared sampler and rebuilds the terrain
 * derivatives (chunk meshes, physics heightfields, BVH) — the same mutate-
 * the-sampler contract the water carve uses, so placement, spawners and
 * physics all see the flattened ground.
 *
 * Ordering: group 'setup', and the water apply systems declare
 * `after: [TerrainPadApplySystem]` — pads must stamp before lakes/rivers
 * carve, or a pad overlapping a channel would fill it back in.
 */
export const TerrainPadApplySystem: System = defineSystem({
  name: 'TerrainPadApplySystem',
  group: 'setup',
  update(state: State) {
    if (state.headless) return;
    for (const eid of padQuery(state.world)) {
      if (TerrainPad.applied[eid] === 1) continue;

      const context = getTerrainContext(state);
      let field: {
        entity: number;
        data: import('./utils').TerrainEntityData;
      } | null = null;
      for (const [fe, fd] of context) {
        if (fd.initialized && fd.sampler.data) {
          field = { entity: fe, data: fd };
          break;
        }
      }
      if (!field) continue; // sampler not decoded yet — retry next frame
      const { data } = field;

      const lx = Transform.posX[eid] - data.worldOffset.x;
      const lz = Transform.posZ[eid] - data.worldOffset.z;
      const halfX = TerrainPad.halfX[eid] || 8;
      const halfZ = TerrainPad.halfZ[eid] || 8;
      // Auto height: the untouched terrain at the pad centre.
      const targetY =
        TerrainPad.height[eid] !== 0
          ? TerrainPad.height[eid]
          : sampleHeightAt(data.sampler, lx, lz);
      const falloff = TerrainPad.falloff[eid] || 8;
      const cornerRadius = TerrainPad.cornerRadius[eid] || 4;

      const changed = flattenRect(data.sampler, {
        centerX: lx,
        centerZ: lz,
        halfX,
        halfZ,
        targetY,
        falloff,
        cornerRadius,
      });

      // Density boost over the pad+falloff (shared ground-mutation + leafPad).
      const reachX = halfX + falloff;
      const reachZ = halfZ + falloff;
      if (data.density) {
        const levels = Math.max(1, Terrain.levels[field.entity] || 1);
        const worldSize =
          Terrain.worldSize[field.entity] || data.sampler.worldSize;
        applyFeatureDensity(
          data.density,
          {
            minX: lx - reachX,
            maxX: lx + reachX,
            minZ: lz - reachZ,
            maxZ: lz + reachZ,
          },
          255,
          densityLeafPad(worldSize, levels)
        );
        refreshChunkResolutions(state, field.entity, data);
      }

      if (changed) rebuildTerrainDerivatives(state, field.entity, data);

      // Persist resolved height so navmesh / consumers can read the pad plane
      // even when the recipe used auto height (height=0 before apply).
      TerrainPad.height[eid] = targetY;
      registerGroundBrush(state, {
        kind: 'pad',
        minX: lx - reachX,
        maxX: lx + reachX,
        minZ: lz - reachZ,
        maxZ: lz + reachZ,
        targetY,
        halfX,
        halfZ,
        cornerRadius,
      });

      TerrainPad.applied[eid] = 1;
      logger.info(
        `[terrain] pad ${eid} applied: y=${targetY.toFixed(1)} core=${(halfX * 2).toFixed(0)}x${(halfZ * 2).toFixed(0)}m falloff=${falloff}m`
      );
    }
  },
});

/** Parses `<TerrainPad at="x z" size="w d">` into Transform + TerrainPad. */
export const terrainPadParser: Parser = ({ entity, element }) => {
  const at = element.attributes.at;
  if (at != null) {
    const v = at as { x?: number; y?: number } | string;
    let x = 0;
    let z = 0;
    if (typeof v === 'string') {
      const parts = splitNumbers(v);
      x = parts[0] ?? 0;
      z = parts[1] ?? 0;
    } else if (typeof v === 'object') {
      x = Number(v.x) || 0;
      z = Number(v.y) || 0;
    }
    Transform.posX[entity] = x;
    Transform.posZ[entity] = z;
    Transform.dirty[entity] = 1;
  }
  const size = element.attributes.size;
  if (size != null) {
    const s = size as { x?: number; y?: number } | string;
    let w = 0;
    let d = 0;
    if (typeof s === 'string') {
      const parts = splitNumbers(s);
      w = parts[0] ?? 0;
      d = parts[1] ?? parts[0] ?? 0;
    } else if (typeof s === 'object') {
      w = Number(s.x) || 0;
      d = Number(s.y) || w;
    }
    if (w > 0) TerrainPad.halfX[entity] = w / 2;
    if (d > 0) TerrainPad.halfZ[entity] = d / 2;
  }
};
