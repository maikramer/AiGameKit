import { beforeEach, describe, expect, it } from 'bun:test';
import type { ParsedElement, XMLValue } from '../../../src/core';
import { ParseContext } from '../../../src/core';
import { State } from 'aigamekit-vibegame';
import { SpawnerPending } from '../../../src/plugins/spawner/components';
import { natureSpawnerParser } from '../../../src/plugins/nature/parser';
import { getNaturePlans } from '../../../src/plugins/nature/context';
import { XMLValueParser } from '../../../src/core';

function el(
  tagName: string,
  attributes: Record<string, XMLValue>,
  children: ParsedElement[] = []
): ParsedElement {
  return { tagName, attributes, children };
}

/** What the core XML pipeline delivers: XMLValueParser.parse per attribute. */
function xmlAttrs(raw: Record<string, string>): Record<string, XMLValue> {
  const out: Record<string, XMLValue> = {};
  for (const [k, v] of Object.entries(raw)) out[k] = XMLValueParser.parse(v);
  return out;
}

function run(state: State, root: ParsedElement): number {
  const entity = state.createEntity();
  state.addComponent(entity, SpawnerPending);
  natureSpawnerParser({
    entity,
    element: root,
    state,
    context: new ParseContext(state),
  });
  return entity;
}

function baseRoot(children: ParsedElement[]): ParsedElement {
  return el(
    'NatureSpawner',
    xmlAttrs({
      seed: '9100',
      'region-min': '-100 0 -100',
      'region-max': '100 0 100',
      count: '50',
    }),
    children
  );
}

const OAK = el(
  'Species',
  xmlAttrs({
    id: 'oak',
    weight: '3',
    url: '/forest/tree_oak_lod0.glb',
  }),
  [
    el(
      'Where',
      xmlAttrs({
        'altitude-min': '1',
        'altitude-max': '15',
        'road-dist-min': '16',
      })
    ),
  ]
);

describe('natureSpawnerParser', () => {
  let state: State;
  beforeEach(() => {
    state = new State();
    state.registerComponent('spawnerPending', SpawnerPending);
  });

  it('parses species, groves and members into a stored plan', () => {
    const hut = el(
      'Species',
      xmlAttrs({ id: 'witch_hut', weight: '0', url: '/forest/witch_hut.glb' })
    );
    const crate = el(
      'Species',
      xmlAttrs({ id: 'crate', url: '/village/wooden_crate.glb' })
    );
    const grove = el(
      'Grove',
      xmlAttrs({ id: 'camp', count: '5', radius: '9' }),
      [
        el(
          'Where',
          xmlAttrs({
            'altitude-min': '12',
            'altitude-max': '17',
            'road-dist-min': '30',
          })
        ),
        el(
          'Member',
          xmlAttrs({
            species: 'witch_hut',
            'count-min': '1',
            'at-min': '0',
            'at-max': '0',
          })
        ),
        el(
          'Member',
          xmlAttrs({
            species: 'crate',
            'count-min': '2',
            'count-max': '4',
            'at-min': '0.5',
            'at-max': '1',
          })
        ),
      ]
    );
    const entity = run(state, baseRoot([OAK, hut, crate, grove]));

    expect(SpawnerPending.spawned[entity]).toBe(1);
    const runtime = getNaturePlans(state).get(entity)!;
    expect(runtime.planned).toBe(false);
    const plan = runtime.plan;
    expect(plan.seed).toBe(9100);
    expect(plan.count).toBe(50);
    expect(plan.minSpacing).toBeCloseTo(2.5, 5);
    expect(plan.noiseScale).toBeCloseTo(90, 5);
    expect(plan.species.map((s) => s.id)).toEqual([
      'oak',
      'witch_hut',
      'crate',
    ]);
    expect(plan.species[0]!.where.altitude).toEqual({ min: 1, max: 15 });
    expect(plan.species[0]!.where.roadDist).toEqual({ min: 16, max: Infinity });
    expect(plan.species[1]!.weight).toBe(0);
    expect(plan.groves).toHaveLength(1);
    expect(plan.groves[0]!.where.altitude).toEqual({ min: 12, max: 17 });
    expect(plan.groves[0]!.members[1]).toEqual({
      species: 'crate',
      countMin: 2,
      countMax: 4,
      ringMin: 0.5,
      ringMax: 1,
    });
  });

  it('bands survive the XMLValueParser numeric pre-conversion', () => {
    // Regression: range strings like "11..17" are pre-converted to the number
    // 11 (parseFloat) before the parser sees them — bands in XML must come as
    // min/max pairs, and this test runs the real value pipeline.
    const entity = run(state, baseRoot([OAK]));
    const plan = getNaturePlans(state).get(entity)!.plan;
    expect(XMLValueParser.parse('11..17')).toBe(11); // the trap itself
    expect(plan.species[0]!.where.altitude).toEqual({ min: 1, max: 15 });
    expect(plan.species[0]!.where.roadDist).toEqual({ min: 16, max: Infinity });
  });

  it('open-ended bands: omitting a side leaves it unbounded', () => {
    const sp = el('Species', xmlAttrs({ id: 'rock', url: '/props/rock.glb' }), [
      el(
        'Where',
        xmlAttrs({
          'slope-min': '14',
          'water-dist-min': '0',
          'water-dist-max': '10',
        })
      ),
    ]);
    const entity = run(state, baseRoot([sp]));
    const plan = getNaturePlans(state).get(entity)!.plan;
    expect(plan.species[0]!.where.slope).toEqual({ min: 14, max: Infinity });
    expect(plan.species[0]!.where.waterDist).toEqual({ min: 0, max: 10 });
  });

  it('accepts density-per-km2 instead of count', () => {
    const root = el(
      'NatureSpawner',
      xmlAttrs({
        'region-min': '-100 0 -100',
        'region-max': '100 0 100',
        'density-per-km2': '9000',
      }),
      [OAK]
    );
    const entity = run(state, root);
    const plan = getNaturePlans(state).get(entity)!.plan;
    expect(plan.spawnCountMode).toBe('density');
    expect(plan.densityPerKm2).toBe(9000);
  });

  it('throws without region or without count/density', () => {
    expect(() =>
      run(state, el('NatureSpawner', xmlAttrs({ count: '10' }), [OAK]))
    ).toThrow(/region-min/);
    expect(() =>
      run(
        state,
        el(
          'NatureSpawner',
          xmlAttrs({ 'region-min': '-1 0 -1', 'region-max': '1 0 1' }),
          [OAK]
        )
      )
    ).toThrow(/count|density/);
  });

  it('throws on duplicate species ids and unknown member refs', () => {
    const dup = el('Species', xmlAttrs({ id: 'oak', url: '/x.glb' }));
    expect(() => run(state, baseRoot([OAK, dup]))).toThrow(/duplicado/);

    const badMember = el('Grove', xmlAttrs({ count: '1' }), [
      el('Member', xmlAttrs({ species: 'ghost', 'count-min': '1' })),
    ]);
    expect(() => run(state, baseRoot([OAK, badMember]))).toThrow(
      /não corresponde a nenhum/
    );
  });

  it('throws on near without near-dist and on water conflicts', () => {
    const badNear = el('Species', xmlAttrs({ id: 'm', url: '/m.glb' }), [
      el('Where', xmlAttrs({ near: 'oak' })),
    ]);
    expect(() => run(state, baseRoot([OAK, badNear]))).toThrow(/near-dist/);

    const badWater = el('Species', xmlAttrs({ id: 'w', url: '/w.glb' }), [
      el(
        'Where',
        xmlAttrs({ water: 'in', 'water-dist-min': '0', 'water-dist-max': '5' })
      ),
    ]);
    expect(() => run(state, baseRoot([OAK, badWater]))).toThrow(/exclusivos/);
  });

  it('throws on inverted bands and non-numeric bounds', () => {
    const inverted = el('Species', xmlAttrs({ id: 'inv', url: '/i.glb' }), [
      el('Where', xmlAttrs({ 'altitude-min': '20', 'altitude-max': '5' })),
    ]);
    expect(() => run(state, baseRoot([OAK, inverted]))).toThrow(/&gt;|>/);

    const nan = el('Species', xmlAttrs({ id: 'nan', url: '/n.glb' }), [
      el('Where', { 'altitude-min': 'abc' }),
    ]);
    expect(() => run(state, baseRoot([OAK, nan]))).toThrow(/numérico/);
  });

  it('throws on unexpected children', () => {
    expect(() => run(state, baseRoot([OAK, el('Rock', {})]))).toThrow(
      /inesperado/
    );
  });

  it('requires at least one species', () => {
    expect(() => run(state, baseRoot([]))).toThrow(/ao menos um/);
  });

  it('groves may reference species declared after them', () => {
    const late = el(
      'Species',
      xmlAttrs({ id: 'hut', weight: '0', url: '/h.glb' })
    );
    const groveFirst = el('Grove', xmlAttrs({ count: '2' }), [
      el('Member', xmlAttrs({ species: 'hut', 'count-min': '1' })),
    ]);
    const entity = run(state, baseRoot([OAK, groveFirst, late]));
    const plan = getNaturePlans(state).get(entity)!.plan;
    expect(plan.groves[0]!.members[0]!.species).toBe('hut');
  });
});
