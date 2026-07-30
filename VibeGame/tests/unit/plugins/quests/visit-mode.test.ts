import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { Transform } from '../../../../src/plugins/transforms';
import { PlayerController } from '../../../../src/plugins/player';
import {
  QuestGiver,
  QuestState,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import {
  QuestVisitSystem,
  getQuestVisitMode,
  getVisitedTargets,
  notifyLandmarkVisited,
  setQuestVisitMode,
  setVisitedTargets,
} from '../../../../src/plugins/quests/systems';
import {
  registerQuest,
  type QuestDef,
} from '../../../../src/plugins/quests/registry';

/**
 * `interact` mode exists so a game can put an action behind reaching a place
 * (a survey, a prayer, planting a flag). With proximity counting left on, that
 * action is decorative — the objective is already ticked by the time the
 * player presses the key.
 */
function visitDef(id: string, target: string, count: number): QuestDef {
  return {
    id,
    npc: `${id}_npc`,
    title: id,
    lines_intro: [],
    lines_progress: ['Faltam {remaining}.'],
    lines_complete: [],
    objective: { type: 'visit', target, count, radius: 10 },
  };
}

describe('quest visit mode', () => {
  let state: State;
  let idx: number;
  let player: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player', PlayerController);
    state.registerComponent('quest-giver', QuestGiver);
    resetQuestState();

    player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);

    idx = registerQuest(state, visitDef('survey', 'cairn-a cairn-b', 2));
    QuestState.active[idx] = 1;

    for (const [name, x] of [
      ['cairn-a', 0],
      ['cairn-b', 200],
    ] as const) {
      const eid = state.createEntity();
      state.addComponent(eid, Transform);
      Transform.posX[eid] = x;
      state.setEntityName(name, eid);
    }
  });

  it('defaults to proximity', () => {
    expect(getQuestVisitMode(state)).toBe('proximity');
  });

  it('counts a landmark by walking into it in proximity mode', () => {
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
    expect(getVisitedTargets(state, 'survey').has('cairn-a')).toBe(true);
  });

  it('ignores proximity entirely in interact mode', () => {
    setQuestVisitMode(state, 'interact');
    QuestVisitSystem.update!(state);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });

  it('counts a reported landmark in interact mode', () => {
    setQuestVisitMode(state, 'interact');
    notifyLandmarkVisited(state, 'cairn-b');
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
    expect(getVisitedTargets(state, 'survey').has('cairn-b')).toBe(true);
  });

  it('never double-counts the same landmark', () => {
    setQuestVisitMode(state, 'interact');
    notifyLandmarkVisited(state, 'cairn-a');
    notifyLandmarkVisited(state, 'cairn-a');
    QuestVisitSystem.update!(state);
    notifyLandmarkVisited(state, 'cairn-a');
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
  });

  it('completes the quest once every landmark is reported', () => {
    setQuestVisitMode(state, 'interact');
    notifyLandmarkVisited(state, 'cairn-a');
    notifyLandmarkVisited(state, 'cairn-b');
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(2);
    expect(QuestState.completed[idx]).toBe(1);
    expect(QuestState.active[idx]).toBe(0);
  });

  it('ignores a report that no active quest asked for', () => {
    setQuestVisitMode(state, 'interact');
    notifyLandmarkVisited(state, 'somewhere-else');
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });

  it('ignores reports for a quest that was never accepted', () => {
    QuestState.active[idx] = 0;
    setQuestVisitMode(state, 'interact');
    notifyLandmarkVisited(state, 'cairn-a');
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });

  it('credits a landmark whose entity does not exist in the scene', () => {
    // Interact mode is name-based: the reporter already knows what it touched,
    // so a landmark in an unloaded chunk still counts.
    setQuestVisitMode(state, 'interact');
    registerQuest(state, visitDef('survey2', 'ghost-stone', 1));
    const idx2 = 1;
    QuestState.active[idx2] = 1;
    notifyLandmarkVisited(state, 'ghost-stone');
    QuestVisitSystem.update!(state);
    expect(QuestState.completed[idx2]).toBe(1);
  });

  it('restores which landmarks were already counted', () => {
    setQuestVisitMode(state, 'interact');
    setVisitedTargets(state, 'survey', ['cairn-a']);
    expect(getVisitedTargets(state, 'survey').has('cairn-a')).toBe(true);

    // A reloaded save must not let the player re-credit that stop.
    notifyLandmarkVisited(state, 'cairn-a');
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });

  it('replaces the restored set rather than merging into it', () => {
    setVisitedTargets(state, 'survey', ['cairn-a', 'cairn-b']);
    setVisitedTargets(state, 'survey', ['cairn-b']);
    const seen = getVisitedTargets(state, 'survey');
    expect(seen.has('cairn-b')).toBe(true);
    expect(seen.has('cairn-a')).toBe(false);
  });
});
