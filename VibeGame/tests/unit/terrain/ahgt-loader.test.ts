import { describe, expect, it } from 'bun:test';
import { loadHeightfield } from '../../../src/plugins/terrain/ahgt-loader';
import { serializeAhgt } from '../../../src/plugins/terrain/ahgt-format';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

function makeSampler(): HeightSampler {
  const size = 16;
  const data = new Float32Array(size * size);
  for (let i = 0; i < data.length; i++) data[i] = 0.3 + (i % 5) * 0.02;
  return { width: size, height: size, data, worldSize: 50, maxHeight: 20 };
}

describe('loadHeightfield dispatch', () => {
  it('parses an .ahgt blob fetched by URL (mocked fetch)', async () => {
    const original = makeSampler();
    const bytes = serializeAhgt(original);
    // Stub global fetch so the test does not depend on data: URL support
    // (Bun's fetch rejects data: URLs carrying a hash fragment, which browsers
    // accept; the loader itself is browser-first).
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async (_url: string) =>
      new Response(bytes.buffer as ArrayBuffer, {
        status: 200,
      })) as typeof fetch;
    try {
      const sampler = await loadHeightfield('https://example.com/foo.ahgt');
      expect(sampler.width).toBe(original.width);
      expect(sampler.height).toBe(original.height);
      expect(
        Math.abs(sampleHeightAt(sampler, 0, 0) - sampleHeightAt(original, 0, 0))
      ).toBeLessThan(original.maxHeight / 65535 + 1e-6);
    } finally {
      globalThis.fetch = realFetch;
    }
  });

  it('sniffs AHGT magic for an extensionless blob (mocked fetch)', async () => {
    const original = makeSampler();
    const bytes = serializeAhgt(original);
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async (_url: string) =>
      new Response(bytes.buffer as ArrayBuffer, {
        status: 200,
      })) as typeof fetch;
    try {
      // Extensionless URL — loader must fall back to magic-byte sniffing.
      const sampler = await loadHeightfield(
        'https://example.com/terrain',
        original.worldSize,
        original.maxHeight
      );
      expect(sampler.width).toBe(original.width);
    } finally {
      globalThis.fetch = realFetch;
    }
  });

  it('rejects an unknown extension with a clear error', async () => {
    await expect(
      loadHeightfield('https://example.com/foo.unknown')
    ).rejects.toThrow(/heightmap format|extension|Unknown/i);
  });
});
