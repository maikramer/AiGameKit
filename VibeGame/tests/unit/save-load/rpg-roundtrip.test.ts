import { beforeEach, describe, expect, it } from 'bun:test';
import {
  CombatPlugin,
  Health,
  InventoryComponent,
  InventoryPlugin,
  ProjectileData,
  ProgressionComponent,
  ProgressionPlugin,
  RpgCorePlugin,
  RpgVaultPlugin,
  SaveLoadPlugin,
  State,
  StatusEffectComponent,
  StatusEffectsPlugin,
  addResource,
  addItem,
  applyStatus,
  deserializeAll,
  getActiveStatuses,
  getDataRegistry,
  getInventory,
  getResource,
  getSkillRank,
  registerRpgSaveSerializers,
  serializeAll,
  spendSkillPoint,
} from 'vibegame';

function makeState(): State {
  const state = new State();
  state.registerPlugin(RpgCorePlugin);
  state.registerPlugin(RpgVaultPlugin);
  state.registerPlugin(InventoryPlugin);
  state.registerPlugin(ProgressionPlugin);
  state.registerPlugin(StatusEffectsPlugin);
  state.registerPlugin(CombatPlugin);
  registerDefs(state);
  registerRpgSaveSerializers(state);
  return state;
}

function registerDefs(state: State): void {
  const registry = getDataRegistry(state);
  registry.register('item', 'potion', {
    id: 'potion',
    name: 'Potion',
    maxStack: 99,
  });
  registry.register('item', 'sword', {
    id: 'sword',
    name: 'Sword',
    maxStack: 1,
  });
  registry.register('skill', 'vitality', {
    id: 'vitality',
    name: 'Vitality',
    maxRank: 5,
    cost: 1,
    effect: {
      kind: 'stat-modifier',
      payload: {
        stat: 'maxHealth',
        magnitude: 10,
        stackMode: 'stack',
      },
    },
  });
  registry.register('status', 'poison', {
    id: 'poison',
    name: 'Poison',
    duration: 5,
    modifiers: [{ stat: 'health', magnitude: -5, stackMode: 'stack' }],
  });
}

describe('RPG save/load round-trip', () => {
  let source: State;
  let hero: number;

  beforeEach(() => {
    source = makeState();
    hero = source.createEntity();

    source.addComponent(hero, InventoryComponent, { capacity: 20 });
    source.addComponent(hero, ProgressionComponent);
    source.addComponent(hero, Health, { current: 80, max: 100 });

    addResource(source, hero, 'gold', 250);
    addResource(source, hero, 'wood', 30);

    addItem(source, hero, 'potion', 3);
    addItem(source, hero, 'sword', 1);

    ProgressionComponent.xp[hero] = 1250;
    ProgressionComponent.level[hero] = 5;
    ProgressionComponent.unspentPoints[hero] = 2;
    ProgressionComponent.spent[hero] = 0;
    expect(spendSkillPoint(source, hero, 'vitality')).toBe(true);
    expect(spendSkillPoint(source, hero, 'vitality')).toBe(true);

    applyStatus(source, hero, 'poison');
  });

  it('serializes every RPG system for the hero into a v1.0 snapshot', () => {
    const snapshot = serializeAll(source);

    expect(snapshot.version).toBe('1.0');
    const heroEntry = snapshot.entities.find((e) => e.eid === hero);
    expect(heroEntry).toBeDefined();
    expect(Object.keys(heroEntry!.kinds).sort()).toEqual(
      ['inventory', 'progression', 'status-effect', 'vault'].sort()
    );
  });

  it('restores vault, inventory, progression and status with exact equality', () => {
    const snapshot = serializeAll(source);
    const json = JSON.stringify(snapshot);
    const restored = JSON.parse(json) as typeof snapshot;

    const target = makeState();
    deserializeAll(target, restored);

    // Entity identity is stable for a fresh target created in snapshot order.
    const targetHero = hero;

    expect(getResource(target, targetHero, 'gold')).toBe(250);
    expect(getResource(target, targetHero, 'wood')).toBe(30);

    const inv = getInventory(target, targetHero).map((s) => ({
      itemId: s.itemId,
      qty: s.qty,
    }));
    expect(inv).toEqual([
      { itemId: 'potion', qty: 3 },
      { itemId: 'sword', qty: 1 },
    ]);

    expect(ProgressionComponent.xp[targetHero]).toBe(1250);
    expect(ProgressionComponent.level[targetHero]).toBe(5);
    expect(getSkillRank(target, targetHero, 'vitality')).toBe(2);

    const statuses = getActiveStatuses(target, targetHero);
    expect(statuses).toHaveLength(1);
    expect(statuses[0].defId).toBe('poison');
    expect(statuses[0].remainingTime).toBeCloseTo(5, 6);
  });

  it('survives a JSON string round-trip byte-for-byte (deep equality)', () => {
    const snapshot = serializeAll(source);
    const reparsed = JSON.parse(JSON.stringify(snapshot)) as typeof snapshot;

    const target = makeState();
    deserializeAll(target, reparsed);
    const resnapshot = serializeAll(target);

    expect(resnapshot).toEqual(snapshot);
  });
});

describe('RPG save/load transient exclusion', () => {
  it('excludes in-flight projectiles even when they carry a serializable component', () => {
    const state = makeState();
    const arrow = state.createEntity();
    state.addComponent(arrow, StatusEffectComponent);
    state.addComponent(arrow, ProjectileData);
    applyStatus(state, arrow, 'poison');

    const snapshot = serializeAll(state);
    const excluded = snapshot.entities.every((e) => e.eid !== arrow);
    expect(excluded).toBe(true);
  });

  it('excludes a bare projectile entity from the snapshot', () => {
    const state = makeState();
    const projectile = state.createEntity();
    state.addComponent(projectile, ProjectileData);

    const snapshot = serializeAll(state);
    expect(snapshot.entities.find((e) => e.eid === projectile)).toBeUndefined();
  });
});

describe('SaveLoadPlugin.initialize wiring', () => {
  it('auto-registers RPG serializers during plugin initialization', async () => {
    const state = new State();
    state.registerPlugin(RpgCorePlugin);
    state.registerPlugin(RpgVaultPlugin);
    state.registerPlugin(InventoryPlugin);
    state.registerPlugin(ProgressionPlugin);
    state.registerPlugin(StatusEffectsPlugin);
    state.registerPlugin(CombatPlugin);
    state.registerPlugin(SaveLoadPlugin);
    await state.initializePlugins();
    registerDefs(state);

    const hero = state.createEntity();
    state.addComponent(hero, ProgressionComponent);
    ProgressionComponent.level[hero] = 3;

    const snapshot = serializeAll(state);
    const entry = snapshot.entities.find((e) => e.eid === hero);
    expect(entry?.kinds.progression).toBeDefined();
  });
});
