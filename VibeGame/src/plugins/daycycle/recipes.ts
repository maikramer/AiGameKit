import type { Recipe } from '../../core';

/**
 * `<DayCycle day="1" season="spring" minute-of-day="360" minutes-per-real-second="1.2" days-per-season="28" drive-sky="1" drive-ambient="1">`
 *
 * All attributes are plain `game-clock` fields (auto-routed); `season` is an
 * enum (`spring|summer|autumn|fall|winter`). Top-level or inside a group —
 * it is a plain entity, no merge semantics.
 */
export const dayCycleRecipe: Recipe = {
  name: 'DayCycle',
  components: ['game-clock'],
};

/** `<Clock>` HUD widget (child of `<HudScreenLayer>`). */
export const clockWidgetRecipe: Recipe = {
  name: 'Clock',
  components: [],
  parserAttributes: ['icon', 'position'],
  parserOwnsChildren: true,
};
