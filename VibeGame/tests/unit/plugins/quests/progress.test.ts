import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { Transform } from '../../../../src/plugins/transforms';
import { PlayerController } from '../../../../src/plugins/player';
import {
  InventoryComponent,
  getItemQty,
} from '../../../../src/plugins/rpg-inventory';
import { ProgressionComponent } from '../../../../src/plugins/rpg-progression';
import {
  VaultComponent,
  getResource,
  registerResourceKind,
} from '../../../../src/plugins/rpg-vault';
import { GOLD_KIND } from '../../../../src/plugins/rpg-economy';
import { getDataRegistry } from '../../../../src/plugins/rpg-core';
import {
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import { acceptQuest } from '../../../../src/plugins/quests/dialogue';
import {
  LAST_SEEN_REANCHOR_DISTANCE,
  QuestProgressSystem,
  getLastSeenTarget,
  notifyEnemyKilled,
  notifyResourceHarvested,
} from '../../../../src/plugins/quests/systems';
import {
  registerQuest,
  type QuestDef,
} from '../../../../src/plugins/quests/registry';

function makeDef(id: string, target: string, count: number): QuestDef {
  return {
    id,
    npc: `${id}_npc`,
    title: id,
    lines_intro: [],
    lines_progress: ['Faltam {remaining}.'],
    lines_complete: [],
    objective: { type: 'kill', target, count },
    rewards: { gold: 200, xp: 150, items: [`${target}_pelt:2`] },
  };
}

describe('QuestProgressSystem kill matching + rewards', () => {
  let state: State;
  let player: number;
  let wolfIdx: number;
  let scorpionIdx: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player', PlayerController);
    state.registerComponent('quest-giver', QuestGiver);
    state.registerComponent('vault', VaultComponent);
    state.registerComponent('progression', ProgressionComponent);
    state.registerComponent('inventory', InventoryComponent);
    resetQuestState();

    player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);
    state.addComponent(player, VaultComponent);
    state.addComponent(player, ProgressionComponent);
    state.addComponent(player, InventoryComponent);
    InventoryComponent.capacity[player] = 20;
    registerResourceKind(state, GOLD_KIND);

    const itemReg = getDataRegistry(state);
    itemReg.register('item', 'wolf_pelt', {
      id: 'wolf_pelt',
      name: 'Wolf Pelt',
      maxStack: 99,
    });

    wolfIdx = registerQuest(state, makeDef('forest_wolves', 'wolf', 2));
    scorpionIdx = registerQuest(
      state,
      makeDef('desert_scorpions', 'scorpion', 3)
    );
  });

  it('increments progress only for the matching active quest', () => {
    const wolfNpc = state.createEntity();
    state.addComponent(wolfNpc, QuestGiver);
    QuestGiver.questId[wolfNpc] = wolfIdx;
    QuestGiver.state[wolfNpc] = QUEST_STATE_AVAILABLE;
    const scorpionNpc = state.createEntity();
    state.addComponent(scorpionNpc, QuestGiver);
    QuestGiver.questId[scorpionNpc] = scorpionIdx;
    QuestGiver.state[scorpionNpc] = QUEST_STATE_AVAILABLE;

    acceptQuest(state, wolfNpc, getDef(state, 'forest_wolves'));

    notifyEnemyKilled(state, 'wolf');
    notifyEnemyKilled(state, 'scorpion');
    QuestProgressSystem.update!(state);

    expect(QuestState.progress[wolfIdx]).toBe(1);
    expect(QuestState.progress[scorpionIdx]).toBe(0);
    expect(QuestState.active[wolfIdx]).toBe(1);
    expect(QuestState.active[scorpionIdx]).toBe(0);
  });

  it('completes the quest and applies gold/xp/item rewards', () => {
    const npc = state.createEntity();
    state.addComponent(npc, QuestGiver);
    QuestGiver.questId[npc] = wolfIdx;
    const def = getDef(state, 'forest_wolves');
    acceptQuest(state, npc, def);

    notifyEnemyKilled(state, 'wolf');
    notifyEnemyKilled(state, 'wolf');
    QuestProgressSystem.update!(state);

    expect(QuestState.completed[wolfIdx]).toBe(1);
    expect(getResource(state, player, GOLD_KIND)).toBe(200);
    expect(getItemQty(state, player, 'wolf_pelt')).toBe(2);
    // addXp mutates ProgressionComponent synchronously (its event is queued
    // for the progression bridge, which this unit test does not run).
    expect(
      ProgressionComponent.level[player] + ProgressionComponent.xp[player]
    ).toBeGreaterThan(0);
  });

  it('does not complete on a non-matching target', () => {
    const npc = state.createEntity();
    state.addComponent(npc, QuestGiver);
    QuestGiver.questId[npc] = wolfIdx;
    acceptQuest(state, npc, getDef(state, 'forest_wolves'));

    notifyEnemyKilled(state, 'scorpion');
    notifyEnemyKilled(state, 'scorpion');
    QuestProgressSystem.update!(state);

    expect(QuestState.progress[wolfIdx]).toBe(0);
    expect(QuestState.completed[wolfIdx]).toBe(0);
  });

  it('clamps progress at the goal and completes only once', () => {
    const npc = state.createEntity();
    state.addComponent(npc, QuestGiver);
    QuestGiver.questId[npc] = wolfIdx;
    acceptQuest(state, npc, getDef(state, 'forest_wolves'));

    for (let i = 0; i < 5; i++) notifyEnemyKilled(state, 'wolf');
    QuestProgressSystem.update!(state);

    expect(QuestState.progress[wolfIdx]).toBe(2);
    expect(QuestState.completed[wolfIdx]).toBe(1);
    expect(QuestGiver.state[npc]).toBe(2);
  });

  it('remembers the hunt area, not each individual kill', () => {
    expect(getLastSeenTarget(state, 'kill', 'wolf')).toBeNull();

    notifyEnemyKilled(state, 'wolf', { x: 1, y: 2, z: 3 });
    QuestProgressSystem.update!(state);
    expect(getLastSeenTarget(state, 'kill', 'wolf')).toEqual({
      x: 1,
      y: 2,
      z: 3,
    });

    // A kill inside the same area must not swing the marker around — the
    // arrow would change direction with every hit of a dying pack.
    notifyEnemyKilled(state, 'wolf', { x: 30, y: 0, z: 10 });
    QuestProgressSystem.update!(state);
    expect(getLastSeenTarget(state, 'kill', 'wolf')).toEqual({
      x: 1,
      y: 2,
      z: 3,
    });

    // A kill clearly beyond the current area re-anchors it.
    notifyEnemyKilled(state, 'wolf', {
      x: 1 + LAST_SEEN_REANCHOR_DISTANCE + 5,
      y: 0,
      z: 3,
    });
    QuestProgressSystem.update!(state);
    expect(getLastSeenTarget(state, 'kill', 'wolf')!.x).toBe(
      LAST_SEEN_REANCHOR_DISTANCE + 6
    );
    expect(getLastSeenTarget(state, 'collect', 'wolf')).toBeNull();
  });

  it('records harvest sites under the collect kind', () => {
    notifyResourceHarvested(state, 'stone', { x: 4, y: 0, z: 2 });
    QuestProgressSystem.update!(state);
    expect(getLastSeenTarget(state, 'collect', 'stone')).toEqual({
      x: 4,
      y: 0,
      z: 2,
    });
  });
});

function getDef(state: State, id: string): QuestDef {
  const def = getDataRegistry(state).get<QuestDef>('quest', id);
  if (!def) throw new Error(`missing quest def ${id}`);
  return def;
}

describe('QuestProgressSystem kill/collect kind gate', () => {
  let state: State;
  let player: number;
  let idx: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player', PlayerController);
    state.registerComponent('quest-giver', QuestGiver);
    state.registerComponent('vault', VaultComponent);
    state.registerComponent('progression', ProgressionComponent);
    state.registerComponent('inventory', InventoryComponent);
    player = state.createEntity();
    state.addComponent(player, PlayerController);
    state.addComponent(player, Transform);

    const def = makeDef('collect_wolf', 'wolf', 1);
    idx = registerQuest(state, {
      ...def,
      objective: { type: 'collect', target: 'wolf', count: 1 },
    });
  });

  it('a harvest report does not advance a kill objective with the same target name', () => {
    const npc = state.createEntity();
    state.addComponent(npc, QuestGiver);
    QuestGiver.questId[npc] = idx;
    QuestGiver.state[npc] = QUEST_STATE_AVAILABLE;
    // The registered def already has a `collect` objective (see beforeEach).
    acceptQuest(state, npc, getDef(state, 'collect_wolf'));

    // Kill report for the same name must NOT advance the collect objective.
    notifyEnemyKilled(state, 'wolf');
    QuestProgressSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
    expect(QuestState.completed[idx]).toBe(0);

    // The matching harvest report does.
    notifyResourceHarvested(state, 'wolf');
    QuestProgressSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
    expect(QuestState.completed[idx]).toBe(1);
  });
});
