import { describe, expect, it } from 'bun:test';
import {
  Road,
  makeRoadGeometry,
  resampleRoadPath,
  smoothPath,
  setRoadData,
  getRoadData,
  type RoadGeometryOptions,
} from 'aigamekit-vibegame/road';
import { deleteRoadData } from '../../../src/plugins/road/components';
import { State } from 'aigamekit-vibegame';

function baseOpts(
  overrides: Partial<RoadGeometryOptions> = {}
): RoadGeometryOptions {
  return {
    width: 5,
    textureScale: 16,
    edgeFeather: 1,
    edgeNoise: 0,
    endFeatherStart: 0,
    endFeatherEnd: 0,
    ...overrides,
  };
}

const STRAIGHT = [0, 0, 20, 0];
const L_SHAPE = [0, 0, 20, 0, 20, 20];

describe('road bulk: smoothPath iterations', () => {
  for (let iter = 0; iter <= 4; iter++) {
    for (let run = 0; run < 5; run++) {
      it(`smoothPath preserves endpoints iter=${iter} run=${run}`, () => {
        const path = run % 2 === 0 ? STRAIGHT : L_SHAPE;
        const out = smoothPath(path, iter);
        expect(out[0]).toBe(path[0]);
        expect(out[1]).toBe(path[1]);
        expect(out[out.length - 2]).toBe(path[path.length - 2]);
        expect(out[out.length - 1]).toBe(path[path.length - 1]);
      });
    }
  }
});

describe('road bulk: resampleRoadPath spacing', () => {
  for (let spacing = 1; spacing <= 20; spacing++) {
    it(`resample spacing=${spacing} reaches end x=20`, () => {
      const out = resampleRoadPath(STRAIGHT, spacing);
      expect(out[out.length - 2]).toBeCloseTo(20);
      expect(out.length / 2).toBeGreaterThanOrEqual(2);
    });
  }

  for (let spacing = 0.5; spacing <= 5; spacing += 0.5) {
    it(`resample monotonic x for spacing=${spacing}`, () => {
      const out = resampleRoadPath(STRAIGHT, spacing);
      for (let i = 2; i < out.length; i += 2) {
        expect(out[i]!).toBeGreaterThanOrEqual(out[i - 2]! - 1e-9);
      }
    });
  }
});

describe('road bulk: makeRoadGeometry vertex counts', () => {
  for (let width = 2; width <= 12; width++) {
    for (let spacing of [2, 4, 8]) {
      it(`geometry stations width=${width} spacing=${spacing}`, () => {
        const path = resampleRoadPath(STRAIGHT, spacing);
        const geo = makeRoadGeometry(path, baseOpts({ width }));
        const stations = path.length / 2;
        expect(geo.getAttribute('position').count).toBe(stations * 4);
        expect(geo.getIndex()!.count).toBe((stations - 1) * 18);
      });
    }
  }
});

describe('road bulk: makeRoadGeometry alpha lanes', () => {
  for (let station = 1; station < 8; station++) {
    it(`middle station ${station} core alpha is 1`, () => {
      const path = resampleRoadPath(STRAIGHT, 2);
      const geo = makeRoadGeometry(path, baseOpts());
      const c = geo.getAttribute('color');
      const base = station * 4;
      expect(c.getW(base)).toBe(0);
      expect(c.getW(base + 1)).toBe(1);
      expect(c.getW(base + 2)).toBe(1);
      expect(c.getW(base + 3)).toBe(0);
    });
  }
});

describe('road bulk: Road component SOA', () => {
  for (let eid = 1; eid <= 10; eid++) {
    it(`Road.width[${eid}] stores float`, () => {
      Road.width[eid] = 4 + eid;
      expect(Road.width[eid]).toBeCloseTo(4 + eid);
    });
    it(`Road.flatten[${eid}] stores uint`, () => {
      Road.flatten[eid] = eid % 2;
      expect(Road.flatten[eid]).toBe(eid % 2);
    });
  }
});

describe('road bulk: RoadData side table', () => {
  for (let i = 0; i < 15; i++) {
    it(`setRoadData/getRoadData round-trip #${i}`, () => {
      const state = new State();
      const entity = state.createEntity();
      const path = [0, 0, i, i];
      setRoadData(state, entity, {
        path,
        textureUrl: `/tex/${i}.png`,
        normalMapUrl: null,
        roughnessMapUrl: null,
      });
      const data = getRoadData(state, entity);
      expect(data?.path).toEqual(path);
      expect(data?.textureUrl).toBe(`/tex/${i}.png`);
      deleteRoadData(state, entity);
      expect(getRoadData(state, entity)).toBeNull();
    });
  }
});

describe('road bulk: heightAt + yOffset', () => {
  for (let slope = 0; slope < 10; slope++) {
    it(`heightAt slope=${slope}`, () => {
      const path = resampleRoadPath([0, 0, 10, 0], 5);
      const geo = makeRoadGeometry(
        path,
        baseOpts({
          heightAt: (x) => x * slope * 0.1,
          yOffset: 0.05,
        })
      );
      const p = geo.getAttribute('position');
      expect(Number.isFinite(p.getY(0))).toBe(true);
      expect(p.getY(p.count - 1)).toBeGreaterThan(0.04);
    });
  }
});
