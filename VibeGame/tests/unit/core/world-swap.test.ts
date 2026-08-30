import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { Scene, State, XMLParser, getAllEntities } from 'aigamekit-vibegame';

function parse(xml: string) {
  return XMLParser.parse(xml).root;
}

describe('Scene.swap', () => {
  let state: State;

  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
    state = new State();
    state.headless = true;
  });

  it('first swap behaves like a fresh world load', () => {
    const result = Scene.swap(
      state,
      parse('<Scene><GameObject name="ground"></GameObject></Scene>')
    );
    expect(result.created).toBe(1);
    expect(result.destroyed).toBe(0);
    expect(state.getEntityByName('ground')).not.toBeNull();
  });

  it('replaces only previous world entities and keeps outsiders alive', () => {
    Scene.swap(
      state,
      parse(
        '<Scene><GameObject name="a"></GameObject><GameObject name="b"></GameObject></Scene>'
      )
    );

    // Simulates the auto-created player: not part of the world document.
    const player = state.createEntity();
    state.setEntityName('player', player);

    const result = Scene.swap(
      state,
      parse('<Scene><GameObject name="c"></GameObject></Scene>')
    );

    expect(result.destroyed).toBe(2);
    expect(result.created).toBe(1);
    expect(state.getEntityByName('a')).toBeNull();
    expect(state.getEntityByName('b')).toBeNull();
    expect(state.getEntityByName('c')).not.toBeNull();
    expect(state.getEntityByName('player')).toBe(player);
  });

  it('worldEntities lists the last parse entities', () => {
    Scene.swap(
      state,
      parse('<Scene><GameObject name="a"></GameObject></Scene>')
    );
    const ids = Scene.worldEntities(state);
    expect(ids.length).toBe(1);
    expect(state.exists(ids[0]!)).toBe(true);
  });

  it('invalid XML throws before any world mutation', () => {
    Scene.swap(
      state,
      parse('<Scene><GameObject name="a"></GameObject></Scene>')
    );
    const before = getAllEntities(state.world).length;

    expect(() => XMLParser.parse('<Scene><unclosed></Scene>')).toThrow();
    expect(getAllEntities(state.world).length).toBe(before);
    expect(state.getEntityByName('a')).not.toBeNull();
  });

  it('empty scene swap clears world entities but keeps outsiders', () => {
    Scene.swap(
      state,
      parse('<Scene><GameObject name="a"></GameObject></Scene>')
    );
    const player = state.createEntity();
    state.setEntityName('player', player);

    const result = Scene.swap(state, parse('<Scene></Scene>'));

    expect(result.destroyed).toBe(1);
    expect(result.created).toBe(0);
    expect(state.getEntityByName('a')).toBeNull();
    expect(state.getEntityByName('player')).toBe(player);
  });
});
