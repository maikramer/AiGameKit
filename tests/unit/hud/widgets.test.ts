import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  addResource,
  addXp,
  bindCombatState,
  CombatPlugin,
  damageHealth,
  Health,
  HudPlugin,
  I18nPlugin,
  ProgressionComponent,
  ProgressionPlugin,
  getResource,
  RpgVaultPlugin,
  State,
  Transform,
  VaultComponent,
  getXpToNextLevel,
} from 'vibegame';
import { TransformsPlugin } from '../../../src/plugins/transforms';
import {
  createBossBarWidget,
  createControlsBarWidget,
  createHealthBarWidget,
  createMissionWidget,
  createResourceChipWidget,
  createTimerWidget,
  createXpBarWidget,
  registerHudWidgetFactories,
} from '../../../src/plugins/hud/widgets';
import {
  getHudWidgetFactory,
  getHudScreenLayer,
  registerHudWidget,
} from '../../../src/plugins/hud/screen-layer';
import {
  BOSS_BAR_TAG,
  CONTROLS_BAR_TAG,
  HEALTH_BAR_TAG,
  MISSION_TAG,
  RESOURCE_CHIP_TAG,
  TIMER_TAG,
  widgetParsers,
  widgetRecipes,
  XP_BAR_TAG,
} from '../../../src/plugins/hud/widgets';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser as unknown as typeof DOMParser;
  globalThis.document = dom.window.document as unknown as typeof document;
  globalThis.window = dom.window as unknown as typeof window;
  globalThis.HTMLElement = dom.window.HTMLElement as unknown as typeof HTMLElement;
  globalThis.HTMLDivElement = dom.window.HTMLDivElement as unknown as typeof HTMLDivElement;
});

async function newState(): Promise<State> {
  const state = new State();
  state.registerPlugin(TransformsPlugin);
  state.registerPlugin(CombatPlugin);
  state.registerPlugin(RpgVaultPlugin);
  state.registerPlugin(ProgressionPlugin);
  state.registerPlugin(I18nPlugin);
  state.registerPlugin(HudPlugin);
  bindCombatState(state);
  await state.initializePlugins();
  return state;
}

function makeHero(state: State): number {
  const eid = state.createEntity();
  state.setEntityName('hero', eid);
  state.addComponent(eid, Health);
  Health.max[eid] = 100;
  Health.current[eid] = 100;
  state.addComponent(eid, ProgressionComponent);
  state.addComponent(eid, VaultComponent);
  state.addComponent(eid, Transform);
  return eid;
}

describe('HudPlugin widget wiring', () => {
  it('HudPlugin exposes the 7 widget recipes', () => {
    const names = (HudPlugin.recipes ?? []).map((r) => r.name);
    expect(names).toContain(HEALTH_BAR_TAG);
    expect(names).toContain(XP_BAR_TAG);
    expect(names).toContain(RESOURCE_CHIP_TAG);
    expect(names).toContain(MISSION_TAG);
    expect(names).toContain(TIMER_TAG);
    expect(names).toContain(BOSS_BAR_TAG);
    expect(names).toContain(CONTROLS_BAR_TAG);
  });

  it('HudPlugin exposes a parser for each widget tag', () => {
    const parsers = HudPlugin.config?.parsers ?? {};
    expect(parsers[HEALTH_BAR_TAG]).toBeDefined();
    expect(parsers[XP_BAR_TAG]).toBeDefined();
    expect(parsers[RESOURCE_CHIP_TAG]).toBeDefined();
    expect(parsers[MISSION_TAG]).toBeDefined();
    expect(parsers[TIMER_TAG]).toBeDefined();
    expect(parsers[BOSS_BAR_TAG]).toBeDefined();
    expect(parsers[CONTROLS_BAR_TAG]).toBeDefined();
  });

  it('widgetRecipes and widgetParsers cover the same tag set', () => {
    const recipeNames = widgetRecipes.map((r) => r.name);
    for (const tag of Object.keys(widgetParsers)) {
      expect(recipeNames).toContain(tag);
    }
  });
});

describe('registerHudWidgetFactories', () => {
  it('registers a factory for each widget type', async () => {
    const state = await newState();
    registerHudWidgetFactories();
    expect(getHudWidgetFactory('health-bar')).toBeDefined();
    expect(getHudWidgetFactory('xp-bar')).toBeDefined();
    expect(getHudWidgetFactory('resource-chip')).toBeDefined();
    expect(getHudWidgetFactory('mission')).toBeDefined();
    expect(getHudWidgetFactory('timer')).toBeDefined();
    expect(getHudWidgetFactory('boss-bar')).toBeDefined();
    expect(getHudWidgetFactory('controls-bar')).toBeDefined();
    void state;
  });
});

describe('HealthBarWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(async () => {
    state = await newState();
    layer = getHudScreenLayer(state);
  });

  it('mount renders .hud-health with 100/100 initially', () => {
    const hero = makeHero(state);
    const widget = createHealthBarWidget({ 'target-entity': 'hero' }, state);
    registerHudWidget(state, widget);
    const root = layer.querySelector('.hud-health') as HTMLElement | null;
    expect(root).not.toBeNull();
    expect(root!.querySelector('.hud-health-text')!.textContent).toBe('100/100');
    expect(hero).toBeGreaterThanOrEqual(0);
  });

  it('update reflects damageHealth immediately', () => {
    const hero = makeHero(state);
    const widget = createHealthBarWidget({ 'target-entity': 'hero' }, state);
    registerHudWidget(state, widget);
    damageHealth(hero, 30);
    state.step(0.016);
    const text = layer.querySelector('.hud-health-text')!.textContent;
    expect(text).toBe('70/100');
  });

  it('unmount removes the root from the layer', () => {
    makeHero(state);
    const widget = createHealthBarWidget({ 'target-entity': 'hero' }, state);
    registerHudWidget(state, widget);
    expect(layer.querySelector('.hud-health')).not.toBeNull();
    widget.mount(layer, state).unmount();
    expect(layer.querySelector('.hud-health')).toBeNull();
  });
});

describe('XpBarWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(async () => {
    state = await newState();
    layer = getHudScreenLayer(state);
  });

  it('mount renders .hud-xp with level 1 and 0% fill', () => {
    makeHero(state);
    const widget = createXpBarWidget({}, state);
    registerHudWidget(state, widget);
    const root = layer.querySelector('.hud-xp') as HTMLElement | null;
    expect(root).not.toBeNull();
    expect(root!.querySelector('.hud-xp-level')!.textContent).toBe('1');
    expect(root!.querySelector('.hud-xp-fill')!.getAttribute('style')).toContain('0%');
  });

  it('addXp grows the fill; level-up resets the bar', () => {
    const hero = makeHero(state);
    const widget = createXpBarWidget({}, state);
    registerHudWidget(state, widget);
    const needed = getXpToNextLevel(state, hero);
    expect(needed).toBe(6);
    addXp(state, hero, 3);
    state.step(0.016);
    const fill = layer.querySelector('.hud-xp-fill') as HTMLElement;
    expect(fill.getAttribute('style')).toContain('50%');
    addXp(state, hero, 3);
    state.step(0.016);
    expect(layer.querySelector('.hud-xp-level')!.textContent).toBe('2');
    expect(layer.querySelector('.hud-xp-fill')!.getAttribute('style')).toContain('0%');
  });
});

describe('ResourceChipWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(async () => {
    state = await newState();
    layer = getHudScreenLayer(state);
  });

  it('mount renders .hud-resource-gold with 0', () => {
    makeHero(state);
    const widget = createResourceChipWidget(
      { resource: 'gold', icon: '🪙' },
      state,
    );
    registerHudWidget(state, widget);
    const root = layer.querySelector('.hud-resource-gold') as HTMLElement | null;
    expect(root).not.toBeNull();
    expect(root!.querySelector('.hud-resource-value')!.textContent).toBe('0');
  });

  it('addResource updates the chip value', () => {
    const hero = makeHero(state);
    const widget = createResourceChipWidget(
      { resource: 'gold', icon: '🪙' },
      state,
    );
    registerHudWidget(state, widget);
    addResource(state, hero, 'gold', 50);
    state.step(0.016);
    expect(getResource(state, hero, 'gold')).toBe(50);
    expect(layer.querySelector('.hud-resource-gold .hud-resource-value')!.textContent).toBe('50');
  });

  it('supports wood and stone resource kinds', () => {
    makeHero(state);
    registerHudWidget(
      state,
      createResourceChipWidget({ resource: 'wood', icon: '🪵' }, state),
    );
    registerHudWidget(
      state,
      createResourceChipWidget({ resource: 'stone', icon: '🪨' }, state),
    );
    expect(layer.querySelector('.hud-resource-wood')).not.toBeNull();
    expect(layer.querySelector('.hud-resource-stone')).not.toBeNull();
  });
});

describe('MissionWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(async () => {
    state = await newState();
    layer = getHudScreenLayer(state);
  });

  it('mount renders .hud-mission with title and body spans', () => {
    const widget = createMissionWidget({}, state);
    registerHudWidget(state, widget);
    const root = layer.querySelector('.hud-mission') as HTMLElement | null;
    expect(root).not.toBeNull();
    expect(root!.querySelector('.hud-mission-title')).not.toBeNull();
    expect(root!.querySelector('.hud-mission-body')).not.toBeNull();
  });
});

describe('TimerWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(async () => {
    state = await newState();
    layer = getHudScreenLayer(state);
  });

  it('mount renders .hud-timer with m:ss format', () => {
    const widget = createTimerWidget({}, state);
    registerHudWidget(state, widget);
    const root = layer.querySelector('.hud-timer') as HTMLElement | null;
    expect(root).not.toBeNull();
    state.time.realtimeSinceStartup = 125;
    state.step(0.016);
    const value = layer.querySelector('.hud-timer .hud-resource-value')!.textContent;
    expect(value).toBe('2:05');
  });
});

describe('BossBarWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(async () => {
    state = await newState();
    layer = getHudScreenLayer(state);
  });

  function makeBoss(state: State, x: number, z: number): number {
    const eid = state.createEntity();
    state.setEntityName('boss', eid);
    state.addComponent(eid, Health);
    Health.max[eid] = 200;
    Health.current[eid] = 200;
    state.addComponent(eid, Transform);
    Transform.posX[eid] = x;
    Transform.posZ[eid] = z;
    return eid;
  }

  it('is hidden when boss is out of range', () => {
    makeHero(state);
    makeBoss(state, 200, 0);
    const widget = createBossBarWidget({ range: '50' }, state);
    registerHudWidget(state, widget);
    state.step(0.016);
    const root = layer.querySelector('.hud-boss') as HTMLElement;
    expect(root.style.display).toBe('none');
  });

  it('becomes visible when boss moves within range', () => {
    const hero = makeHero(state);
    makeBoss(state, 200, 0);
    const widget = createBossBarWidget({ range: '50' }, state);
    registerHudWidget(state, widget);
    state.step(0.016);
    const root = layer.querySelector('.hud-boss') as HTMLElement;
    expect(root.style.display).toBe('none');
    const boss = state.getEntityByName('boss')!;
    Transform.posX[boss] = Transform.posX[hero] + 40;
    state.step(0.016);
    expect(root.style.display).toBe('block');
    const text = layer.querySelector('.hud-boss-text')!.textContent;
    expect(text).toContain('200/200');
  });
});

describe('ControlsBarWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(async () => {
    state = await newState();
    layer = getHudScreenLayer(state);
  });

  it('mount renders .hud-controls', () => {
    const widget = createControlsBarWidget({}, state);
    registerHudWidget(state, widget);
    expect(layer.querySelector('.hud-controls')).not.toBeNull();
  });
});
