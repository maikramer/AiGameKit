import { describe, expect, it } from 'bun:test';
import { ParticlesPlugin } from 'vibegame';
import type { Recipe } from 'vibegame';

describe('ParticlesPlugin recipes', () => {
  const recipes = ParticlesPlugin.recipes ?? [];

  it('regista duas recipes (ParticleSystem + ParticleBurst)', () => {
    expect(recipes).toHaveLength(2);
    expect(recipes.map((r: Recipe) => r.name)).toContain('ParticleSystem');
    expect(recipes.map((r: Recipe) => r.name)).toContain('ParticleBurst');
  });

  it('ParticleSystem recipe declara particle-emitter + transform', () => {
    const r = recipes.find((rec: Recipe) => rec.name === 'ParticleSystem');
    expect(r).toBeDefined();
    expect(r!.components).toContain('particle-emitter');
    expect(r!.components).toContain('transform');
  });

  it('ParticleBurst recipe marca burst=1 e looping=0', () => {
    const r = recipes.find((rec: Recipe) => rec.name === 'ParticleBurst');
    expect(r).toBeDefined();
    expect(r!.components).toContain('particle-emitter');
    expect(r!.components).toContain('transform');
    expect(r!.overrides).toMatchObject({
      'particle-emitter.burst': 1,
      'particle-emitter.looping': 0,
    });
  });
});

describe('ParticlesPlugin preset enum', () => {
  const presetEnum =
    ParticlesPlugin.config?.enums?.['particle-emitter']?.preset;

  it('mapeia todos os 9 presets para índices únicos', () => {
    expect(presetEnum).toBeDefined();
    expect(presetEnum!.fire).toBe(0);
    expect(presetEnum!.rain).toBe(1);
    expect(presetEnum!.snow).toBe(2);
    expect(presetEnum!.smoke).toBe(3);
    expect(presetEnum!.dust).toBe(4);
    expect(presetEnum!.explosion).toBe(5);
    expect(presetEnum!.sparks).toBe(6);
    expect(presetEnum!.magic).toBe(7);
    expect(presetEnum!.fireflies).toBe(8);
  });

  it('índices são todos distintos', () => {
    const values = Object.values(presetEnum ?? {});
    const unique = new Set(values);
    expect(unique.size).toBe(values.length);
  });
});

describe('ParticlesPlugin render-mode enum', () => {
  const renderEnum =
    ParticlesPlugin.config?.enums?.['particle-emitter']?.['render-mode'];

  it('mapeia os 4 render modes', () => {
    expect(renderEnum).toBeDefined();
    expect(renderEnum!.billboard).toBe(0);
    expect(renderEnum!.stretched).toBe(1);
    expect(renderEnum!.mesh).toBe(2);
    expect(renderEnum!.trail).toBe(3);
  });
});

describe('ParticlesPlugin defaults', () => {
  const defaults = ParticlesPlugin.config?.defaults?.['particle-emitter'];

  it('define active=1 e looping=1 por defeito', () => {
    expect(defaults).toBeDefined();
    expect(defaults!.active).toBe(1);
    expect(defaults!.looping).toBe(1);
  });

  it('define emissionRate=50 e duration=5', () => {
    expect(defaults!.emissionRate).toBe(50);
    expect(defaults!.duration).toBe(5);
  });

  it('define intervalos de vida, velocidade e tamanho', () => {
    expect(defaults!.startLifeMin).toBe(1);
    expect(defaults!.startLifeMax).toBe(3);
    expect(defaults!.startSpeedMin).toBe(1);
    expect(defaults!.startSpeedMax).toBe(5);
    expect(defaults!.startSizeMin).toBe(0.1);
    expect(defaults!.startSizeMax).toBe(0.5);
  });

  it('define cor inicial (R=1, G=0.5, B=0.1, A=1)', () => {
    expect(defaults!.startColorR).toBe(1);
    expect(defaults!.startColorG).toBe(0.5);
    expect(defaults!.startColorB).toBe(0.1);
    expect(defaults!.startColorA).toBe(1);
  });

  it('define burst=0 e burstCount=20', () => {
    expect(defaults!.burst).toBe(0);
    expect(defaults!.burstCount).toBe(20);
  });
});

describe('ParticlesPlugin estrutura', () => {
  it('regista componente particle-emitter', () => {
    expect(ParticlesPlugin.components).toBeDefined();
    expect(ParticlesPlugin.components!['particle-emitter']).toBeDefined();
    expect(
      ParticlesPlugin.components!['particle-emitter'].active
    ).toBeInstanceOf(Uint8Array);
  });

  it('tem pelo menos um sistema', () => {
    expect(ParticlesPlugin.systems).toBeDefined();
    expect(ParticlesPlugin.systems!.length).toBeGreaterThanOrEqual(1);
  });
});
