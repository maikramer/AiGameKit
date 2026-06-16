import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  RpgCorePlugin,
  State,
  XMLParser,
  getDataRegistry,
  parseXMLToEntities,
} from 'vibegame';
import { join } from 'node:path';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

const FIXTURE = join(import.meta.dir, 'fixtures', 'items.yaml');

function newStateWithRpg(): State {
  const state = new State();
  state.registerPlugin(RpgCorePlugin);
  return state;
}

describe('<RpgData> recipe loading', () => {
  it('loads a YAML file and registers its definitions', () => {
    const state = newStateWithRpg();
    const xml = `<Scene><RpgData src="${FIXTURE}"/></Scene>`;
    const parsed = XMLParser.parse(xml);

    parseXMLToEntities(state, parsed.root);

    const reg = getDataRegistry(state);
    const potion = reg.get<{ name: string; maxStack: number }>(
      'item',
      'potion'
    );
    expect(potion).toBeDefined();
    expect(potion!.name).toBe('Health Potion');
    expect(potion!.maxStack).toBe(99);

    const sword = reg.get<{ name: string }>('item', 'sword');
    expect(sword).toBeDefined();
    expect(sword!.name).toBe('Iron Sword');

    const vit = reg.get<{ name: string; maxRank: number }>(
      'skill',
      'vitality'
    );
    expect(vit).toBeDefined();
    expect(vit!.maxRank).toBe(5);

    expect(reg.all('item').length).toBe(2);
  });

  it('injects the id field onto definitions that omit it', () => {
    const state = newStateWithRpg();
    const xml = `<Scene><RpgData src="${FIXTURE}"/></Scene>`;

    parseXMLToEntities(state, XMLParser.parse(xml).root);

    const potion = getDataRegistry(state).get<{ id: string }>(
      'item',
      'potion'
    );
    expect(potion!.id).toBe('potion');
  });

  it('does not throw on a missing src file (logs and continues)', () => {
    const state = newStateWithRpg();
    const xml = `<Scene><RpgData src="/does/not/exist.yaml"/></Scene>`;

    expect(() =>
      parseXMLToEntities(state, XMLParser.parse(xml).root)
    ).not.toThrow();
    expect(getDataRegistry(state).all('item')).toEqual([]);
  });

  it('multiple <RpgData> elements accumulate into the same registry', () => {
    const state = newStateWithRpg();
    const xml = `<Scene>
      <RpgData src="${FIXTURE}"/>
      <RpgData src="${FIXTURE}"/>
    </Scene>`;

    parseXMLToEntities(state, XMLParser.parse(xml).root);

    expect(getDataRegistry(state).all('item').length).toBe(2);
  });

  it('without RpgCorePlugin <RpgData> is an unknown recipe and throws', () => {
    const state = new State();
    const xml = `<Scene><RpgData src="${FIXTURE}"/></Scene>`;

    expect(() => parseXMLToEntities(state, XMLParser.parse(xml).root)).toThrow(
      /RpgData/
    );
  });
});
