import type { Recipe } from '../../core';

/**
 * `<FarmPlot at="0 16" size="24 18" cell-size="1" base-y="12">`
 *
 * `at` is the CENTRE of cell (0,0) (the grid's north-west corner), matching
 * how `<TerrainPad at>` names the centre of its footprint. `size` is in
 * metres and snaps to whole cells. `base-y` omitted/0 = sample the ground
 * once at setup.
 */
export const farmPlotRecipe: Recipe = {
  name: 'FarmPlot',
  components: ['farm-grid', 'transform'],
  parserAttributes: ['at', 'size', 'cell-size', 'base-y'],
};
