import type { Plugin } from '../../core';
import { FarmGrid } from './components';
import { farmPlotRecipe } from './recipes';
import {
  FarmGridSetupSystem,
  FarmHighlightSystem,
  FarmRenderSystem,
  farmPlotParser,
} from './systems';
import { farmGridSerializer } from './serializer';
import { registerSaveSerializer } from '../save-load/serializer-registry';

/**
 * Opt-in farming grid plugin — NOT in DefaultPlugins.
 *
 * ```ts
 * withPlugin(FarmPlotPlugin);
 * ```
 * ```html
 * <FarmPlot at="0 16" size="24 18" cell-size="1" base-y="12"></FarmPlot>
 * ```
 *
 * Crop definitions load into the DataRegistry as `kind: crop` (YAML fetched
 * by the game and fed via `getDataRegistry(state).loadYaml(...)` before
 * `runtime.start()` — the setup system interns them once).
 */
export const FarmPlotPlugin: Plugin = {
  systems: [FarmGridSetupSystem, FarmRenderSystem, FarmHighlightSystem],
  recipes: [farmPlotRecipe],
  components: { FarmGrid },
  config: {
    defaults: {
      'farm-grid': {
        originX: 0,
        originZ: 0,
        cellSize: 1,
        baseY: 0,
        surfaceEpsilon: 0.02,
        cols: 0,
        rows: 0,
        version: 0,
      },
    },
    parsers: {
      FarmPlot: farmPlotParser,
    },
  },
  initialize(state) {
    // The registry is a module WeakMap — inert unless SaveLoadPlugin collects.
    registerSaveSerializer(state, 'farm-grid', farmGridSerializer);
  },
};
