import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { Parent, State, XMLParser, parseXMLToEntities } from 'vibegame';
import { DefaultPlugins } from 'vibegame/defaults';
import {
  cellToWorld,
  parseCell,
  parseOrigin,
} from '../../../src/plugins/city-layout/grid';
import { getCompositionData } from '../../../src/plugins/composition/primitives';
import { getRoadData } from '../../../src/plugins/road';
import { getPlacementSpecs } from '../../../src/plugins/spawner/place-context';

describe('city-layout grid helpers', () => {
  it('parses origin and cell', () => {
    expect(parseOrigin('10 -4')).toEqual([10, -4]);
    expect(parseCell('2 1', 't')).toEqual([2, 1]);
    expect(cellToWorld(2, 1, 4, 0, 0)).toEqual([8, 4]);
  });
});

describe('CityGrid expansion', () => {
  let state: State;

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
    state = new State();
    for (const p of DefaultPlugins) {
      state.registerPlugin(p);
    }
  });

  it('expands Building prefab + Street + Slot under CityGrid', () => {
    const xml = `
      <Scene>
        <CityGrid cell="4" origin="0 0" align-to-terrain="0" name="grid">
          <Street from="0 0" to="2 0" width="1"></Street>
          <Building at="1 0" prefab="house" name="city.house.a" rot="90"></Building>
          <Slot at="2 1" role="well" name="city.well"></Slot>
        </CityGrid>
      </Scene>
    `;
    const parsed = XMLParser.parse(xml);
    parseXMLToEntities(state, parsed.root);

    const house = state.getEntityByName('city.house.a');
    const well = state.getEntityByName('city.well');
    const grid = state.getEntityByName('grid');
    expect(house).not.toBeNull();
    expect(well).not.toBeNull();
    expect(grid).not.toBeNull();

    const housePlace = getPlacementSpecs(state).get(house!);
    expect(housePlace?.atX).toBe(4);
    expect(housePlace?.atZ).toBe(0);

    const comp = getCompositionData(state, house!);
    expect(comp?.specs.length).toBeGreaterThan(0);

    let roadEid = -1;
    for (let e = 0; e < 4096; e++) {
      if (!state.hasComponent(e, Parent)) continue;
      if (Parent.entity[e] !== grid) continue;
      if (getRoadData(state, e)) {
        roadEid = e;
        break;
      }
    }
    expect(roadEid).toBeGreaterThanOrEqual(0);
    const road = getRoadData(state, roadEid)!;
    expect(road.path.length).toBeGreaterThanOrEqual(4);
    expect(road.path[0]).toBe(0);
    expect(road.path[2]).toBe(8);
  });

  it('rejects Building outside CityGrid', () => {
    const xml = `
      <Scene>
        <Building at="0 0" prefab="house"></Building>
      </Scene>
    `;
    const parsed = XMLParser.parse(xml);
    expect(() => parseXMLToEntities(state, parsed.root)).toThrow(
      /must be a child of <CityGrid>/
    );
  });
});
