import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { DefaultPlugins } from 'vibegame/defaults';
import { RacingPlugin } from '../../../src/plugins/racing/plugin';
import { RaceConditionsSystem } from '../../../src/plugins/racing/conditions';
import {
  START_LIGHT_COUNT,
  StartLightsSystem,
  startLightPattern,
} from '../../../src/plugins/racing/start-lights';
import { TrackSpawnSystem } from '../../../src/plugins/racing/track-spawn';
import type { System } from '../../../src/core/ecs/types';

describe('startLightPattern', () => {
  it('lights one red per countdown second (3 → 1, then 2, then 3)', () => {
    expect(startLightPattern('countdown', 3, 0)).toEqual({
      lit: 1,
      green: false,
    });
    expect(startLightPattern('countdown', 2, 0)).toEqual({
      lit: 2,
      green: false,
    });
    expect(startLightPattern('countdown', 1, 0)).toEqual({
      lit: START_LIGHT_COUNT,
      green: false,
    });
    expect(startLightPattern('countdown', 0.2, 0).lit).toBe(START_LIGHT_COUNT);
  });

  it('goes all-green for a beat after the flag, then off', () => {
    expect(startLightPattern('racing', 0, 0.2)).toEqual({
      lit: START_LIGHT_COUNT,
      green: true,
    });
    expect(startLightPattern('racing', 0, 1)).toEqual({ lit: 0, green: false });
  });

  it('stays dark on the grid and after the race', () => {
    expect(startLightPattern('grid', 3, 0)).toEqual({ lit: 0, green: false });
    expect(startLightPattern('finished', 0, 90)).toEqual({
      lit: 0,
      green: false,
    });
  });
});

describe('StartLightsSystem scheduling', () => {
  it('stays in draw; TrackSpawnSystem stays in simulation (no cross-group after)', () => {
    expect(StartLightsSystem.group).toBe('draw');
    expect(TrackSpawnSystem.group).toBe('simulation');
    expect(RaceConditionsSystem.group).toBe('draw');
    expect(RaceConditionsSystem.after ?? []).not.toContain('TrackSpawnSystem');
    const state = new State();
    state.registerPlugin(RacingPlugin);
    expect(() => state.step(0.016)).not.toThrow();
  });

  it('RacingPlugin after/before stay inside the same group', () => {
    const systems = RacingPlugin.systems ?? [];
    const byName = new Map<string, System>();
    for (const s of systems) {
      if (s.name) byName.set(s.name, s);
    }
    const groupOf = (s: System) => s.group ?? 'simulation';
    for (const s of systems) {
      const g = groupOf(s);
      for (const dep of [...(s.after ?? []), ...(s.before ?? [])]) {
        const name = typeof dep === 'string' ? dep : dep.name;
        const target = typeof dep === 'string' ? byName.get(dep) : dep;
        if (!target || !name) continue;
        expect(groupOf(target), `${s.name} → ${name}`).toBe(g);
      }
    }
  });

  it('DefaultPlugins (simple-rpg path) sort without cross-group after', () => {
    const state = new State();
    for (const plugin of DefaultPlugins) {
      state.registerPlugin(plugin);
    }
    expect(() => state.step(0.016)).not.toThrow();
  });
});
