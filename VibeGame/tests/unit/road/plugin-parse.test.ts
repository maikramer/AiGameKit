import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { XMLParser, parseXMLToEntities, State } from 'aigamekit-vibegame';
import { RoadPlugin } from '../../../src/plugins/road';
import { getRoadData } from '../../../src/plugins/road/components';
import { Transform } from '../../../src/plugins/transforms/components';

describe('Road plugin parseFlatNumbers', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  function parseRoad(xml: string): { state: State; eid: number } {
    const state = new State();
    state.registerPlugin(RoadPlugin);
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);
    return { state, eid: entities[0]!.entity };
  }

  it('parses a 2-point path (4 numbers → {x,y,z,w}) into real geometry', () => {
    const { state, eid } = parseRoad(
      '<root><Road path="0 0 10 0" width="4"></Road></root>'
    );
    // The {x,y,z,w} object from XMLValueParser must flatten back to 4 numbers
    // — the old string/array-only read left a 2-point road with no geometry.
    const data = getRoadData(state, eid);
    expect(data?.path).toEqual([0, 0, 10, 0]);
    expect(Transform.posX[eid]).toBeCloseTo(0);
    expect(Transform.posZ[eid]).toBeCloseTo(0);
  });

  it('parses a 3-point path (6 numbers → number[]) unchanged', () => {
    const { state, eid } = parseRoad(
      '<root><Road path="0 4 0 24 6 40" width="3"></Road></root>'
    );
    const data = getRoadData(state, eid);
    expect(data?.path).toEqual([0, 4, 0, 24, 6, 40]);
  });

  it('parses widths as a {x,y,z,w} object when there are 2 points', () => {
    const { state, eid } = parseRoad(
      '<root><Road path="0 0 10 0" widths="3 5"></Road></root>'
    );
    const data = getRoadData(state, eid);
    expect(data?.widths).toEqual([3, 5]);
  });
});
