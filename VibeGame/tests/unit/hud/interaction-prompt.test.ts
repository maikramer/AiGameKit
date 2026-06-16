import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'vibegame';
import type { ParserParams } from '../../../src/core';
import { loadDictionary } from '../../../src/plugins/i18n/utils';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import {
  HudScreenUpdateSystem,
  getHudScreenLayer,
  getHudWidgetFactory,
  registerHudWidget,
} from '../../../src/plugins/hud/screen-layer';
import { Transform } from '../../../src/plugins/transforms';
import {
  type InteractionTarget,
  getInteractionTargets,
  interactionPromptParser,
  interactionPromptRecipe,
  interactionPromptWidgetFactory,
  registerInteractionTarget,
  unregisterInteractionTarget,
} from '../../../src/plugins/hud/widgets/interaction-prompt';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser as unknown as typeof DOMParser;
  globalThis.document = dom.window.document as unknown as typeof document;
  globalThis.window = dom.window as unknown as typeof window;
  globalThis.HTMLElement = dom.window
    .HTMLElement as unknown as typeof HTMLElement;
  globalThis.HTMLDivElement = dom.window
    .HTMLDivElement as unknown as typeof HTMLDivElement;
});

function newState(): State {
  const state = new State();
  state.registerPlugin(HudPlugin);
  return state;
}

function mountPrompt(
  state: State,
  attrs: Record<string, string> = {}
): HTMLDivElement {
  registerHudWidget(state, interactionPromptWidgetFactory(attrs, state));
  return getHudScreenLayer(state).querySelector<HTMLDivElement>('.hud-prompt')!;
}

function tick(state: State): void {
  HudScreenUpdateSystem.update!(state);
}

function place(eid: number, x: number, z: number): void {
  Transform.posX[eid] = x;
  Transform.posZ[eid] = z;
}

function keyOf(root: HTMLElement): string {
  return root.querySelector<HTMLElement>('.hud-prompt-key')!.textContent ?? '';
}

function labelOf(root: HTMLElement): string {
  return (
    root.querySelector<HTMLElement>('.hud-prompt-label')!.textContent ?? ''
  );
}

describe('InteractionPrompt — surface', () => {
  it('recipe is named InteractionPrompt and owns its parser attributes', () => {
    expect(interactionPromptRecipe.name).toBe('InteractionPrompt');
    expect(interactionPromptRecipe.parserAttributes).toContain('range');
    expect(interactionPromptRecipe.parserAttributes).toContain('prompt-range');
    expect(interactionPromptRecipe.parserAttributes).toContain('key');
    expect(interactionPromptRecipe.parserAttributes).toContain(
      'i18n-key-template'
    );
  });

  it('factory is registered for <HudWidget type="interaction-prompt">', () => {
    expect(getHudWidgetFactory('interaction-prompt')).toBe(
      interactionPromptWidgetFactory
    );
  });

  it('HudPlugin exposes the InteractionPrompt parser', () => {
    expect(HudPlugin.config?.parsers?.InteractionPrompt).toBe(
      interactionPromptParser
    );
  });

  it('widget id is stable so duplicate mounts are deduped', () => {
    const state = newState();
    const w = interactionPromptWidgetFactory({}, state);
    expect(w.id).toBe('vibe:interaction-prompt');
  });
});

describe('InteractionPrompt — mount', () => {
  let state: State;
  let root: HTMLDivElement;

  beforeEach(() => {
    state = newState();
    root = mountPrompt(state);
  });

  it('builds a .hud-prompt with key (default F) and label spans', () => {
    expect(root.classList.contains('hud-prompt')).toBe(true);
    expect(keyOf(root)).toBe('F');
    expect(root.querySelector('.hud-prompt-label')).not.toBeNull();
  });

  it('starts hidden (data-visible=false, visibility hidden)', () => {
    expect(root.dataset.visible).toBe('false');
    expect(root.style.visibility).toBe('hidden');
    expect(root.style.opacity).toBe('0');
  });

  it('honours a custom key attribute', () => {
    const s = newState();
    const r = mountPrompt(s, { key: 'K' });
    expect(keyOf(r)).toBe('K');
  });
});

describe('InteractionPrompt — nearest-in-range gating', () => {
  let state: State;
  let player: number;

  beforeEach(() => {
    state = newState();
    player = state.createEntity();
    place(player, 0, 0);
  });

  it('shows the nearest target when the player is within range', () => {
    const merchant = state.createEntity();
    place(merchant, 3, 0);
    registerInteractionTarget(state, merchant, { label: 'Talk to Merchant' });

    const root = mountPrompt(state, {
      range: '4.5',
      'player-eid': String(player),
    });
    tick(state);

    expect(root.dataset.visible).toBe('true');
    expect(root.style.visibility).toBe('visible');
    expect(labelOf(root)).toBe('Talk to Merchant');
    expect(keyOf(root)).toBe('F');
  });

  it('shows only the nearest target — not all in-range interactables', () => {
    const far = state.createEntity();
    place(far, 3, 0);
    registerInteractionTarget(state, far, { label: 'Far' });

    const near = state.createEntity();
    place(near, 1, 0);
    registerInteractionTarget(state, near, { label: 'Near' });

    const root = mountPrompt(state, {
      range: '4.5',
      'player-eid': String(player),
    });
    tick(state);

    expect(labelOf(root)).toBe('Near');
    const prompts = getHudScreenLayer(state).querySelectorAll('.hud-prompt');
    expect(prompts).toHaveLength(1);
  });

  it('hides when the player moves out of range', () => {
    const merchant = state.createEntity();
    registerInteractionTarget(state, merchant, { label: 'Talk to Merchant' });

    const root = mountPrompt(state, {
      range: '4.5',
      'player-eid': String(player),
    });

    place(merchant, 3, 0);
    tick(state);
    expect(root.dataset.visible).toBe('true');

    place(merchant, 5, 0);
    tick(state);
    expect(root.dataset.visible).toBe('false');
    expect(root.style.visibility).toBe('hidden');
  });

  it('hides when there are no registered targets', () => {
    const root = mountPrompt(state, {
      range: '4.5',
      'player-eid': String(player),
    });
    tick(state);
    expect(root.dataset.visible).toBe('false');
  });

  it('hides when no player can be resolved', () => {
    const merchant = state.createEntity();
    place(merchant, 1, 0);
    registerInteractionTarget(state, merchant, { label: 'Talk' });

    const root = mountPrompt(state, { range: '4.5' });
    tick(state);
    expect(root.dataset.visible).toBe('false');
  });

  it('skips stale (destroyed) target eids without throwing', () => {
    const ghost = state.createEntity();
    place(ghost, 1, 0);
    registerInteractionTarget(state, ghost, { label: 'Ghost' });
    state.destroyEntity(ghost);

    const root = mountPrompt(state, {
      range: '4.5',
      'player-eid': String(player),
    });
    expect(() => tick(state)).not.toThrow();
    expect(root.dataset.visible).toBe('false');
  });

  it('respects the prompt-range alias', () => {
    const merchant = state.createEntity();
    place(merchant, 3.2, 0);
    registerInteractionTarget(state, merchant, { label: 'Talk' });

    const root = mountPrompt(state, {
      'prompt-range': '3',
      'player-eid': String(player),
    });
    tick(state);
    expect(root.dataset.visible).toBe('false');
  });
});

describe('InteractionPrompt — label resolution', () => {
  let state: State;
  let player: number;

  beforeEach(() => {
    state = newState();
    player = state.createEntity();
    place(player, 0, 0);
    loadDictionary(state, 'en', {
      'hint.merchant': 'Talk to Merchant',
      'hint.harvest.wood': 'Harvest Wood',
    });
  });

  it('resolves i18n-key-template with the {kind} slot', () => {
    const tree = state.createEntity();
    place(tree, 1, 0);
    registerInteractionTarget(state, tree, { kind: 'wood' });

    const root = mountPrompt(state, {
      range: '4.5',
      key: 'K',
      'i18n-key-template': 'hint.harvest.{kind}',
      'player-eid': String(player),
    });
    tick(state);

    expect(labelOf(root)).toBe('Harvest Wood');
    expect(keyOf(root)).toBe('K');
  });

  it('falls back to the target i18nKey when no template is set', () => {
    const merchant = state.createEntity();
    place(merchant, 1, 0);
    registerInteractionTarget(state, merchant, { i18nKey: 'hint.merchant' });

    const root = mountPrompt(state, { 'player-eid': String(player) });
    tick(state);
    expect(labelOf(root)).toBe('Talk to Merchant');
  });

  it('a static target label wins over the template', () => {
    const tree = state.createEntity();
    place(tree, 1, 0);
    registerInteractionTarget(state, tree, { label: 'Chop', kind: 'wood' });

    const root = mountPrompt(state, {
      'i18n-key-template': 'hint.harvest.{kind}',
      'player-eid': String(player),
    });
    tick(state);
    expect(labelOf(root)).toBe('Chop');
  });

  it('per-target key overrides the widget key', () => {
    const merchant = state.createEntity();
    place(merchant, 1, 0);
    registerInteractionTarget(state, merchant, { label: 'Talk', key: 'J' });

    const root = mountPrompt(state, { key: 'K', 'player-eid': String(player) });
    tick(state);
    expect(keyOf(root)).toBe('J');
  });
});

describe('InteractionPrompt — registry', () => {
  it('register/unregister mutate the per-state target map', () => {
    const state = newState();
    const eid = state.createEntity();
    const info: InteractionTarget = { label: 'X' };
    expect(getInteractionTargets(state).has(eid)).toBe(false);

    registerInteractionTarget(state, eid, info);
    expect(getInteractionTargets(state).get(eid)).toBe(info);

    unregisterInteractionTarget(state, eid);
    expect(getInteractionTargets(state).has(eid)).toBe(false);
  });

  it('registry is scoped per State', () => {
    const a = newState();
    const b = newState();
    const ea = a.createEntity();
    registerInteractionTarget(a, ea, { label: 'A' });
    expect(getInteractionTargets(b).has(ea)).toBe(false);
  });
});

describe('InteractionPrompt — XML parser', () => {
  it('interactionPromptParser mounts a .hud-prompt on the screen layer', () => {
    const state = newState();
    const player = state.createEntity();
    place(player, 0, 0);
    const merchant = state.createEntity();
    place(merchant, 2, 0);
    registerInteractionTarget(state, merchant, { label: 'Talk' });

    const params = {
      element: {
        attributes: { range: '4.5', key: 'K', 'player-eid': String(player) },
      },
      state,
    } as unknown as ParserParams;

    interactionPromptParser(params);
    tick(state);

    const root =
      getHudScreenLayer(state).querySelector<HTMLDivElement>('.hud-prompt')!;
    expect(root).not.toBeNull();
    expect(root.dataset.visible).toBe('true');
    expect(labelOf(root)).toBe('Talk');
    expect(keyOf(root)).toBe('K');
  });
});
