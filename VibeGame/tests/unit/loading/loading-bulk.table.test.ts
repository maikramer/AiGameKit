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
  registerReadyGate,
  setLoadingScreenLocale,
  setLoadingScreenText,
  _resetGltfLoadTrackingForTests,
  _trackGltfLoadForTests,
} from 'vibegame';
import {
  cancelLoadingFade,
  getLoadingScreenText as getTextDirect,
  mountLoadingScreen as mountDirect,
  setLoadingScreenLocale as setLocaleDirect,
  setLoadingScreenText as setTextDirect,
  updateLoadingScreen as updateDirect,
} from '../../../src/plugins/loading/context';

const DEFAULT = { title: 'Loading…', subtitle: '' };

function resetText(): void {
  setLoadingScreenText({ title: DEFAULT.title, subtitle: DEFAULT.subtitle });
  setLoadingScreenLocale('en');
  cancelLoadingFade();
  _resetGltfLoadTrackingForTests();
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

describe('overlay stops eating clicks as soon as it fades', () => {
  it('sets pointer-events:none when the fade starts', async () => {
    mountDirect({ title: 'fade' });
    const el = document.getElementById('vibegame-loading') as HTMLElement;
    expect(el.style.pointerEvents).toBe('auto');

    const state = new State();
    // First call latches `firstShown`; the fade only starts after
    // MIN_VISIBLE_MS (350ms) has elapsed since then.
    updateDirect(state);
    expect(el.style.pointerEvents).toBe('auto');

    await new Promise((r) => setTimeout(r, 400));
    updateDirect(state);

    expect(el.style.opacity).toBe('0');
    // Invisible but still on top for FADE_MS — it must not swallow the click
    // that focuses the canvas / unlocks audio.
    expect(el.style.pointerEvents).toBe('none');
  });
});

describe('status shows remaining critical models (not stuck at 0)', () => {
  it('EN: done/total · N remaining while assets gate held', async () => {
    setLocaleDirect('en');
    mountDirect({ title: 'assets' });
    const state = new State();
    let resolveA!: () => void;
    let resolveB!: () => void;
    const a = _trackGltfLoadForTests(
      new Promise<void>((r) => {
        resolveA = r;
      }),
      'critical',
      '/models/hero.glb'
    );
    const b = _trackGltfLoadForTests(
      new Promise<void>((r) => {
        resolveB = r;
      }),
      'critical',
      '/models/tree.glb'
    );
    registerReadyGate(state, 'assets', () => false);
    updateDirect(state);
    const root = document.getElementById('vibegame-loading')!;
    const status = root.children[3] as HTMLElement;
    expect(status.textContent).toContain('remaining');
    expect(status.textContent).toMatch(/0\/2/);
    expect(status.textContent).not.toMatch(/0 pending/);

    resolveA();
    await a;
    updateDirect(state);
    expect(status.textContent).toMatch(/1\/2/);
    expect(status.textContent).toContain('1 remaining');

    resolveB();
    await b;
  });

  it('PT: usa restantes em vez de pending', async () => {
    setLocaleDirect('pt');
    mountDirect({ title: 'assets-pt' });
    const state = new State();
    let resolve!: () => void;
    const p = _trackGltfLoadForTests(
      new Promise<void>((r) => {
        resolve = r;
      }),
      'critical',
      '/m/castle.glb'
    );
    registerReadyGate(state, 'assets', () => false);
    updateDirect(state);
    const root = document.getElementById('vibegame-loading')!;
    const status = root.children[3] as HTMLElement;
    expect(status.textContent).toContain('restantes');
    expect(status.textContent).toContain('A carregar modelos');
    resolve();
    await p;
  });
});

describe('LoadingScreenSystem setup on State', () => {
  for (let headless of [true, false]) {
    it(`headless=${headless} setup does not throw`, () => {
      const state = new State();
      state.headless = headless;
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
