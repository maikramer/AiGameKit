import {
  defineComponent,
  F32,
  U8,
  U16,
} from '../../core/ecs/component-storage';

/**
 * The game's calendar: a wall clock the simulation advances, plus the day/
 * season/year rolls derived from it. Deliberately engine-level — it animates
 * engine subsystems (`<Sky>` sun angles, `AmbientLight`) that otherwise have
 * no animator; games read it and hang their own rules (crop growth, shop
 * hours) off its events.
 */
export const GameClock = defineComponent({
  /** Minutes since midnight [0, 1440). The authority — everything derives. */
  minuteOfDay: F32,
  /** Day of season, 1-based. */
  day: U16,
  /** Year, 1-based. */
  year: U16,
  /** 0 spring · 1 summer · 2 autumn · 3 winter. */
  season: U8,
  /** Calendar length. Harvest Moon pacing: 28. */
  daysPerSeason: U8,
  /** In-game minutes per real second. 1.2 ⇒ a 20-minute day. */
  minutesPerRealSecond: F32,
  /** 1 freezes the clock (menus, cutscenes). */
  paused: U8,
  /** Minute the hero wakes to (sleepUntilMorning target). */
  wakeMinute: F32,
  /** First/last minute of daylight — shapes the sun arc. */
  dawnMinute: F32,
  duskMinute: F32,
  /** Sun arc shaping, degrees. */
  sunAzimuthBase: F32,
  maxSunElevation: F32,
  minSunElevation: F32,
  /** 1 = write the sun into the first <Sky>. */
  driveSky: U8,
  /**
   * Quantization step (degrees) for sky writes. ProceduralSkySystem rebuilds
   * its PMREM environment whenever elevation/azimuth change — without
   * quantization that is a full cube render per frame.
   */
  skyStepDeg: F32,
  /** 1 = drive the first ambient light's intensity through the day. */
  driveAmbient: U8,
  ambientDayIntensity: F32,
  ambientNightIntensity: F32,
});
