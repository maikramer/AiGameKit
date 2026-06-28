import { describe, expect, it } from 'bun:test';
import {
  EasingType,
  TweenAxis,
  TweenData,
  TweeningPlugin,
  TweenProcessingSystem,
} from 'vibegame/tweening';

describe('tweening: TweenAxis', () => {
  it('None começa em 0 e cada eixo tem valor distinto', () => {
    expect(TweenAxis.None).toBe(0);
    expect(TweenAxis.PosX).toBe(1);
    expect(TweenAxis.PosY).toBe(2);
    expect(TweenAxis.PosZ).toBe(3);
    expect(TweenAxis.RotX).toBe(4);
    expect(TweenAxis.RotY).toBe(5);
    expect(TweenAxis.RotZ).toBe(6);
  });

  it('não há valores duplicados no enum', () => {
    const values = [
      TweenAxis.None,
      TweenAxis.PosX,
      TweenAxis.PosY,
      TweenAxis.PosZ,
      TweenAxis.RotX,
      TweenAxis.RotY,
      TweenAxis.RotZ,
    ];
    expect(new Set(values).size).toBe(values.length);
  });

  it('eixos de posição são menores que eixos de rotação', () => {
    const maxPos = Math.max(TweenAxis.PosX, TweenAxis.PosY, TweenAxis.PosZ);
    const minRot = Math.min(TweenAxis.RotX, TweenAxis.RotY, TweenAxis.RotZ);
    expect(maxPos).toBeLessThan(minRot);
  });
});

describe('tweening: EasingType', () => {
  it('expõe Linear=0, EaseInOut=1, EaseOutQuad=2', () => {
    expect(EasingType.Linear).toBe(0);
    expect(EasingType.EaseInOut).toBe(1);
    expect(EasingType.EaseOutQuad).toBe(2);
  });

  it('tem 3 valores distintos', () => {
    const values = [
      EasingType.Linear,
      EasingType.EaseInOut,
      EasingType.EaseOutQuad,
    ];
    expect(new Set(values).size).toBe(values.length);
  });
});

describe('tweening: TweenData component', () => {
  it('targetEntity é Uint32Array (referência de entidade)', () => {
    expect(TweenData.targetEntity).toBeInstanceOf(Uint32Array);
  });

  it('flags discretas são Uint8Array', () => {
    expect(TweenData.axis).toBeInstanceOf(Uint8Array);
    expect(TweenData.easing).toBeInstanceOf(Uint8Array);
    expect(TweenData.loop).toBeInstanceOf(Uint8Array);
    expect(TweenData.pingPong).toBeInstanceOf(Uint8Array);
    expect(TweenData.active).toBeInstanceOf(Uint8Array);
  });

  it('campos contínuos são Float32Array', () => {
    expect(TweenData.from).toBeInstanceOf(Float32Array);
    expect(TweenData.to).toBeInstanceOf(Float32Array);
    expect(TweenData.duration).toBeInstanceOf(Float32Array);
    expect(TweenData.delay).toBeInstanceOf(Float32Array);
    expect(TweenData.elapsed).toBeInstanceOf(Float32Array);
  });

  it('todos os arrays têm o mesmo comprimento (MAX_ENTITIES)', () => {
    const length = TweenData.active.length;
    expect(length).toBeGreaterThan(0);
    for (const key of Object.keys(TweenData) as (keyof typeof TweenData)[]) {
      expect(TweenData[key].length).toBe(length);
    }
  });
});

describe('tweening: TweeningPlugin', () => {
  it('registra exatamente um recipe chamado "Tween"', () => {
    expect(TweeningPlugin.recipes).toHaveLength(1);
    expect(TweeningPlugin.recipes?.[0]?.name).toBe('Tween');
  });

  it('declara parserAttributes que cobrem todos os campos do Tween', () => {
    const attrs = TweeningPlugin.recipes?.[0]?.parserAttributes;
    expect(attrs).toEqual(
      expect.arrayContaining([
        'target',
        'attr',
        'from',
        'to',
        'duration',
        'delay',
        'loop',
        'easing',
        'ping-pong',
      ])
    );
  });

  it('liga o componente "tween-data" ao TweenData', () => {
    expect(TweeningPlugin.components?.['tween-data']).toBe(TweenData);
  });

  it('expõe o parser da tag <Tween>', () => {
    expect(TweeningPlugin.config?.parsers?.Tween).toBeTypeOf('function');
  });

  it('registra TweenProcessingSystem', () => {
    expect(TweeningPlugin.systems).toContain(TweenProcessingSystem);
  });
});

describe('tweening: TweenProcessingSystem', () => {
  it('roda no grupo fixed', () => {
    expect(TweenProcessingSystem.group).toBe('fixed');
  });

  it('expõe callback de update', () => {
    expect(TweenProcessingSystem.update).toBeTypeOf('function');
  });
});
