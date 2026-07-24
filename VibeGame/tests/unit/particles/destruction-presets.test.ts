import { describe, expect, it } from 'bun:test';
import { ParticlesPlugin } from 'vibegame';
import {
  createPresetParams,
  presetIndex,
  presetName,
  PRESET_NAMES,
} from '../../../src/plugins/particles/presets';

describe('destruction particle presets', () => {
  it('woodchips/rockshards/leaves/ground-dust anexados no fim (índices estáveis)', () => {
    expect(presetIndex('woodchips')).toBe(10);
    expect(presetIndex('rockshards')).toBe(11);
    expect(presetIndex('leaves')).toBe(12);
    expect(presetIndex('ground-dust')).toBe(13);
    expect(presetIndex('sand-dust')).toBe(13);
    expect(presetName(10)).toBe('woodchips');
    expect(presetName(11)).toBe('rockshards');
    expect(presetName(12)).toBe('leaves');
    expect(presetName(13)).toBe('ground-dust');
  });

  it('cada preset novo tem factory one-shot com burst', () => {
    for (const name of ['woodchips', 'rockshards', 'leaves'] as const) {
      const params = createPresetParams(name);
      expect(params.looping).toBe(false);
      expect(params.autoDestroy).toBe(true);
      expect(params.emissionBursts?.length).toBe(1);
    }
  });

  it('ground-dust é looping rasteiro (sheet ambient)', () => {
    const params = createPresetParams('ground-dust');
    expect(params.looping).toBe(true);
    expect(params.autoDestroy).toBeFalsy();
    expect(params.shape).toBeDefined();
  });

  it('enum do plugin cobre todos os PRESET_NAMES com índices corretos', () => {
    const presetEnum =
      ParticlesPlugin.config?.enums?.['particle-emitter']?.preset;
    expect(presetEnum).toBeDefined();
    PRESET_NAMES.forEach((name, i) => {
      expect(presetEnum![name]).toBe(i);
    });
  });
});
