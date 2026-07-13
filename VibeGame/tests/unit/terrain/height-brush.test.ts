import { describe, expect, it } from 'bun:test';
import {
  applyHeightBrush,
  minEffectiveFalloff,
  samplerTexelStep,
} from '../../../src/plugins/terrain/height-brush';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';

function makeSampler(size = 33, worldSize = 32, fill = 0.5): HeightSampler {
  return {
    data: new Float32Array(size * size).fill(fill),
    width: size,
    height: size,
    worldSize,
    maxHeight: 100,
  } as HeightSampler;
}

describe('height-brush: samplerTexelStep / minEffectiveFalloff', () => {
  it('texel step = worldSize/(width-1)', () => {
    expect(samplerTexelStep(makeSampler(33, 32))).toBeCloseTo(1);
    expect(samplerTexelStep(makeSampler(17, 32))).toBeCloseTo(2);
  });

  it('clampa o falloff a 1.5 texels por defeito', () => {
    const s = makeSampler(17, 32); // texel 2 m
    expect(minEffectiveFalloff(s, 0.5)).toBeCloseTo(3);
    expect(minEffectiveFalloff(s, 10)).toBeCloseTo(10);
  });
});

describe('height-brush: applyHeightBrush', () => {
  it('modo blend escreve lerp(atual→alvo, weight)', () => {
    const s = makeSampler();
    const changed = applyHeightBrush(s, {
      minX: -2,
      maxX: 2,
      minZ: -2,
      maxZ: 2,
      evalAt: () => ({ targetY: 100, weight: 0.5 }),
    });
    expect(changed).toBe(true);
    // centro do sampler (0,0) → índice do meio
    const mid = Math.floor(s.height / 2) * s.width + Math.floor(s.width / 2);
    expect(s.data![mid]!).toBeCloseTo(0.75); // 0.5 + (1.0-0.5)*0.5
  });

  it('modo lower nunca sobe; modo raise nunca desce', () => {
    const low = makeSampler();
    applyHeightBrush(low, {
      minX: -2,
      maxX: 2,
      minZ: -2,
      maxZ: 2,
      mode: 'lower',
      evalAt: () => ({ targetY: 100, weight: 1 }), // alvo ACIMA do atual
    });
    expect(Math.max(...low.data!)).toBeCloseTo(0.5); // intocado

    const high = makeSampler();
    applyHeightBrush(high, {
      minX: -2,
      maxX: 2,
      minZ: -2,
      maxZ: 2,
      mode: 'raise',
      evalAt: () => ({ targetY: 0, weight: 1 }), // alvo ABAIXO do atual
    });
    expect(Math.min(...high.data!)).toBeCloseTo(0.5); // intocado
  });

  it('expande o AABB em 1 texel (cantos nunca ficam de fora)', () => {
    const s = makeSampler(33, 32); // texel 1 m; grelha em [-16..16]
    const visited: number[] = [];
    applyHeightBrush(s, {
      minX: 0.4,
      maxX: 0.6,
      minZ: 0.4,
      maxZ: 0.6,
      evalAt: (x, z) => {
        visited.push(x, z);
        return null;
      },
    });
    // AABB sub-texel ainda visita texels vizinhos (0 e 1 em ambos os eixos ±1)
    const xs = new Set<number>();
    for (let i = 0; i < visited.length; i += 2) xs.add(visited[i]!);
    expect(xs.size).toBeGreaterThanOrEqual(3);
    expect(Math.min(...xs)).toBeLessThanOrEqual(0);
    expect(Math.max(...xs)).toBeGreaterThanOrEqual(1);
  });

  it('devolve false num sampler sem data', () => {
    const s = makeSampler();
    (s as { data: Float32Array | null }).data = null;
    expect(
      applyHeightBrush(s, {
        minX: -1,
        maxX: 1,
        minZ: -1,
        maxZ: 1,
        evalAt: () => ({ targetY: 50, weight: 1 }),
      })
    ).toBe(false);
  });

  it('clampa alvo a [0, maxHeight] no espaço normalizado', () => {
    const s = makeSampler();
    applyHeightBrush(s, {
      minX: -1,
      maxX: 1,
      minZ: -1,
      maxZ: 1,
      evalAt: () => ({ targetY: 9999, weight: 1 }),
    });
    expect(Math.max(...s.data!)).toBeLessThanOrEqual(1);
  });
});
