import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  InventoryComponent,
  InventoryPlugin,
  isPaused,
  ProgressionComponent,
  ProgressionPlugin,
  RpgCoreEventsPlugin,
  RpgCorePlugin,
  PauseCoordinatorPlugin,
  State,
  Transform,
  I18nPlugin,
  onEvent,
} from 'vibegame';
import { TransformsPlugin } from '../../../src/plugins/transforms';
import type { ParserParams } from '../../../src/core';
import { getDataRegistry } from '../../../src/plugins/rpg-core';
import { loadDictionary } from '../../../src/plugins/i18n/utils';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import {
  getHudScreenLayer,
  registerHudWidget,
} from '../../../src/plugins/hud/screen-layer';
import {
  MODAL_OPTION_CHANGED,
  createOptionsTab,
  getOptionValue,
  parseOptionDef,
  registerOptionDef,
  setOptionValue,
} from '../../../src/plugins/hud/widgets/options-tab';
import { createSkillsTab } from '../../../src/plugins/hud/widgets/skills-tab';
import { createInventoryTab } from '../../../src/plugins/hud/widgets/inventory-tab';
import {
  TABBED_MODAL_TAG,
  buildTabsFromChildren,
  closeModal,
  createTabbedModalWidget,
  isModalOpen,
  openModal,
  tabbedModalParser,
  tabbedModalRecipe,
  toggleModal,
} from '../../../src/plugins/hud/widgets/tabbed-modal';
import { addItem } from '../../../src/plugins/rpg-inventory/systems';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser as unknown as typeof DOMParser;
  globalThis.document = dom.window.document as unknown as typeof document;
  globalThis.window = dom.window as unknown as typeof window;
  globalThis.HTMLElement = dom.window
    .HTMLElement as unknown as typeof HTMLElement;
  globalThis.HTMLDivElement = dom.window
    .HTMLDivElement as unknown as typeof HTMLDivElement;
  globalThis.HTMLInputElement = dom.window
    .HTMLInputElement as unknown as typeof HTMLInputElement;
  globalThis.HTMLButtonElement = dom.window
    .HTMLButtonElement as unknown as typeof HTMLButtonElement;
  globalThis.Event = dom.window.Event as unknown as typeof Event;
  globalThis.KeyboardEvent = dom.window
    .KeyboardEvent as unknown as typeof KeyboardEvent;
  globalThis.Node = dom.window.Node as unknown as typeof Node;
});

async function newState(): Promise<State> {
  const state = new State();
  state.registerPlugin(TransformsPlugin);
  state.registerPlugin(RpgCorePlugin);
  state.registerPlugin(RpgCoreEventsPlugin);
  state.registerPlugin(PauseCoordinatorPlugin);
  state.registerPlugin(ProgressionPlugin);
  state.registerPlugin(InventoryPlugin);
  state.registerPlugin(I18nPlugin);
  state.registerPlugin(HudPlugin);
  await state.initializePlugins();
  loadDictionary(state, 'en', {
    'modal.tab.skills': 'Skills',
    'modal.tab.inventory': 'Inventory',
    'modal.tab.options': 'Options',
    'modal.tab.menutab': 'Menu',
    'modal.skillPoints': '{n} points',
    'options.on': 'On',
    'options.off': 'Off',
  });
  return state;
}

function makeHero(state: State): number {
  const eid = state.createEntity();
  state.setEntityName('hero', eid);
  state.addComponent(eid, Transform);
  state.addComponent(eid, ProgressionComponent);
  ProgressionComponent.unspentPoints[eid] = 3;
  state.addComponent(eid, InventoryComponent);
  InventoryComponent.capacity[eid] = 10;
  return eid;
}

function pressKey(key: string): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key }));
}

describe('TabbedModal — surface', () => {
  it('recipe is named TabbedModal and owns its children', () => {
    expect(tabbedModalRecipe.name).toBe(TABBED_MODAL_TAG);
    expect(tabbedModalRecipe.parserOwnsChildren).toBe(true);
    expect(tabbedModalRecipe.parserAttributes).toContain('id');
    expect(tabbedModalRecipe.parserAttributes).toContain('pause-on-open');
    expect(tabbedModalRecipe.parserAttributes).toContain('key');
  });

  it('HudPlugin exposes the TabbedModal recipe and parser', () => {
    const names = (HudPlugin.recipes ?? []).map((r) => r.name);
    expect(names).toContain(TABBED_MODAL_TAG);
    expect(HudPlugin.config?.parsers?.TabbedModal).toBe(tabbedModalParser);
  });

  it('widget id is unique per modal id', () => {
    const state = new State();
    const a = createTabbedModalWidget({ id: 'pause' }, state);
    const b = createTabbedModalWidget({ id: 'settings' }, state);
    expect(a.id).toBe('vibe:tabbed-modal:pause');
    expect(b.id).toBe('vibe:tabbed-modal:settings');
  });
});

describe('TabbedModal — pause toggle via Escape key', () => {
  let state: State;
  let overlay: HTMLElement;

  beforeEach(async () => {
    state = await newState();
    makeHero(state);
    registerHudWidget(state, createTabbedModalWidget({ id: 'pause' }, state));
    overlay =
      getHudScreenLayer(state).querySelector<HTMLElement>(
        '.hud-modal-overlay'
      )!;
    expect(overlay).not.toBeNull();
  });

  it('starts hidden with timeScale=1', () => {
    expect(overlay.dataset.open).toBe('false');
    expect(isPaused(state)).toBe(false);
    expect(state.time.timeScale).toBe(1);
  });

  it('Escape opens the modal and pauses (pushModal → timeScale=0)', () => {
    pressKey('Escape');
    expect(overlay.dataset.open).toBe('true');
    expect(isPaused(state)).toBe(true);
    expect(state.time.timeScale).toBe(0);
  });

  it('Escape again closes the modal and resumes (popModal → timeScale=1)', () => {
    pressKey('Escape');
    pressKey('Escape');
    expect(overlay.dataset.open).toBe('false');
    expect(isPaused(state)).toBe(false);
    expect(state.time.timeScale).toBe(1);
  });

  it('openModal/closeModal/toggleModal helpers drive the same controller', () => {
    openModal(state, 'pause');
    expect(isModalOpen(state, 'pause')).toBe(true);
    expect(isPaused(state)).toBe(true);
    closeModal(state, 'pause');
    expect(isModalOpen(state, 'pause')).toBe(false);
    toggleModal(state, 'pause');
    expect(isModalOpen(state, 'pause')).toBe(true);
  });

  it('pause-on-open="false" shows modal without pausing', async () => {
    const s = await newState();
    makeHero(s);
    registerHudWidget(
      s,
      createTabbedModalWidget({ id: 'nopause', 'pause-on-open': 'false' }, s)
    );
    openModal(s, 'nopause');
    expect(isModalOpen(s, 'nopause')).toBe(true);
    expect(isPaused(s)).toBe(false);
    expect(s.time.timeScale).toBe(1);
  });
});

describe('TabbedModal — skills tab', () => {
  let state: State;
  let hero: number;

  beforeEach(async () => {
    state = await newState();
    hero = makeHero(state);
    getDataRegistry(state).register('skill', 'vitality', {
      id: 'vitality',
      name: 'Vitality',
      maxRank: 5,
      cost: 1,
      effect: {
        kind: 'stat-modifier',
        payload: { stat: 'maxHealth', magnitude: 10, stackMode: 'stack' },
      },
    });
  });

  it('renders rank 0 and unspent points', () => {
    const tab = createSkillsTab(state, { targetEntity: hero });
    expect(tab.root.querySelector('.hud-modal-skill-name')!.textContent).toBe(
      'Vitality'
    );
    expect(tab.root.querySelector('.hud-modal-skill-rank')!.textContent).toBe(
      '0'
    );
    expect(
      tab.root.querySelector('.hud-modal-skill-points')!.textContent
    ).toContain('3');
  });

  it('clicking + spends a skill point (rank up, points down)', () => {
    const tab = createSkillsTab(state, { targetEntity: hero });
    const plus = tab.root.querySelector<HTMLButtonElement>(
      '.hud-modal-skill-plus'
    )!;
    expect(plus.disabled).toBe(false);
    plus.click();
    expect(tab.root.querySelector('.hud-modal-skill-rank')!.textContent).toBe(
      '1'
    );
    expect(
      tab.root.querySelector('.hud-modal-skill-points')!.textContent
    ).toContain('2');
    expect(ProgressionComponent.unspentPoints[hero]).toBe(2);
  });

  it('disables + when no unspent points remain', () => {
    ProgressionComponent.unspentPoints[hero] = 0;
    const tab = createSkillsTab(state, { targetEntity: hero });
    const plus = tab.root.querySelector<HTMLButtonElement>(
      '.hud-modal-skill-plus'
    )!;
    expect(plus.disabled).toBe(true);
  });
});

describe('TabbedModal — inventory tab', () => {
  let state: State;
  let hero: number;

  beforeEach(async () => {
    state = await newState();
    hero = makeHero(state);
    getDataRegistry(state).register('item', 'potion', {
      id: 'potion',
      name: 'Potion',
      icon: '🧪',
      maxStack: 99,
      tags: ['consumable'],
    });
  });

  it('shows empty message when bag is empty', () => {
    const tab = createInventoryTab(state, { targetEntity: hero });
    expect(
      (tab.root.querySelector('.hud-modal-inv-empty') as HTMLElement)!.style
        .display
    ).toBe('block');
  });

  it('renders filled slots up to capacity after addItem', () => {
    addItem(state, hero, 'potion', 3);
    const tab = createInventoryTab(state, { targetEntity: hero });
    const slots = tab.root.querySelectorAll<HTMLElement>(
      '.hud-modal-inventory > div > div'
    );
    expect(slots.length).toBe(10);
    const first = slots[0];
    expect(first.title).toContain('Potion ×3');
    expect(first.querySelector('.hud-modal-inv-qty')!.textContent).toBe('3');
  });
});

describe('TabbedModal — options tab', () => {
  let state: State;

  beforeEach(async () => {
    state = await newState();
  });

  it('getOptionValue returns the default; setOptionValue updates it', () => {
    registerOptionDef(state, {
      id: 'music',
      labelKey: 'opt.music',
      type: 'cycle',
      values: ['0', '50', '100'],
      default: '50',
    });
    expect(getOptionValue(state, 'music')).toBe('50');
    setOptionValue(state, 'music', '100');
    expect(getOptionValue(state, 'music')).toBe('100');
  });

  it('emits MODAL_OPTION_CHANGED on setOptionValue', () => {
    registerOptionDef(state, {
      id: 'lang',
      labelKey: 'opt.lang',
      type: 'cycle',
      values: ['en', 'pt'],
    });
    const seen: { id: string; value: string }[] = [];
    onEvent(state, MODAL_OPTION_CHANGED, (p) =>
      seen.push(p as { id: string; value: string })
    );
    setOptionValue(state, 'lang', 'pt');
    expect(seen).toEqual([{ id: 'lang', value: 'pt' }]);
  });

  it('cycle row click advances to the next value', () => {
    const tab = createOptionsTab(state, [
      {
        id: 'quality',
        labelKey: 'opt.quality',
        type: 'cycle',
        values: ['low', 'med', 'high'],
        default: 'low',
      },
    ]);
    const valueEl = tab.root.querySelector<HTMLElement>(
      '.hud-modal-option-value'
    )!;
    expect(valueEl.textContent).toBe('low');
    tab.root.querySelector<HTMLButtonElement>('.hud-modal-option')!.click();
    expect(getOptionValue(state, 'quality')).toBe('med');
  });

  it('toggle row flips between On/Off', () => {
    const tab = createOptionsTab(state, [
      {
        id: 'subtitles',
        labelKey: 'opt.subtitles',
        type: 'toggle',
        default: 'false',
      },
    ]);
    const valueEl = tab.root.querySelector<HTMLElement>(
      '.hud-modal-option-value'
    )!;
    expect(valueEl.textContent).toBe('Off');
    tab.root.querySelector<HTMLButtonElement>('.hud-modal-option')!.click();
    expect(getOptionValue(state, 'subtitles')).toBe('true');
  });

  it('slider row renders a range input and updates on input', () => {
    const tab = createOptionsTab(state, [
      {
        id: 'vol',
        labelKey: 'opt.vol',
        type: 'slider',
        min: 0,
        max: 100,
        step: 10,
        default: 50,
      },
    ]);
    const slider = tab.root.querySelector<HTMLInputElement>(
      'input[type="range"]'
    )!;
    expect(slider).not.toBeNull();
    expect(slider.min).toBe('0');
    expect(slider.max).toBe('100');
    slider.value = '80';
    slider.dispatchEvent(new Event('input'));
    expect(getOptionValue(state, 'vol')).toBe('80');
  });

  it('parseOptionDef reads cycle values from comma-separated string', () => {
    const def = parseOptionDef({
      id: 'x',
      'label-key': 'opt.x',
      type: 'cycle',
      values: 'a, b ,c',
    });
    expect(def.values).toEqual(['a', 'b', 'c']);
  });
});

describe('TabbedModal — buildTabsFromChildren (XML)', () => {
  let state: State;
  let hero: number;

  beforeEach(async () => {
    state = await newState();
    hero = makeHero(state);
    getDataRegistry(state).register('skill', 'vitality', {
      id: 'vitality',
      name: 'Vitality',
      maxRank: 3,
      cost: 1,
      effect: {
        kind: 'stat-modifier',
        payload: { stat: 'hp', magnitude: 5, stackMode: 'stack' },
      },
    });
  });

  it('builds SkillsTab + OptionsTab descriptors from child elements', () => {
    const children = [
      {
        tagName: 'SkillsTab',
        attributes: { 'label-key': 'modal.tab.skills' },
        children: [],
      },
      {
        tagName: 'OptionsTab',
        attributes: { 'label-key': 'modal.tab.options' },
        children: [
          {
            tagName: 'OptionRow',
            attributes: {
              id: 'music',
              'label-key': 'opt.music',
              type: 'cycle',
              values: 'low,high',
            },
            children: [],
          },
        ],
      },
    ];
    const tabs = buildTabsFromChildren(state, 'pause', children as never, hero);
    expect(tabs).toHaveLength(2);
    expect(tabs[0].id).toBe('skillstab');
    expect(tabs[1].id).toBe('optionstab');
    expect(getOptionValue(state, 'music')).toBe('low');
  });

  it('tabbedModalParser mounts an overlay with tabs from element children', () => {
    const element = {
      tagName: 'TabbedModal',
      attributes: { id: 'pause', 'target-entity': 'hero' },
      children: [
        {
          tagName: 'SkillsTab',
          attributes: { 'label-key': 'modal.tab.skills' },
          children: [],
        },
      ],
    };
    const params = {
      element,
      state,
      entity: 0,
      context: {},
    } as unknown as ParserParams;
    tabbedModalParser(params);
    const overlay =
      getHudScreenLayer(state).querySelector<HTMLElement>('.hud-modal-overlay');
    expect(overlay).not.toBeNull();
  });
});

describe('TabbedModal — unmount cleans up pause + key listener', () => {
  it('popModal on unmount while open and removes the overlay', async () => {
    const state = await newState();
    makeHero(state);
    const widget = createTabbedModalWidget({ id: 'pause' }, state);
    const handle = widget.mount(getHudScreenLayer(state), state);
    openModal(state, 'pause');
    expect(isPaused(state)).toBe(true);
    handle.unmount();
    expect(isPaused(state)).toBe(false);
    expect(
      getHudScreenLayer(state).querySelector('.hud-modal-overlay')
    ).toBeNull();
  });
});
