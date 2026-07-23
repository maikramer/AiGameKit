import { describe, expect, it } from 'bun:test';
import {
  CRACK_STYLE_VERTICAL,
  CRACK_STYLE_VORONOI,
  DestructibleFxSystem,
} from '../../../src/plugins/destructible/fx';
import { DestructiblePlugin } from '../../../src/plugins/destructible/plugin';
import { DestructibleSystem } from '../../../src/plugins/destructible/systems';
import { Destructible } from '../../../src/plugins/destructible/components';

describe('DestructiblePlugin registration', () => {
  it('registers DestructibleSystem and DestructibleFxSystem', () => {
    expect(DestructiblePlugin.systems).toHaveLength(2);
    expect(DestructiblePlugin.systems![0]).toBe(DestructibleSystem);
    expect(DestructiblePlugin.systems![1]).toBe(DestructibleFxSystem);
  });

  it('maps destructible component', () => {
    expect(DestructiblePlugin.components!.destructible).toBe(Destructible);
  });

  const defaults = DestructiblePlugin.config!.defaults!.destructible as Record<
    string,
    number
  >;

  for (const [key, value] of Object.entries(defaults)) {
    it(`default destructible.${key} is ${value}`, () => {
      expect(defaults[key]).toBe(value);
    });
  }

  const breakStyle = DestructiblePlugin.config!.enums!.destructible
    .breakStyle as Record<string, number>;
  for (const [name, code] of Object.entries(breakStyle)) {
    it(`breakStyle enum ${name} = ${code}`, () => {
      expect(breakStyle[name]).toBe(code);
    });
  }

  const crackStyle = DestructiblePlugin.config!.enums!.destructible
    .crackStyle as Record<string, number>;
  it('crackStyle voronoi matches CRACK_STYLE_VORONOI', () => {
    expect(crackStyle.voronoi).toBe(CRACK_STYLE_VORONOI);
  });
  it('crackStyle vertical matches CRACK_STYLE_VERTICAL', () => {
    expect(crackStyle.vertical).toBe(CRACK_STYLE_VERTICAL);
  });

  const presetEnum = DestructiblePlugin.config!.enums!.destructible
    .preset as Record<string, number>;
  for (let i = 0; i < 13; i++) {
    it(`particle preset enum includes index ${i}`, () => {
      const names = Object.values(presetEnum);
      expect(names).toContain(i);
    });
  }
});
