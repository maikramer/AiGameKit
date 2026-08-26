export { FarmGrid } from './components';
export { FarmPlotPlugin } from './plugin';
export { farmPlotRecipe } from './recipes';
export {
  FarmGridSetupSystem,
  FarmHighlightSystem,
  FarmRenderSystem,
  farmPlotParser,
  getFacingCell,
} from './systems';
export {
  cellIndex,
  cellToWorld,
  facingCellFrom,
  quantizeForward,
  worldToCell,
} from './grid';
export type { CellRef, GridSpec } from './grid';
export {
  FarmTileStates,
  advanceFarmDay,
  clearTile,
  harvestTile,
  normalizeCropDef,
  plantSeed,
  stageForGrowth,
  tillTile,
  totalGrowthDays,
  waterTile,
} from './crops';
export type {
  CropDef,
  FarmDayReport,
  FarmTileListener,
  FarmTileState,
  HarvestYield,
} from './crops';
export { createFarmGrid, getFarmGrid, getTileState } from './api';
export type { FarmGridData, FarmTile } from './store';
export { onFarmTileChanged } from './crops';
export { buildProceduralCropGeometry } from './render';
export { deserializeFarmGrid, serializeFarmGrid } from './serializer';
export type { FarmGridSave } from './serializer';
