/** Season enum + display names. Numeric order is the calendar order. */
export const SEASON_COUNT = 4;

export type Season = 0 | 1 | 2 | 3;

export const SEASON_NAMES: readonly string[] = [
  'spring',
  'summer',
  'autumn',
  'winter',
] as const;

/** `fall` is an alias of `autumn` — both authors and saves use either. */
export const SEASON_ENUM: Record<string, number> = {
  spring: 0,
  summer: 1,
  autumn: 2,
  fall: 2,
  winter: 3,
};
