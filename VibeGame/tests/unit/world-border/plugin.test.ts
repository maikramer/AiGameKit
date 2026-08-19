import { describe, expect, it } from 'bun:test';
import { DefaultPlugins } from 'vibegame/defaults';
import type { ParserParams, Plugin, Recipe } from 'vibegame';

interface WorldBorderComponent {
  radius: Float32Array;
  warnSeconds: Float32Array;
  margin: Float32Array;
  warnUntil: Float32Array;
}

function findWorldBorderPlugin(): Plugin | undefined {
  return DefaultPlugins.find((p: Plugin) =>
    p.recipes?.some((r: Recipe) => r.name === 'WorldBorder')
  );
}

function getComponent(): WorldBorderComponent {
  return findWorldBorderPlugin()!.components![
    'world-border'
  ] as unknown as WorldBorderComponent;
}

function makeElement(
  tagName: string,
  attributes: Record<string, string>
): ParserParams {
  return {
    entity: 0,
    element: { tagName, attributes, children: [] },
  } as unknown as ParserParams;
}

function getParser() {
  return findWorldBorderPlugin()!.config!.parsers![
    'WorldBorder'
  ] as unknown as (params: ParserParams) => void;
}

describe('WorldBorder componente', () => {
  const comp = getComponent();

  it('tem radius, warnSeconds, margin e warnUntil', () => {
    const keys = Object.keys(comp);
    expect(keys).toContain('radius');
    expect(keys).toContain('warnSeconds');
    expect(keys).toContain('margin');
    expect(keys).toContain('warnUntil');
  });

  it('campos são TypedArrays com MAX_ENTITIES', () => {
    expect(comp.radius).toBeInstanceOf(Float32Array);
    expect(comp.warnSeconds).toBeInstanceOf(Float32Array);
    expect(comp.radius.length).toBeGreaterThan(0);
  });
});

describe('WorldBorder parser', () => {
  const parser = getParser();
  const comp = getComponent();

  it('parseia radius, warn-seconds e margin', () => {
    parser(makeElement('worldborder', { radius: '560', 'warn-seconds': '5', margin: '24' }));
    expect(comp.radius[0]).toBe(560);
    expect(comp.warnSeconds[0]).toBe(5);
    expect(comp.margin[0]).toBe(24);
  });

  it('aplica defaults sem atributos', () => {
    parser(makeElement('worldborder', {}));
    expect(comp.radius[0]).toBe(600);
    expect(comp.warnSeconds[0]).toBe(5);
    expect(comp.margin[0]).toBe(24);
  });

  it('tagName diferente → ignora sem alterar', () => {
    comp.radius[0] = 123;
    parser(makeElement('notborder', { radius: '999' }));
    expect(comp.radius[0]).toBe(123);
  });
});

describe('WorldBorder plugin estrutura', () => {
  const plugin = findWorldBorderPlugin()!;

  it('tem 1 recipe (WorldBorder) e 1 sistema', () => {
    expect(plugin.recipes?.map((r: Recipe) => r.name)).toEqual(['WorldBorder']);
    expect(plugin.systems).toHaveLength(1);
  });

  it('sistema está no grupo simulation', () => {
    expect(plugin.systems![0].group).toBe('simulation');
  });
});
