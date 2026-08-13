import { describe, expect, it } from 'bun:test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Roadside oaks/pines sit at ~14–18 m past the barrier. A 5 m flatten-falloff
 * ends the carve as a cliff; those trees plant on the uncut lip and read as
 * floating above the track. The bed must grade a talude that actually reaches
 * them (~16 m).
 */
describe('simple-racer circuit bed falloff', () => {
  it('flatten-falloff cobre o talude das árvores da berma', () => {
    const xml = readFileSync(
      resolve(
        import.meta.dir,
        '../../../examples/simple-racer/public/world/circuit/bed.xml'
      ),
      'utf8'
    );
    const match = xml.match(/flatten-falloff="([0-9.]+)"/);
    expect(match).toBeTruthy();
    expect(Number(match![1])).toBeGreaterThanOrEqual(16);
  });
});
