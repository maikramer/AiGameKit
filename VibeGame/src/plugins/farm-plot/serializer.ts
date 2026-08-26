import type { State } from '../../core';
import { FarmGrid } from './components';
import { attachCropDefs, normalizeCropDef } from './crops';
import {
  ensureFarmGridData,
  markAllTilesDirty,
  type FarmGridData,
} from './store';
import { getDataRegistry } from '../rpg-core/registry';
import type {
  SaveSerializer,
  SerializedKind,
} from '../save-load/serializer-registry';

/**
 * Save format for one farm grid.
 *
 * Tiles pack into 10 bytes each (state, cropIdx, stage, growthDays, dryDays,
 * wateredToday, regrowCount) and base64 into `tiles`. The header carries the
 * interned `cropIds` alphabet; on load ids are remapped to the CURRENT
 * interning (crops may have been added or reordered since the save), and a
 * missing crop loads as withered-free Empty rather than corrupting the field.
 */

export interface FarmGridSave {
  v: 1;
  cols: number;
  rows: number;
  cellSize: number;
  originX: number;
  originZ: number;
  baseY: number;
  cropIds: string[];
  tiles: string;
}

const BYTES_PER_TILE = 10;

export function serializeFarmGrid(
  state: State,
  eid: number
): FarmGridSave | null {
  if (!state.hasComponent(eid, FarmGrid)) return null;
  const data = ensureFarmGridData(
    state,
    eid,
    FarmGrid.cols[eid],
    FarmGrid.rows[eid]
  );
  const tiles = data.state.length;
  const bytes = new Uint8Array(tiles * BYTES_PER_TILE);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < tiles; i++) {
    const o = i * BYTES_PER_TILE;
    bytes[o] = data.state[i];
    view.setInt16(o + 1, data.cropId[i], true);
    bytes[o + 3] = data.stage[i];
    view.setUint16(o + 4, data.growthDays[i], true);
    view.setUint16(o + 6, data.dryDays[i], true);
    bytes[o + 8] = data.wateredToday[i];
    bytes[o + 9] = data.regrowCount[i];
  }
  return {
    v: 1,
    cols: data.cols,
    rows: data.rows,
    cellSize: FarmGrid.cellSize[eid],
    originX: FarmGrid.originX[eid],
    originZ: FarmGrid.originZ[eid],
    baseY: FarmGrid.baseY[eid],
    cropIds: data.cropIds,
    tiles: bytesToBase64(bytes),
  };
}

export function deserializeFarmGrid(
  state: State,
  eid: number,
  payload: SerializedKind
): boolean {
  if (!state.hasComponent(eid, FarmGrid)) return false;
  const save = payload as FarmGridSave;
  if (
    !save ||
    save.v !== 1 ||
    !Array.isArray(save.cropIds) ||
    typeof save.tiles !== 'string'
  ) {
    return false;
  }

  let bytes: Uint8Array;
  try {
    bytes = base64ToBytes(save.tiles);
  } catch {
    return false; // not base64 — corrupted or foreign payload
  }
  const tiles = Math.floor(bytes.length / BYTES_PER_TILE);
  if (tiles !== save.cols * save.rows) return false;

  FarmGrid.cols[eid] = save.cols;
  FarmGrid.rows[eid] = save.rows;
  FarmGrid.cellSize[eid] = save.cellSize;
  FarmGrid.originX[eid] = save.originX;
  FarmGrid.originZ[eid] = save.originZ;
  FarmGrid.baseY[eid] = save.baseY;

  const data = ensureFarmGridData(state, eid, save.cols, save.rows);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  // Remap the save's crop alphabet onto the current one; ids unknown today
  // (mod removed a crop) keep the tile but drop the plant.
  const current = internCurrentCrops(state, data);
  const remap = new Int16Array(save.cropIds.length);
  for (let s = 0; s < save.cropIds.length; s++) {
    remap[s] = current.indexOf(save.cropIds[s]);
  }

  for (let i = 0; i < tiles; i++) {
    const o = i * BYTES_PER_TILE;
    const savedCrop = view.getInt16(o + 1, true);
    const mapped = savedCrop >= 0 ? remap[savedCrop] : -1;
    data.state[i] = mapped >= 0 ? bytes[o] : stripCrop(bytes[o]);
    data.cropId[i] = mapped;
    data.stage[i] = mapped >= 0 ? bytes[o + 3] : 0;
    data.growthDays[i] = view.getUint16(o + 4, true);
    data.dryDays[i] = view.getUint16(o + 6, true);
    data.wateredToday[i] = bytes[o + 8];
    data.regrowCount[i] = bytes[o + 9];
    data.cropSlot[i] = -1;
    data.slotCrop[i] = -1;
  }
  data.ready = true;
  FarmGrid.version[eid]++;
  markAllTilesDirty(data);
  return true;
}

function stripCrop(tileState: number): number {
  // A tile whose crop no longer exists degrades to its bare-soil state.
  return tileState === 1 ? 1 : 0;
}

/** (Re)intern the registry's crops into `data`; returns the id list used. */
function internCurrentCrops(state: State, data: FarmGridData): string[] {
  const registry = getDataRegistry(state);
  const defs = registry
    .all<Record<string, unknown>>('crop')
    .map(normalizeCropDef)
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  data.cropIds = defs.map((d) => d.id);
  attachCropDefs(data, defs);
  return data.cropIds;
}

export const farmGridSerializer: SaveSerializer = {
  serialize: (state, eid) => serializeFarmGrid(state, eid),
  deserialize: (state, eid, data) => {
    deserializeFarmGrid(state, eid, data);
  },
};

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function base64ToBytes(text: string): Uint8Array {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
