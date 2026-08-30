// Hotbar tools. Tool defs come from /data/tools.yaml (kind `tool` in the
// DataRegistry); this module normalizes them, owns the active selection and
// applies the active tool to the facing farm tile on [J]. Stamina is paid
// here, bag items are consumed/banked here — the engine farm-plot mutators
// stay pure tile-state transitions.

import {
  FarmGrid,
  FarmTileStates,
  Transform,
  addItem,
  clearTile,
  defineQuery,
  getDataRegistry,
  getItemQty,
  getFacingCell,
  getTileState,
  harvestTile,
  plantSeed,
  removeItem,
  setHotbarActive,
  setHotbarSlots,
  spawnFloatingText,
  tillTile,
  waterTile,
} from 'aigamekit-vibegame';
import type { HotbarSlotSpec, State } from 'aigamekit-vibegame';
import { trySpendStamina } from './stamina';

export type ToolAction = 'till' | 'water' | 'plant' | 'hand';

export interface ToolDef {
  id: string;
  name: string;
  icon: string;
  color: string;
  action: ToolAction;
  cropId?: string;
  seedItemId?: string;
}

export const STAMINA_COSTS: Record<ToolAction, number> = {
  till: 6,
  water: 2,
  plant: 2,
  hand: 3,
};

export function normalizeToolDef(raw: Record<string, unknown>): ToolDef | null {
  const action = String(raw.action ?? '');
  if (
    action !== 'till' &&
    action !== 'water' &&
    action !== 'plant' &&
    action !== 'hand'
  ) {
    return null;
  }
  const cropId = raw.cropId !== undefined ? String(raw.cropId) : undefined;
  const seedItemId =
    raw.seedItemId !== undefined ? String(raw.seedItemId) : undefined;
  if (action === 'plant' && (!cropId || !seedItemId)) return null;
  return {
    id: String(raw.id ?? ''),
    name: String(raw.name ?? raw.id ?? 'Tool'),
    icon: String(raw.icon ?? '🔧'),
    color: String(raw.color ?? '#8fa3b8'),
    action,
    ...(cropId ? { cropId } : {}),
    ...(seedItemId ? { seedItemId } : {}),
  };
}

/** Hotbar order = file order of tools.yaml. */
export function loadToolDefs(state: State): ToolDef[] {
  return getDataRegistry(state)
    .all<Record<string, unknown>>('tool')
    .map(normalizeToolDef)
    .filter((d): d is ToolDef => d !== null);
}

let tools: ToolDef[] = [];
let active = 0;
let hotbarSignature = '';

export function activeTool(): ToolDef | null {
  return tools[active] ?? null;
}

export function activeToolIndex(): number {
  return active;
}

export function setActiveToolIndex(index: number): void {
  if (index < 0 || index >= tools.length) return;
  active = index;
}

export function setTools(defs: ToolDef[]): void {
  tools = defs;
  active = Math.min(active, Math.max(0, defs.length - 1));
}

/**
 * Push the current tools into the <Hotbar> when their visible content changed
 * (icon or owned count). The hotbar rebuilds its DOM on every setHotbarSlots,
 * so a stable signature keeps that to actual changes only.
 */
export function syncHotbar(state: State, player: number): void {
  const slots: HotbarSlotSpec[] = tools.map((tool) => ({
    icon: tool.icon,
    label: tool.name,
    color: tool.color,
    count: tool.seedItemId
      ? getItemQty(state, player, tool.seedItemId)
      : undefined,
  }));
  const signature = JSON.stringify(slots);
  if (signature === hotbarSignature) return;
  hotbarSignature = signature;
  setHotbarSlots(state, slots);
  setHotbarActive(state, active);
}

export interface ToolOutcome {
  ok: boolean;
  /** Player-facing key; empty when the action simply didn't apply. */
  message:
    | ''
    | 'stamina'
    | 'no-seeds'
    | 'tilled'
    | 'watered'
    | 'planted'
    | 'harvested'
    | 'cleared';
}

const gridQuery = defineQuery([FarmGrid]);

/**
 * Apply a tool to the tile the player faces. The facing cell may be off the
 * grid or the tile state may not match the action — both are silent no-ops.
 */
export function applyToolToFacingTile(
  state: State,
  player: number,
  tool: ToolDef
): ToolOutcome {
  const grids = gridQuery(state.world);
  const gridEid = grids[0];
  if (gridEid === undefined) return { ok: false, message: '' };
  const cell = getFacingCell(state, gridEid, player);
  if (!cell) return { ok: false, message: '' };
  const tile = getTileState(state, gridEid, cell.col, cell.row);
  if (!tile) return { ok: false, message: '' };

  switch (tool.action) {
    case 'till': {
      if (tile.state !== FarmTileStates.Empty)
        return { ok: false, message: '' };
      if (!trySpendStamina(STAMINA_COSTS.till))
        return { ok: false, message: 'stamina' };
      const done = tillTile(state, gridEid, cell.col, cell.row);
      return { ok: done, message: done ? 'tilled' : '' };
    }
    case 'water': {
      if (
        tile.state !== FarmTileStates.Tilled &&
        tile.state !== FarmTileStates.Growing
      ) {
        return { ok: false, message: '' };
      }
      if (!trySpendStamina(STAMINA_COSTS.water))
        return { ok: false, message: 'stamina' };
      const done = waterTile(state, gridEid, cell.col, cell.row);
      return { ok: done, message: done ? 'watered' : '' };
    }
    case 'plant': {
      if (tile.state !== FarmTileStates.Tilled)
        return { ok: false, message: '' };
      if (!tool.cropId || !tool.seedItemId) return { ok: false, message: '' };
      if (getItemQty(state, player, tool.seedItemId) < 1) {
        return { ok: false, message: 'no-seeds' };
      }
      if (!trySpendStamina(STAMINA_COSTS.plant))
        return { ok: false, message: 'stamina' };
      if (!plantSeed(state, gridEid, cell.col, cell.row, tool.cropId)) {
        return { ok: false, message: '' };
      }
      removeItem(state, player, tool.seedItemId, 1);
      return { ok: true, message: 'planted' };
    }
    case 'hand': {
      if (tile.state === FarmTileStates.Ready) {
        if (!trySpendStamina(STAMINA_COSTS.hand))
          return { ok: false, message: 'stamina' };
        const harvested = harvestTile(state, gridEid, cell.col, cell.row);
        if (!harvested) return { ok: false, message: '' };
        addItem(state, player, harvested.itemId, harvested.count);
        spawnFloatingText(state, `+${harvested.count} ${harvested.cropId}`, {
          x: Transform.posX[player],
          y: Transform.posY[player] + 1.6,
          z: Transform.posZ[player],
          duration: 1.2,
        });
        return { ok: true, message: 'harvested' };
      }
      if (tile.state === FarmTileStates.Withered) {
        if (!trySpendStamina(2)) return { ok: false, message: 'stamina' };
        const done = clearTile(state, gridEid, cell.col, cell.row);
        return { ok: done, message: done ? 'cleared' : '' };
      }
      return { ok: false, message: '' };
    }
  }
}
