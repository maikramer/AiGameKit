import {
  defineSystem,
  defineQuery,
  type Parser,
  type State,
  type System,
} from '../../core';
import { getAllEntities } from 'bitecs';
import { FarmGrid } from './components';
import { cellIndex, facingCellFrom, type GridSpec } from './grid';
import { attachCropDefs, normalizeCropDef } from './crops';
import {
  ensureFarmGridData,
  farmGridEntities,
  getFarmGridData,
  markAllTilesDirty,
  releaseFarmGridData,
  specOf,
} from './store';
import { flushFarmRender, sweepDeadFarmPools } from './render';
import { updateFarmHighlight } from './highlight';
import { getGroundHeight } from '../terrain/height-sampler';
import { getDataRegistry } from '../rpg-core/registry';
import { Transform, WorldTransform } from '../transforms';
import * as THREE from 'three';

const farmGridQuery = defineQuery([FarmGrid]);

/**
 * Finish grid initialization once the world is up: intern crop defs (the
 * registry must already hold the `crop` YAML — the example loads it before
 * `runtime.start()`), resolve `baseY`, size the side arrays.
 *
 * `after: [TerrainPadApplySystem]` because an auto `baseY` samples the ground
 * the pads have stamped — sampling before the plateau exists would pin the
 * grid to the wild terrain height.
 */
export const FarmGridSetupSystem: System = defineSystem({
  name: 'FarmGridSetupSystem',
  group: 'setup',
  after: ['TerrainPadApplySystem'],
  update: (state) => {
    for (const eid of farmGridQuery(state.world)) {
      const data = ensureFarmGridData(
        state,
        eid,
        FarmGrid.cols[eid],
        FarmGrid.rows[eid]
      );
      if (data.ready) continue;

      if (FarmGrid.baseY[eid] === 0) {
        FarmGrid.baseY[eid] = getGroundHeight(
          state,
          FarmGrid.originX[eid],
          FarmGrid.originZ[eid]
        );
      }

      // Intern crops alphabetically: the save stores this array and remaps by
      // id on load, so adding/reordering crops never corrupts a save.
      const registry = getDataRegistry(state);
      const defs = registry
        .all<Record<string, unknown>>('crop')
        .map(normalizeCropDef)
        .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      data.cropIds = defs.map((d) => d.id);
      attachCropDefs(data, defs);

      data.ready = true;
      markAllTilesDirty(data);
    }
    // World reloads can orphan render pools; sweep them from the draw side.
    for (const eid of farmGridEntities(state)) {
      if (!state.exists(eid)) releaseFarmGridData(state, eid);
    }
  },
});

/** Push dirty tiles into the instanced pools (see render.ts). */
export const FarmRenderSystem: System = defineSystem({
  name: 'FarmRenderSystem',
  group: 'draw',
  update: (state) => {
    sweepDeadFarmPools(state);
    for (const eid of farmGridQuery(state.world)) {
      flushFarmRender(state, eid);
    }
  },
});

// The highlight follows an actor entity. Resolved dynamically so this plugin
// never imports the player plugin: PlayerController when present, else the
// first InputState holder (the actor is whoever reads input). Broad scans are
// fine — highlight runs once per frame over few entities.
function resolveActor(state: State): number {
  for (const component of ['player-controller', 'input-state']) {
    const c = state.getComponent(component);
    if (!c) continue;
    for (const eid of getAllEntities(state.world)) {
      if (state.hasComponent(eid, c)) return eid;
    }
  }
  return 0;
}

const _forward = new THREE.Vector3(0, 0, -1);
const _actorQuat = new THREE.Quaternion();

/** Highlight the tile the actor faces (see highlight.ts). */
export const FarmHighlightSystem: System = defineSystem({
  name: 'FarmHighlightSystem',
  group: 'draw',
  after: ['FarmRenderSystem'],
  update: (state) => {
    const grids = farmGridQuery(state.world);
    if (grids.length === 0) return;
    const actor = resolveActor(state);
    if (!actor || !state.hasComponent(actor, WorldTransform)) return;

    _actorQuat.set(
      WorldTransform.rotX[actor],
      WorldTransform.rotY[actor],
      WorldTransform.rotZ[actor],
      WorldTransform.rotW[actor]
    );
    _forward.set(0, 0, -1).applyQuaternion(_actorQuat);

    updateFarmHighlight(state, grids[0], actor, {
      x: _forward.x,
      z: _forward.z,
    });
  },
});

/** Public helper: the tile `actorEid` is facing on `gridEid`, if any. */
export function getFacingCell(
  state: State,
  gridEid: number,
  actorEid: number
): { col: number; row: number } | null {
  const data = getFarmGridData(state, gridEid);
  if (!data?.ready) return null;
  if (!state.hasComponent(actorEid, WorldTransform)) return null;
  const spec: GridSpec = specOf(state, gridEid);
  _actorQuat.set(
    WorldTransform.rotX[actorEid],
    WorldTransform.rotY[actorEid],
    WorldTransform.rotZ[actorEid],
    WorldTransform.rotW[actorEid]
  );
  _forward.set(0, 0, -1).applyQuaternion(_actorQuat);
  const cell = facingCellFrom(
    WorldTransform.posX[actorEid],
    WorldTransform.posZ[actorEid],
    _forward.x,
    _forward.z,
    spec
  );
  return cell && cellIndex(spec, cell.col, cell.row) >= 0 ? cell : null;
}

/** Parses `<FarmPlot at="x z" size="w d" cell-size="1" base-y="12">`. */
export const farmPlotParser: Parser = ({ entity, element, state }) => {
  const at = element.attributes.at;
  if (at != null) {
    const v = at as { x?: number; y?: number } | string;
    let x = 0;
    let z = 0;
    if (typeof v === 'string') {
      const parts = v
        .trim()
        .split(/[\s,]+/)
        .map(Number);
      x = parts[0] ?? 0;
      z = parts[1] ?? 0;
    } else if (typeof v === 'object') {
      x = Number(v.x) || 0;
      z = Number(v.y) || 0;
    }
    FarmGrid.originX[entity] = x;
    FarmGrid.originZ[entity] = z;
    Transform.posX[entity] = x;
    Transform.posZ[entity] = z;
    Transform.dirty[entity] = 1;
  }
  const cellSize = element.attributes['cell-size'];
  if (typeof cellSize === 'number' && cellSize > 0) {
    FarmGrid.cellSize[entity] = cellSize;
  }
  const size = element.attributes.size;
  if (size != null) {
    const s = size as { x?: number; y?: number } | string;
    let w = 0;
    let d = 0;
    if (typeof s === 'string') {
      const parts = s
        .trim()
        .split(/[\s,]+/)
        .map(Number);
      w = parts[0] ?? 0;
      d = parts[1] ?? parts[0] ?? 0;
    } else if (typeof s === 'object') {
      w = Number(s.x) || 0;
      d = Number(s.y) || w;
    }
    const cs = FarmGrid.cellSize[entity] || 1;
    if (w > 0 && d > 0) {
      FarmGrid.cols[entity] = Math.max(1, Math.round(w / cs));
      FarmGrid.rows[entity] = Math.max(1, Math.round(d / cs));
    }
  }
  const baseY = element.attributes['base-y'];
  if (typeof baseY === 'number') {
    FarmGrid.baseY[entity] = baseY;
  }
  ensureFarmGridData(
    state,
    entity,
    FarmGrid.cols[entity],
    FarmGrid.rows[entity]
  );
};
