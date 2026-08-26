import { beforeEach, describe, expect, it } from 'bun:test';
import {
  DAY_ADVANCED,
  DayCyclePlugin,
  GameClock,
  SEASON_CHANGED,
  State,
  YEAR_CHANGED,
  advanceGameDay,
  formatClock,
  getClockEntity,
  getTimeOfDay,
  onClockEvent,
  setClockPaused,
  setClockScale,
  sleepUntilMorning,
  sunAngles,
} from 'vibegame';
// Systems live one level below the public barrel (API-only surface).
import {
  DayCycleSkySystem,
  DayCycleSystem,
} from '../../../src/plugins/daycycle/systems';
import { ProceduralSky } from '../../../src/plugins/sky';
import { AmbientLight } from '../../../src/plugins/rendering';

function boot() {
  const state = new State();
  state.registerPlugin(DayCyclePlugin);
  const clock = state.createEntity();
  state.addComponent(clock, GameClock);
  return { state, clock };
}

/** Drive N frames of `dt` seconds through the simulation system. */
function run(state: State, seconds: number, dt = 1 / 60): void {
  const frames = Math.round(seconds / dt);
  for (let i = 0; i < frames; i++) {
    state.time.deltaTime = dt;
    DayCycleSystem.update!(state);
  }
}

describe('daycycle clock', () => {
  let state: State;
  let clock: number;

  beforeEach(() => {
    ({ state, clock } = boot());
  });

  it('advances minutes at minutes-per-real-second', () => {
    const start = GameClock.minuteOfDay[clock];
    run(state, 10, 1); // 10 s in one frame, scale 1.2 ⇒ +12 min
    // F32 storage: compare at 1e-3.
    expect(GameClock.minuteOfDay[clock] - start).toBeCloseTo(12, 3);
  });

  it('rolls the day at midnight and fires DAY_ADVANCED', () => {
    GameClock.minuteOfDay[clock] = 1439;
    const fired: string[] = [];
    onClockEvent(state, DAY_ADVANCED, (eid) => {
      expect(eid).toBe(clock);
      fired.push(DAY_ADVANCED);
    });
    run(state, 2, 1); // +2.4 min crosses midnight
    expect(GameClock.day[clock]).toBe(2);
    expect(GameClock.minuteOfDay[clock]).toBeLessThan(5);
    expect(fired).toEqual([DAY_ADVANCED]);
  });

  it('rolls season and year through the calendar', () => {
    GameClock.day[clock] = 28; // last day of spring
    const fired: string[] = [];
    onClockEvent(state, SEASON_CHANGED, () => fired.push(SEASON_CHANGED));
    advanceGameDay(state);
    expect(GameClock.day[clock]).toBe(1);
    expect(GameClock.season[clock]).toBe(1); // summer
    expect(fired).toEqual([SEASON_CHANGED]);

    GameClock.season[clock] = 3;
    GameClock.day[clock] = 28;
    const years: string[] = [];
    onClockEvent(state, YEAR_CHANGED, () => years.push(YEAR_CHANGED));
    advanceGameDay(state);
    expect(GameClock.season[clock]).toBe(0);
    expect(GameClock.year[clock]).toBe(2);
    expect(years).toEqual([YEAR_CHANGED]);
  });

  it('paused freezes the clock', () => {
    setClockPaused(state, true);
    const start = GameClock.minuteOfDay[clock];
    run(state, 5, 1);
    expect(GameClock.minuteOfDay[clock]).toBe(start);
    setClockPaused(state, false);
    run(state, 5, 1);
    expect(GameClock.minuteOfDay[clock]).toBeGreaterThan(start);
  });

  it('setClockScale changes the pace', () => {
    setClockScale(state, 10);
    const start = GameClock.minuteOfDay[clock];
    run(state, 1, 1);
    expect(GameClock.minuteOfDay[clock] - start).toBeCloseTo(10, 5);
  });

  it('sleepUntilMorning lands on the wake minute of the next day', () => {
    GameClock.minuteOfDay[clock] = 22 * 60; // 22:00
    GameClock.wakeMinute[clock] = 360; // 06:00
    const fired: string[] = [];
    onClockEvent(state, DAY_ADVANCED, () => fired.push(DAY_ADVANCED));
    const events = sleepUntilMorning(state);
    expect(GameClock.minuteOfDay[clock]).toBeCloseTo(360, 5);
    expect(GameClock.day[clock]).toBe(2);
    expect(events).toContain(DAY_ADVANCED);
    expect(fired).toEqual([DAY_ADVANCED]);

    // Already at wake ⇒ sleeps into the day after.
    sleepUntilMorning(state);
    expect(GameClock.day[clock]).toBe(3);
  });

  it('getTimeOfDay snapshots the calendar', () => {
    GameClock.minuteOfDay[clock] = 750;
    GameClock.day[clock] = 4;
    GameClock.season[clock] = 2;
    const snap = getTimeOfDay(state)!;
    expect(snap).toMatchObject({
      minuteOfDay: 750,
      day: 4,
      season: 2,
      seasonName: 'autumn',
      year: 1,
      daysPerSeason: 28,
      paused: false,
    });
  });

  it('formatClock renders HH:MM', () => {
    GameClock.minuteOfDay[clock] = 402;
    expect(formatClock(state)).toBe('06:42');
    GameClock.minuteOfDay[clock] = 0;
    expect(formatClock(state)).toBe('00:00');
  });

  it('getClockEntity finds the clock (0 when absent)', () => {
    expect(getClockEntity(state)).toBe(clock);
    const bare = new State();
    expect(getClockEntity(bare)).toBe(0);
  });
});

describe('sunAngles (pure)', () => {
  const arc = {
    dawnMinute: 300,
    duskMinute: 1200,
    sunAzimuthBase: 135,
    maxSunElevation: 62,
    minSunElevation: 2,
  };

  it('peaks at solar noon', () => {
    const noon = (300 + 1200) / 2;
    const angles = sunAngles(noon, arc);
    expect(angles.elevation).toBeCloseTo(62, 3);
  });

  it('touches the horizon minimum at dawn and dusk', () => {
    expect(sunAngles(300, arc).elevation).toBeCloseTo(2, 3);
    expect(sunAngles(1200, arc).elevation).toBeCloseTo(2, 3);
  });

  it('dips below the horizon through the night, continuously', () => {
    const midnight = sunAngles(0, arc);
    expect(midnight.elevation).toBeLessThan(0);
    // Dusk joins the day arc without a jump.
    const justBefore = sunAngles(1199.9, arc).elevation;
    const justAfter = sunAngles(1200.1, arc).elevation;
    expect(Math.abs(justBefore - justAfter)).toBeLessThan(0.2);
  });

  it('sweeps azimuth east→west over the daylight span', () => {
    let prev = sunAngles(300, arc).azimuth;
    for (let m = 360; m <= 1200; m += 60) {
      const a = sunAngles(m, arc).azimuth;
      expect(a).toBeGreaterThan(prev);
      prev = a;
    }
    // base−90 at dawn, base at solar noon, base+90 at dusk.
    expect(sunAngles(300, arc).azimuth).toBeCloseTo(45, 3);
    expect(sunAngles(750, arc).azimuth).toBeCloseTo(135, 3);
    expect(sunAngles(1200, arc).azimuth).toBeCloseTo(225, 3);
  });

  it('wraps minute inputs outside [0,1440)', () => {
    expect(sunAngles(750 + 1440, arc)).toEqual(sunAngles(750, arc));
    expect(sunAngles(750 - 1440, arc)).toEqual(sunAngles(750, arc));
  });
});

describe('DayCycleSkySystem', () => {
  it('drives the first sky and ambient light', () => {
    const state = new State();
    state.registerPlugin(DayCyclePlugin);
    const clock = state.createEntity();
    state.addComponent(clock, GameClock);
    GameClock.driveSky[clock] = 1;
    GameClock.driveAmbient[clock] = 1;
    GameClock.minuteOfDay[clock] = 750; // noon
    GameClock.skyStepDeg[clock] = 2;

    const sky = state.createEntity();
    state.addComponent(sky, ProceduralSky);
    const ambient = state.createEntity();
    state.addComponent(ambient, AmbientLight);
    GameClock.ambientDayIntensity[clock] = 0.4;
    GameClock.ambientNightIntensity[clock] = 0.1;

    DayCycleSkySystem.update!(state);

    expect(ProceduralSky.sunElevation[sky]).toBeCloseTo(62, 3);
    expect(AmbientLight.intensity[ambient]).toBeCloseTo(0.4, 3);

    GameClock.minuteOfDay[clock] = 0; // deep night
    DayCycleSkySystem.update!(state);
    expect(ProceduralSky.sunElevation[sky]).toBeLessThan(0);
    expect(AmbientLight.intensity[ambient]).toBeCloseTo(0.1, 2);
  });

  it('quantization: 100 sub-band frames ⇒ at most 1 sky write (PMREM brake)', () => {
    const state = new State();
    state.registerPlugin(DayCyclePlugin);
    const clock = state.createEntity();
    state.addComponent(clock, GameClock);
    GameClock.minuteOfDay[clock] = 400;
    GameClock.skyStepDeg[clock] = 2;
    const sky = state.createEntity();
    state.addComponent(sky, ProceduralSky);

    // Sub-band drift: 100 frames × 0.02 min ≈ 0.4° of elevation — safely
    // inside one 2° band, so after the initial write the sky stays untouched.
    // ProceduralSkySystem rebuilds its PMREM cube on every change of these
    // fields; unquantized, that would be 100 cube renders.
    let writes = 0;
    let last = ProceduralSky.sunElevation[sky];
    for (let i = 0; i < 100; i++) {
      GameClock.minuteOfDay[clock] += 0.02;
      DayCycleSkySystem.update!(state);
      const now = ProceduralSky.sunElevation[sky];
      if (now !== last) {
        writes++;
        last = now;
      }
    }
    expect(writes).toBeLessThanOrEqual(1);

    // Same drift with quantization off rewrites the field every frame —
    // the failure mode skyStepDeg exists to prevent.
    GameClock.skyStepDeg[clock] = 0;
    GameClock.minuteOfDay[clock] = 400;
    ProceduralSky.sunElevation[sky] = 0;
    let rawWrites = 0;
    last = ProceduralSky.sunElevation[sky];
    for (let i = 0; i < 100; i++) {
      GameClock.minuteOfDay[clock] += 0.02;
      DayCycleSkySystem.update!(state);
      if (ProceduralSky.sunElevation[sky] !== last) {
        rawWrites++;
        last = ProceduralSky.sunElevation[sky];
      }
    }
    expect(rawWrites).toBe(100);
  });
});
