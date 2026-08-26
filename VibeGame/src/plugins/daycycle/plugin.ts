import type { Plugin } from '../../core';
import { GameClock } from './components';
import { clockWidgetRecipe, dayCycleRecipe } from './recipes';
import { DayCycleSkySystem, DayCycleSystem } from './systems';
import { SEASON_ENUM } from './calendar';
import { registerClockSerializer } from './serializer';
import { CLOCK_TAG, clockWidgetParser, registerClockHudWidget } from './hud';

/**
 * Opt-in day/night calendar — NOT in DefaultPlugins (costs nothing unless a
 * game asks for it; same policy as SaveLoadPlugin/ChronoPlugin).
 *
 * ```ts
 * withPlugin(DayCyclePlugin);
 * ```
 * ```html
 * <DayCycle minute-of-day="360" minutes-per-real-second="1.2"></DayCycle>
 * ```
 */
export const DayCyclePlugin: Plugin = {
  systems: [DayCycleSystem, DayCycleSkySystem],
  recipes: [dayCycleRecipe, clockWidgetRecipe],
  components: { GameClock },
  config: {
    defaults: {
      'game-clock': {
        minuteOfDay: 360,
        day: 1,
        year: 1,
        season: 0,
        daysPerSeason: 28,
        minutesPerRealSecond: 1.2,
        paused: 0,
        wakeMinute: 360,
        dawnMinute: 300, // 05:00
        duskMinute: 1200, // 20:00
        sunAzimuthBase: 135,
        maxSunElevation: 62,
        minSunElevation: 2,
        driveSky: 1,
        skyStepDeg: 2,
        driveAmbient: 1,
        ambientDayIntensity: 0.32,
        ambientNightIntensity: 0.08,
      },
    },
    enums: {
      'game-clock': {
        season: SEASON_ENUM,
      },
    },
    parsers: {
      [CLOCK_TAG]: clockWidgetParser,
    },
  },
  initialize(state) {
    registerClockSerializer(state);
    registerClockHudWidget();
  },
};
