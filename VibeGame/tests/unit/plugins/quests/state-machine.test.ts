import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { Transform } from '../../../../src/plugins/transforms';
import { PlayerController } from '../../../../src/plugins/player';
import {
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
  QUEST_STATE_TAKEN,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import {
  acceptQuest,
  endDialogue,
  getActiveDialogue,
  showDialogue,
} from '../../../../src/plugins/quests/dialogue';
import {
  QuestProgressSystem,
  QUEST_COMPLETED,
  notifyEnemyKilled,
} from '../../../../src/plugins/quests/systems';
import {
  getQuestIndex,
  registerQuest,
  type QuestDef,
} from '../../../../src/plugins/quests/registry';
import { onEvent } from '../../../../src/plugins/rpg-core';

function makeDef(overrides: Partial<QuestDef> = {}): QuestDef {
  return {
    id: 'forest_wolves',
    npc: 'hunter_npc',
    biome: 'dark-forest',
    title: 'Caçador da Floresta',
    portrait: '/assets/ui/hunter.png',
    voice: 'npc_speak_low',
    lines_intro: ['Lobos mataram meu gado.'],
    lines_progress: ['Faltam {remaining} lobos.'],
    lines_complete: ['Obrigado!'],
    objective: { type: 'kill', target: 'wolf', count: 2 },
    rewards: { gold: 200, xp: 150, items: ['wolf_pelt:2'] },
    ...overrides,
  };
}

describe('quest dialogue state machine', () => {
  let state: State;
  let player: number;
  let npc: number;
  let def: QuestDef;
  let idx: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player', PlayerController);
    state.registerComponent('quest-giver', QuestGiver);
    resetQuestState();

    player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);

    npc = state.createEntity();
    state.addComponent(npc, Transform);
    state.addComponent(npc, QuestGiver);

    def = makeDef();
    idx = registerQuest(state, def);
    QuestGiver.questId[npc] = idx;
    QuestGiver.state[npc] = QUEST_STATE_AVAILABLE;
  });

  it('opens a dialogue and clears it on endDialogue', () => {
    expect(getActiveDialogue(state)).toBeNull();
    showDialogue(state, { speakerEid: npc, def, phase: 'intro' });
    const active = getActiveDialogue(state);
    expect(active).not.toBeNull();
    expect(active!.speakerEid).toBe(npc);
    endDialogue(state);
    expect(getActiveDialogue(state)).toBeNull();
  });

  it('transitions a giver from available to taken on acceptQuest', () => {
    showDialogue(state, { speakerEid: npc, def, phase: 'intro' });
    acceptQuest(state, npc, def);
    endDialogue(state);

    expect(QuestGiver.state[npc]).toBe(QUEST_STATE_TAKEN);
    expect(QuestState.active[idx]).toBe(1);
    expect(QuestState.progress[idx]).toBe(0);
    expect(QuestState.completed[idx]).toBe(0);
  });

  it('completes the quest (taken -> completed) when the kill goal is reached', () => {
    acceptQuest(state, npc, def);

    const captured = { value: null as string | null };
    onEvent(state, QUEST_COMPLETED, (payload) => {
      captured.value = (payload as { questId: string }).questId;
    });

    notifyEnemyKilled(state, 'wolf');
    QuestProgressSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
    expect(QuestState.completed[idx]).toBe(0);
    expect(QuestGiver.state[npc]).toBe(QUEST_STATE_TAKEN);

    notifyEnemyKilled(state, 'wolf');
    QuestProgressSystem.update!(state);

    expect(QuestState.progress[idx]).toBe(2);
    expect(QuestState.completed[idx]).toBe(1);
    expect(QuestState.active[idx]).toBe(0);
    expect(QuestGiver.state[npc]).toBe(QUEST_STATE_COMPLETED);
    expect(captured.value).toBe(def.id);
  });

  it('does not advance a quest that has not been accepted', () => {
    notifyEnemyKilled(state, 'wolf');
    notifyEnemyKilled(state, 'wolf');
    QuestProgressSystem.update!(state);

    expect(QuestState.progress[idx]).toBe(0);
    expect(QuestState.completed[idx]).toBe(0);
    expect(QuestGiver.state[npc]).toBe(QUEST_STATE_AVAILABLE);
  });

  it('getQuestIndex returns -1 for unknown ids', () => {
    expect(getQuestIndex(state, 'does_not_exist')).toBe(-1);
  });
});
