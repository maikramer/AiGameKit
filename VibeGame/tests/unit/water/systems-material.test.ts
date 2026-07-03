import { describe, expect, it } from 'bun:test';
import { makeLakeGeometry } from '../../../src/plugins/water/systems';
import { makeRiverGeometry } from '../../../src/plugins/water/river-geometry';

describe('Water shape geometry carries aWaterT', () => {
  it('lake fan has an aWaterT attribute with centre=0', () => {
    const geo = makeLakeGeometry(6, 0, 0);
    expect(geo.getAttribute('aWaterT')).toBeDefined();
    const t = geo.getAttribute('aWaterT');
    expect(t.array[0]).toBeCloseTo(0, 5); // centre vertex
  });

  it('river ribbon has an aWaterT attribute', () => {
    const geo = makeRiverGeometry([0, 0, 10, 0], 4);
    expect(geo.getAttribute('aWaterT')).toBeDefined();
  });
});
