import { describe, expect, it } from 'bun:test';
import { DefaultPlugins } from 'vibegame/defaults';
import type { Parser, ParserParams, Plugin, Recipe } from 'vibegame';

interface EquirectSkyComponent {
  rotationDeg: Float32Array;
  setBackground: Uint8Array;
  applied: Uint8Array;
}

function findSkyPlugin(): Plugin | undefined {
  return DefaultPlugins.find((p: Plugin) =>
    p.recipes?.some((r: Recipe) => r.name === 'EquirectSky')
  );
}

function getSkyComponent(): EquirectSkyComponent {
  return findSkyPlugin()!.components![
    'equirect-sky'
  ] as unknown as EquirectSkyComponent;
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

describe('EquirectSky componente', () => {
  const comp = getSkyComponent();

  it('tem rotationDeg, setBackground e applied', () => {
    const keys = Object.keys(comp);
    expect(keys).toContain('rotationDeg');
    expect(keys).toContain('setBackground');
    expect(keys).toContain('applied');
    expect(keys).toHaveLength(3);
  });

  it('rotationDeg é Float32Array', () => {
    expect(comp.rotationDeg).toBeInstanceOf(Float32Array);
  });

  it('setBackground e applied são Uint8Array', () => {
    expect(comp.setBackground).toBeInstanceOf(Uint8Array);
    expect(comp.applied).toBeInstanceOf(Uint8Array);
  });
});

describe('EquirectSky recipe', () => {
  const recipe = findSkyPlugin()!.recipes!.find(
    (r: Recipe) => r.name === 'EquirectSky'
  )!;

  it('tem name EquirectSky e componente equirect-sky', () => {
    expect(recipe.name).toBe('EquirectSky');
    expect(recipe.components).toContain('equirect-sky');
  });

  it('declara parserAttributes url, rotation-deg, set-background', () => {
    expect(recipe.parserAttributes).toContain('url');
    expect(recipe.parserAttributes).toContain('rotation-deg');
    expect(recipe.parserAttributes).toContain('set-background');
  });
});

describe('EquirectSky plugin defaults', () => {
  const defaults = findSkyPlugin()!.config!.defaults!['equirect-sky'];

  it('define rotationDeg=0, setBackground=1, applied=0', () => {
    expect(defaults.rotationDeg).toBe(0);
    expect(defaults.setBackground).toBe(1);
    expect(defaults.applied).toBe(0);
  });
});

describe('equirectSkyParser', () => {
  const parser = findSkyPlugin()!.config!.parsers!.EquirectSky as Parser;
  const comp = getSkyComponent();
  const EID = 42;

  it('parser válido: url presente → applied=0 (aguarda load)', () => {
    comp.applied[EID] = 1;
    const params = makeElement('equirectsky', { url: '/assets/sky.png' });
    params.entity = EID;
    parser(params);
    expect(comp.applied[EID]).toBe(0);
    expect(comp.setBackground[EID]).toBe(1);
    expect(comp.rotationDeg[EID]).toBe(0);
  });

  it('parser: set-background="false" → setBackground=0', () => {
    const params = makeElement('equirectsky', {
      url: '/assets/sky.png',
      'set-background': 'false',
    });
    params.entity = EID;
    parser(params);
    expect(comp.setBackground[EID]).toBe(0);
  });

  it('parser: set-background="0" → setBackground=0', () => {
    const params = makeElement('equirectsky', {
      url: '/assets/sky.png',
      'set-background': '0',
    });
    params.entity = EID;
    parser(params);
    expect(comp.setBackground[EID]).toBe(0);
  });

  it('parser: rotation-deg="90" → rotationDeg=90', () => {
    const params = makeElement('equirectsky', {
      url: '/assets/sky.png',
      'rotation-deg': '90',
    });
    params.entity = EID;
    parser(params);
    expect(comp.rotationDeg[EID]).toBe(90);
  });

  it('parser: rotation-deg negativo é respeitado', () => {
    const params = makeElement('equirectsky', {
      url: '/assets/sky.png',
      'rotation-deg': '-45',
    });
    params.entity = EID;
    parser(params);
    expect(comp.rotationDeg[EID]).toBe(-45);
  });

  it('parser: url ausente → applied=1 (skip) sem crash', () => {
    comp.applied[EID] = 0;
    const params = makeElement('equirectsky', {});
    params.entity = EID;
    parser(params);
    expect(comp.applied[EID]).toBe(1);
  });

  it('parser: url vazia → treated as missing (applied=1)', () => {
    comp.applied[EID] = 0;
    const params = makeElement('equirectsky', { url: '   ' });
    params.entity = EID;
    parser(params);
    expect(comp.applied[EID]).toBe(1);
  });

  it('parser: tagName diferente → early return sem alterações', () => {
    comp.applied[EID] = 0;
    comp.rotationDeg[EID] = 77;
    const params = makeElement('notsky', { url: '/assets/sky.png' });
    params.entity = EID;
    parser(params);
    expect(comp.applied[EID]).toBe(0);
    expect(comp.rotationDeg[EID]).toBe(77);
  });
});

describe('EquirectSky plugin estrutura', () => {
  const plugin = findSkyPlugin()!;

  it('tem exactamente 1 recipe e 1 sistema', () => {
    expect(plugin.recipes).toHaveLength(1);
    expect(plugin.systems).toHaveLength(1);
  });

  it('sistema está no grupo simulation', () => {
    expect(plugin.systems![0].group).toBe('simulation');
  });
});
