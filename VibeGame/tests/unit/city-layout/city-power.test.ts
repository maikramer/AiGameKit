import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { DefaultPlugins } from 'vibegame/defaults';
import { listAllPrefabs } from '../../../src/plugins/city-layout/prefabs';
import { getCompositionData } from '../../../src/plugins/composition/primitives';
import { getRoadData } from '../../../src/plugins/road';
import { getPlacementSpecs } from '../../../src/plugins/spawner/place-context';

describe('city-layout power recipes', () => {
  let state: State;

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
    state = new State();
    for (const p of DefaultPlugins) {
      state.registerPlugin(p);
    }
  });

  it('lists expanded prefab catalog', () => {
    expect(listAllPrefabs()).toContain('chapel');
    expect(listAllPrefabs()).toContain('well');
    expect(listAllPrefabs()).toContain('fountain');
  });

  it('expands WallRect + Plaza + BuildingRow + Prop + Gate', () => {
    const xml = `
      <Scene>
        <CityGrid cell="4" origin="0 0" align-to-terrain="0" name="g">
          <WallRect min="0 0" max="4 4" height="3" gates="s"></WallRect>
          <Plaza min="1 1" max="3 3" color="#6b4a2b" name="plaza"></Plaza>
          <BuildingRow from="1 0" to="3 0" step="1" prefab="house" name="row"></BuildingRow>
          <Prop at="2 2" prefab="well" name="well"></Prop>
          <Gate at="2 0" facing="s" name="south-gate"></Gate>
          <StreetRing min="0 0" max="4 4" width="1"></StreetRing>
        </CityGrid>
      </Scene>
    `;
    parseXMLToEntities(state, XMLParser.parse(xml).root);

    expect(state.getEntityByName('plaza')).not.toBeNull();
    expect(state.getEntityByName('well')).not.toBeNull();
    expect(state.getEntityByName('south-gate')).not.toBeNull();
    expect(state.getEntityByName('row.0')).not.toBeNull();
    expect(state.getEntityByName('row.2')).not.toBeNull();
    expect(state.getEntityByName('gate.s')).not.toBeNull();

    const plaza = getCompositionData(state, state.getEntityByName('plaza')!);
    expect(plaza?.specs.length).toBeGreaterThan(0);

    const wellPlace = getPlacementSpecs(state).get(
      state.getEntityByName('well')!
    );
    expect(wellPlace?.atX).toBe(8);
    expect(wellPlace?.atZ).toBe(8);

    // StreetRing → 4 roads
    let roads = 0;
    for (let e = 0; e < 8192; e++) {
      if (getRoadData(state, e)) roads++;
    }
    expect(roads).toBe(4);
  });

  it('expands Block fill', () => {
    const xml = `
      <Scene>
        <CityGrid cell="2" origin="0 0" align-to-terrain="0">
          <Block min="0 0" max="1 1" mode="fill" prefab="cottage" name="b"></Block>
        </CityGrid>
      </Scene>
    `;
    parseXMLToEntities(state, XMLParser.parse(xml).root);
    expect(state.getEntityByName('b.0')).not.toBeNull();
    expect(state.getEntityByName('b.3')).not.toBeNull();
  });

  it('rejects diagonal BuildingRow', () => {
    const xml = `
      <Scene>
        <CityGrid cell="4" origin="0 0">
          <BuildingRow from="0 0" to="2 2" prefab="house"></BuildingRow>
        </CityGrid>
      </Scene>
    `;
    expect(() => parseXMLToEntities(state, XMLParser.parse(xml).root)).toThrow(
      /axis-aligned/
    );
  });
});
