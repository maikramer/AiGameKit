import { beforeEach, describe, expect, it, mock } from 'bun:test';
import { JSDOM } from 'jsdom';

mock.module('howler', () => ({
  Howl: class {
    play() {
      return 1;
    }
    stop() {}
    unload() {}
    volume() {}
    rate() {}
    once() {}
    fade() {}
    pos() {}
  },
  Howler: { ctx: { state: 'suspended' } },
}));

const { createProfilerPanel, setProfilerPanelTab } =
  await import('../../../src/plugins/profiler/panel');
const {
  getAudioDebugSnapshot,
  isAudioDebugArmed,
  recordAudioDebugEvent,
  _resetAudioDebugLog,
} = await import('../../../src/plugins/audio/debug-log');

describe('profiler panel tabs', () => {
  beforeEach(() => {
    _resetAudioDebugLog();
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
      url: 'http://localhost/',
    });
    (globalThis as unknown as { document: Document }).document = dom.window
      .document as unknown as Document;
    (globalThis as unknown as { window: Window }).window =
      dom.window as unknown as Window;
    (globalThis as unknown as { HTMLElement: typeof HTMLElement }).HTMLElement =
      dom.window.HTMLElement;
    (globalThis as unknown as { navigator: Navigator }).navigator = {
      clipboard: {
        writeText: async () => {},
      },
    } as unknown as Navigator;
  });

  it('creates Systems, Audio, and World tabs and switches panes', () => {
    const panel = createProfilerPanel();
    expect(panel.root.id).toBe('vibegame-profiler-panel');
    expect(panel.tab).toBe('systems');
    expect(panel.systemsPane.style.display).not.toBe('none');
    expect(panel.audioPane.style.display).toBe('none');
    expect(panel.worldPane.style.display).toBe('none');

    setProfilerPanelTab(panel, 'audio', { syncUrl: false });
    expect(panel.tab).toBe('audio');
    expect(panel.systemsPane.style.display).toBe('none');
    expect(panel.audioPane.style.display).toBe('block');
    expect(panel.worldPane.style.display).toBe('none');
    expect(isAudioDebugArmed()).toBe(true);

    setProfilerPanelTab(panel, 'world', { syncUrl: false });
    expect(panel.tab).toBe('world');
    expect(panel.worldPane.style.display).toBe('block');
    expect(panel.audioPane.style.display).toBe('none');
    expect(panel.systemsPane.style.display).toBe('none');

    setProfilerPanelTab(panel, 'systems', { syncUrl: false });
    expect(panel.tab).toBe('systems');
    expect(panel.systemsPane.style.display).toBe('block');
  });

  it('creates a Physics tab and switches panes', () => {
    const panel = createProfilerPanel();
    expect(panel.tabButtons.physics).toBeDefined();
    expect(panel.tabButtons.physics.textContent).toBe('Physics');
    expect(panel.physicsPane.style.display).toBe('none');

    setProfilerPanelTab(panel, 'physics', { syncUrl: false });
    expect(panel.tab).toBe('physics');
    expect(panel.physicsPane.style.display).toBe('block');
    expect(panel.systemsPane.style.display).toBe('none');
    expect(panel.worldPane.style.display).toBe('none');

    // Copy JSON button exists inside the physics toolbar.
    const copyBtn = [...panel.physicsPane.querySelectorAll('button')].find(
      (b) => b.textContent === 'Copy JSON'
    );
    expect(copyBtn).toBeDefined();

    setProfilerPanelTab(panel, 'systems', { syncUrl: false });
    expect(panel.physicsPane.style.display).toBe('none');
  });

  it('audio pane buttons clear log', () => {
    const panel = createProfilerPanel();
    setProfilerPanelTab(panel, 'audio', { syncUrl: false });
    recordAudioDebugEvent({ kind: 'play', key: 'x', source: 'bank' });
    expect(getAudioDebugSnapshot().events.length).toBeGreaterThan(0);

    const clearBtn = [...panel.audioPane.querySelectorAll('button')].find(
      (b) => b.textContent === 'Clear log'
    );
    expect(clearBtn).toBeDefined();
    clearBtn!.click();
    expect(getAudioDebugSnapshot().events).toHaveLength(0);
  });
});
