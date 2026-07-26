import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { Transform } from '../../../../src/plugins/transforms';
import { PlayerController } from '../../../../src/plugins/player';
import { ProgressionComponent } from '../../../../src/plugins/rpg-progression';
import {
  VaultComponent,
  getResource,
  registerResourceKind,
} from '../../../../src/plugins/rpg-vault';
import { GOLD_KIND } from '../../../../src/plugins/rpg-economy';
import {
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import { acceptQuest } from '../../../../src/plugins/quests/dialogue';
import { QuestVisitSystem } from '../../../../src/plugins/quests/systems';
import {
  registerQuest,
  getQuestDef,
  type QuestDef,
} from '../../../../src/plugins/quests/registry';

/**
 * `visit` objectives: reach a named landmark. Unlike kill/collect there is no
 * game script pushing an event, so without this system a quest pointing at a
 * point of interest could never complete.
 */
function visitDef(
  id: string,
  target: string,
  count: number,
  radius?: number
): QuestDef {
  return {
    id,
    npc: `${id}_npc`,
    title: id,
    lines_intro: [],
    lines_progress: ['Faltam {remaining}.'],
    lines_complete: [],
    objective: { type: 'visit', target, count, radius },
    rewards: { gold: 120, xp: 90 },
  };
}

function landmark(state: State, name: string, x: number, z: number): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  Transform.posX[eid] = x;
  Transform.posZ[eid] = z;
  state.setEntityName(name, eid);
  return eid;
}

function movePlayer(player: number, x: number, z: number): void {
  Transform.posX[player] = x;
  Transform.posZ[player] = z;
}

describe('QuestVisitSystem', () => {
  let state: State;
  let player: number;
  let idx: number;
  let npc: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player', PlayerController);
    state.registerComponent('quest-giver', QuestGiver);
    state.registerComponent('vault', VaultComponent);
    state.registerComponent('progression', ProgressionComponent);
    resetQuestState();

    player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);
    state.addComponent(player, VaultComponent);
    state.addComponent(player, ProgressionComponent);
    registerResourceKind(state, GOLD_KIND);

    idx = registerQuest(
      state,
      visitDef('peaks_cairns', 'cairn_a cairn_b', 2, 6)
    );
    npc = state.createEntity();
    state.addComponent(npc, QuestGiver);
    QuestGiver.questId[npc] = idx;
    QuestGiver.state[npc] = QUEST_STATE_AVAILABLE;

    landmark(state, 'cairn_a', 100, 0);
    landmark(state, 'cairn_b', 200, 0);
  });

  function accept(): void {
    acceptQuest(state, npc, getQuestDef(state, 'peaks_cairns')!);
  }

  it('does not advance while the quest is not accepted', () => {
    movePlayer(player, 100, 0);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });

  it('advances when the player reaches a target within the radius', () => {
    accept();
    movePlayer(player, 100, 4); // 4 m away, radius 6
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
  });

  it('ignores targets outside the radius', () => {
    accept();
    movePlayer(player, 100, 9);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });

  it('counts each landmark once, however often it is re-entered', () => {
    accept();
    movePlayer(player, 100, 0);
    QuestVisitSystem.update!(state);
    QuestVisitSystem.update!(state);
    movePlayer(player, 500, 500);
    QuestVisitSystem.update!(state);
    movePlayer(player, 100, 0);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
  });

  it('completes on the last landmark and pays the rewards', () => {
    accept();
    movePlayer(player, 100, 0);
    QuestVisitSystem.update!(state);
    movePlayer(player, 200, 0);
    QuestVisitSystem.update!(state);

    expect(QuestState.completed[idx]).toBe(1);
    expect(QuestState.active[idx]).toBe(0);
    expect(QuestGiver.state[npc]).toBe(QUEST_STATE_COMPLETED);
    expect(getResource(state, player, GOLD_KIND)).toBe(120);
  });

  it('tolerates a target name that has no entity yet', () => {
    const ghostIdx = registerQuest(
      state,
      visitDef('ghost', 'not_spawned_yet', 1)
    );
    const ghostNpc = state.createEntity();
    state.addComponent(ghostNpc, QuestGiver);
    QuestGiver.questId[ghostNpc] = ghostIdx;
    QuestGiver.state[ghostNpc] = QUEST_STATE_AVAILABLE;
    acceptQuest(state, ghostNpc, getQuestDef(state, 'ghost')!);
    movePlayer(player, 0, 0);
    expect(() => QuestVisitSystem.update!(state)).not.toThrow();
    expect(QuestState.progress[ghostIdx]).toBe(0);
  });
});
