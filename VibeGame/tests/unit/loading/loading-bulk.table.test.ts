import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
} from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  LoadingPlugin,
  LoadingScreenSystem,
  State,
  getLoadingScreenText,
  mountLoadingScreen,
  setLoadingScreenText,
} from 'vibegame';
import {
  cancelLoadingFade,
  getLoadingScreenText as getTextDirect,
  mountLoadingScreen as mountDirect,
  setLoadingScreenText as setTextDirect,
} from '../../../src/plugins/loading/context';

const DEFAULT = { title: 'Loading…', subtitle: '' };

function resetText(): void {
  setLoadingScreenText({ title: DEFAULT.title, subtitle: DEFAULT.subtitle });
  cancelLoadingFade();
}

const prevDocument = globalThis.document;
const prevPerformance = globalThis.performance;
const prevDOMParser = globalThis.DOMParser;

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.document = dom.window.document;
  // Do NOT replace globalThis.performance — jsdom's Performance breaks later
  // suites (input/gamepad/profiler) with "Illegal constructor" on .now().
  globalThis.DOMParser = dom.window.DOMParser;
});

afterAll(() => {
  globalThis.document = prevDocument;
  globalThis.performance = prevPerformance;
  globalThis.DOMParser = prevDOMParser;
});

beforeEach(resetText);
afterEach(resetText);

describe('setLoadingScreenText title/subtitle matrix', () => {
  const titles = ['Loading…', 'Please wait', 'Almost ready', ''];
  const subtitles = ['', 'Terrain', 'Assets', 'Spawns'];

  for (const title of titles) {
    for (const subtitle of subtitles) {
      it(`title="${title}" subtitle="${subtitle}"`, () => {
        setTextDirect({ title, subtitle });
        const snap = getTextDirect();
        expect(snap.title).toBe(title);
        expect(snap.subtitle).toBe(subtitle);
      });
    }
  }
});

describe('mountLoadingScreen idempotent DOM', () => {
  for (let i = 0; i < 10; i += 1) {
    it(`mount call ${i}`, () => {
      mountDirect({ title: `T${i}` });
      const el = document.getElementById('vibegame-loading');
      expect(el).not.toBeNull();
      expect(getLoadingScreenText().title).toBe(`T${i}`);
      mountDirect();
      expect(document.querySelectorAll('#vibegame-loading').length).toBe(1);
    });
  }
});

describe('cancelLoadingFade removes overlay', () => {
  for (let i = 0; i < 8; i += 1) {
    it(`cycle ${i}`, () => {
      mountDirect({ title: `cycle-${i}` });
      expect(document.getElementById('vibegame-loading')).not.toBeNull();
      cancelLoadingFade();
      expect(document.getElementById('vibegame-loading')).toBeNull();
    });
  }
});

describe('LoadingScreenSystem setup on State', () => {
  for (let headless of [true, false]) {
    it(`headless=${headless} setup does not throw`, () => {
      const state = new State({ headless });
      expect(() => LoadingScreenSystem.setup?.(state)).not.toThrow();
    });
  }
});

describe('LoadingPlugin exports', () => {
  it('name is implied via systems', () => {
    expect(LoadingPlugin.systems?.length).toBe(1);
    expect(LoadingPlugin.systems?.[0]?.name).toBe('LoadingScreenSystem');
  });

  for (const key of ['group', 'name'] as const) {
    it(`LoadingScreenSystem.${key}`, () => {
      expect(LoadingScreenSystem[key]).toBeDefined();
    });
  }
});

describe('vibegame re-exports loading API', () => {
  for (const fn of [
    getLoadingScreenText,
    setLoadingScreenText,
    mountLoadingScreen,
  ]) {
    it(`${fn.name} is function`, () => {
      expect(fn).toBeTypeOf('function');
    });
  }
});

describe('partial title updates preserve subtitle batch', () => {
  for (let i = 0; i < 40; i += 1) {
    it(`iteration ${i}`, () => {
      setTextDirect({ subtitle: `sub-${i}` });
      setTextDirect({ title: `title-${i}` });
      const t = getTextDirect();
      expect(t.subtitle).toBe(`sub-${i}`);
      expect(t.title).toBe(`title-${i}`);
    });
  }
});

describe('LoadingScreenSystem metadata', () => {
  for (let i = 0; i < 10; i += 1) {
    it(`system name stable ${i}`, () => {
      expect(LoadingScreenSystem.name).toBe('LoadingScreenSystem');
      expect(LoadingScreenSystem.group).toBe('draw');
    });
  }
});
