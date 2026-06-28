import { afterEach, describe, expect, it } from 'bun:test';
import {
  type LoadingScreenText,
  LoadingPlugin,
  LoadingScreenSystem,
  getLoadingScreenText,
  setLoadingScreenText,
} from 'vibegame';

const DEFAULT_TEXT: LoadingScreenText = { title: 'Loading…', subtitle: '' };

function resetLoadingText(): void {
  setLoadingScreenText({
    title: DEFAULT_TEXT.title,
    subtitle: DEFAULT_TEXT.subtitle,
  });
}

describe('loading: getLoadingScreenText default', () => {
  afterEach(resetLoadingText);

  it('expõe título e subtítulo por defeito', () => {
    const text = getLoadingScreenText();
    expect(text.title).toBe('Loading…');
    expect(text.subtitle).toBe('');
  });
});

describe('loading: setLoadingScreenText merge', () => {
  afterEach(resetLoadingText);

  it('atualiza apenas o título mantendo o subtítulo', () => {
    setLoadingScreenText({ subtitle: 'Building terrain' });
    setLoadingScreenText({ title: 'Almost there' });
    const text = getLoadingScreenText();
    expect(text.title).toBe('Almost there');
    expect(text.subtitle).toBe('Building terrain');
  });

  it('limpa o subtítulo quando definido como string vazia', () => {
    setLoadingScreenText({ subtitle: 'Loading assets' });
    expect(getLoadingScreenText().subtitle).toBe('Loading assets');
    setLoadingScreenText({ subtitle: '' });
    expect(getLoadingScreenText().subtitle).toBe('');
  });

  it('objeto parcial vazio não altera o estado', () => {
    setLoadingScreenText({ title: 'Hold on', subtitle: 'Parsing scene' });
    const before = getLoadingScreenText();
    setLoadingScreenText({});
    const after = getLoadingScreenText();
    expect(after).toEqual(before);
  });

  it('sempre devolve um snapshot com título e subtítulo definidos', () => {
    setLoadingScreenText({ title: 'Welcome' });
    const text = getLoadingScreenText();
    expect(typeof text.title).toBe('string');
    expect(typeof text.subtitle).toBe('string');
  });
});

describe('loading: LoadingPlugin shape', () => {
  it('é um plugin system-only (sem recipes nem componentes)', () => {
    expect(LoadingPlugin.systems).toContain(LoadingScreenSystem);
    expect(LoadingPlugin.recipes ?? []).toHaveLength(0);
    expect(LoadingPlugin.components ?? {}).toEqual({});
  });
});

describe('loading: LoadingScreenSystem shape', () => {
  it('roda no grupo draw', () => {
    expect(LoadingScreenSystem.group).toBe('draw');
  });

  it('expõe setup e update como funções', () => {
    expect(LoadingScreenSystem.setup).toBeTypeOf('function');
    expect(LoadingScreenSystem.update).toBeTypeOf('function');
  });
});
