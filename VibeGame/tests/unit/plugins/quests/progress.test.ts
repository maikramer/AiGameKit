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
  QuestProgressSystem,
  notifyEnemyKilled,
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
});

function getDef(state: State, id: string): QuestDef {
  const def = getDataRegistry(state).get<QuestDef>('quest', id);
  if (!def) throw new Error(`missing quest def ${id}`);
  return def;
}
