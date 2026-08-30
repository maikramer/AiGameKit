import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { hideErrorOverlay, showErrorOverlay } from 'aigamekit-vibegame';

const OVERLAY_ID = 'vibegame-error-overlay';

describe('error overlay', () => {
  let savedDocument: unknown;

  beforeEach(() => {
    // jsdom document left on globalThis leaks into later files in the same
    // bun process.
    savedDocument = globalThis.document;
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.document = dom.window.document as unknown as Document;
  });

  afterEach(() => {
    const g = globalThis as unknown as Record<string, unknown>;
    if (savedDocument === undefined) delete g.document;
    else g.document = savedDocument;
  });

  it('shows a card with title and detail', () => {
    showErrorOverlay('World reload failed', 'Missing closing tag');

    const card = global.document.getElementById(OVERLAY_ID);
    expect(card).not.toBeNull();
    expect(card!.textContent).toContain('World reload failed');
    expect(card!.textContent).toContain('Missing closing tag');
  });

  it('hide removes the card', () => {
    showErrorOverlay('Boom');
    expect(global.document.getElementById(OVERLAY_ID)).not.toBeNull();

    hideErrorOverlay();
    expect(global.document.getElementById(OVERLAY_ID)).toBeNull();
  });

  it('re-showing replaces content instead of stacking cards', () => {
    showErrorOverlay('first');
    showErrorOverlay('second');

    const cards = global.document.querySelectorAll(`#${OVERLAY_ID}`);
    expect(cards.length).toBe(1);
    expect(cards[0]!.textContent).toContain('second');
  });
});
