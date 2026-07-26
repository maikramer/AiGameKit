import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import type { Parser, Recipe } from 'vibegame';
import { compositionParser } from '../../../src/plugins/composition/parser';

/**
 * `<Composition>` declares `parserOwnsChildren`, so the core element walker
 * never descends into its children — Composition creates recipe children
 * itself. It used to create them with `createFromRecipe` alone, which applies
 * component defaults and declared attribute mappings but **not** the recipe's
 * own parser. Every attribute a parser owns was therefore dropped: the case
 * that surfaced it was `<DialogueNPC dialogue-id>` inside a `<Composition>`,
 * where all 16 quest NPCs silently fell back to quest index 0.
 */
const Marker = {
  value: new Float32Array(256),
  parsed: new Uint8Array(256),
};

function makeState(): { state: State; seen: string[] } {
  const seen: string[] = [];
  const state = new State();
  state.registerComponent('marker', Marker);

  const markerRecipe: Recipe = {
    name: 'MarkerChild',
    components: ['marker'],
    parserAttributes: ['tag'],
  };
  const markerParser: Parser = ({ entity, element }) => {
    Marker.parsed[entity] = 1;
    const tag = element.attributes.tag;
    seen.push(String(tag ?? ''));
    Marker.value[entity] = Number(tag ?? 0);
  };

  // Parsers are registered through a plugin (State has no direct setter).
  state.registerPlugin({
    recipes: [markerRecipe],
    config: { parsers: { MarkerChild: markerParser } },
  });
  return { state, seen };
}

function compositionElement(children: unknown[]): Parameters<Parser>[0] {
  return {
    entity: 0,
    element: {
      tagName: 'Composition',
      attributes: {},
      children,
    },
    state: null,
    context: undefined,
  } as unknown as Parameters<Parser>[0];
}

describe('Composition recipe children', () => {
  it("runs the child recipe's own parser", () => {
    const { state, seen } = makeState();
    const entity = state.createEntity();
    const args = compositionElement([
      { tagName: 'MarkerChild', attributes: { tag: '7' }, children: [] },
    ]);
    compositionParser({ ...args, entity, state });

    expect(seen).toEqual(['7']);
  });

  it('passes the attributes through to the parser, not just the defaults', () => {
    const { state, seen } = makeState();
    const entity = state.createEntity();
    const args = compositionElement([
      { tagName: 'MarkerChild', attributes: { tag: '3' }, children: [] },
      { tagName: 'MarkerChild', attributes: { tag: '9' }, children: [] },
    ]);
    compositionParser({ ...args, entity, state });

    // Distinct values — the regression made every child collapse onto the
    // component default instead of its own attribute.
    expect(seen).toEqual(['3', '9']);
  });

  it('still ignores unknown child tags without throwing', () => {
    const { state } = makeState();
    const entity = state.createEntity();
    const args = compositionElement([
      { tagName: 'NotARecipe', attributes: {}, children: [] },
    ]);
    expect(() => compositionParser({ ...args, entity, state })).not.toThrow();
  });

  it('handles a recipe child that has no parser', () => {
    const { state } = makeState();
    state.registerPlugin({ recipes: [{ name: 'Plain', components: [] }] });
    const entity = state.createEntity();
    const args = compositionElement([
      { tagName: 'Plain', attributes: {}, children: [] },
    ]);
    expect(() => compositionParser({ ...args, entity, state })).not.toThrow();
  });
});
