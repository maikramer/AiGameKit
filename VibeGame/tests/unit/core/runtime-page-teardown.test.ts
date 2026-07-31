import { afterEach, beforeEach, describe, expect, it, mock } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  disposeAllRuntimes,
  ensureRuntimePageTeardown,
  registerRuntime,
  releaseRuntimeGpuResources,
  resetRuntimePageTeardownForTests,
} from '../../../src/core/runtime-manager';

describe('ensureRuntimePageTeardown', () => {
  let dom: JSDOM;

  beforeEach(() => {
    dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    globalThis.window = dom.window as unknown as typeof window;
    globalThis.Event = dom.window.Event as unknown as typeof Event;
    resetRuntimePageTeardownForTests();
  });

  afterEach(() => {
    // Fake runtimes in this file do not unregister on destroy — clear hooks only.
    resetRuntimePageTeardownForTests();
    disposeAllRuntimes();
    dom.window.close();
  });

  it('installs pagehide only once', () => {
    const add = mock(() => {});
    const original = window.addEventListener.bind(window);
    window.addEventListener = add as typeof window.addEventListener;
    try {
      ensureRuntimePageTeardown();
      ensureRuntimePageTeardown();
      const pagehideCalls = add.mock.calls.filter(
        (c: unknown[]) => c[0] === 'pagehide'
      );
      expect(pagehideCalls.length).toBe(1);
    } finally {
      window.addEventListener = original;
    }
  });

  it('releases GPU on non-persisted pagehide without full destroy', () => {
    ensureRuntimePageTeardown();

    const releaseGpuContext = mock(() => {});
    const destroy = mock(() => {});
    registerRuntime({ releaseGpuContext, destroy } as never);

    const event = new Event('pagehide') as PageTransitionEvent;
    Object.defineProperty(event, 'persisted', { value: false });
    window.dispatchEvent(event);

    expect(releaseGpuContext).toHaveBeenCalledTimes(1);
    expect(destroy).not.toHaveBeenCalled();
  });

  it('skips GPU release when pagehide is persisted (bfcache)', () => {
    ensureRuntimePageTeardown();

    const releaseGpuContext = mock(() => {});
    registerRuntime({ releaseGpuContext, destroy: mock(() => {}) } as never);

    const event = new Event('pagehide') as PageTransitionEvent;
    Object.defineProperty(event, 'persisted', { value: true });
    window.dispatchEvent(event);

    expect(releaseGpuContext).not.toHaveBeenCalled();
  });

  it('releaseRuntimeGpuResources calls each runtime', () => {
    const a = mock(() => {});
    const b = mock(() => {});
    registerRuntime({ releaseGpuContext: a, destroy: mock(() => {}) } as never);
    registerRuntime({ releaseGpuContext: b, destroy: mock(() => {}) } as never);
    releaseRuntimeGpuResources();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });
});
