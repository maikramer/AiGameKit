import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { PlayerController } from '../../../../src/plugins/player';
import { Transform } from '../../../../src/plugins/transforms';
import {
  clearWaypoints,
  setWaypoint,
} from '../../../../src/plugins/hud/waypoints';
import { setTrackedQuest } from '../../../../src/plugins/quests/beacon';
import {
  QuestState,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import { collectQuestTrackerEntries } from '../../../../src/plugins/quests/hud/quest-tracker';
import {
  registerQuest,
  type QuestDef,
} from '../../../../src/plugins/quests/registry';

function def(id: string, count: number): QuestDef {
  return {
    id,
    npc: `${id}_npc`,
    title: `Title ${id}`,
    lines_intro: [],
    lines_progress: [],
    lines_complete: [],
    objective: { type: 'kill', target: 'wolf', count },
  };
}

describe('collectQuestTrackerEntries', () => {
  let state: State;
  let a: number;
  let b: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player', PlayerController);
    resetQuestState();
    clearWaypoints(state);
    setTrackedQuest(state, null);

    const player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);

    a = registerQuest(state, def('quest_a', 5));
    b = registerQuest(state, def('quest_b', 3));
  });

  it('lists nothing before any quest is accepted', () => {
    expect(collectQuestTrackerEntries(state)).toEqual([]);
  });

  it('reports title, progress and goal for an active quest', () => {
    QuestState.active[a] = 1;
    QuestState.progress[a] = 2;

    const [entry] = collectQuestTrackerEntries(state);
    expect(entry.questId).toBe('quest_a');
    expect(entry.title).toBe('Title quest_a');
    expect(entry.progress).toBe(2);
    expect(entry.goal).toBe(5);
    // With no explicit pin, the beacon follows the first active quest —
    // the tracker highlights whichever quest the arrow is on.
    expect(entry.tracked).toBe(true);
    expect(entry.distance).toBeNull();
  });

  it('clamps progress that overran the goal', () => {
    QuestState.active[a] = 1;
    QuestState.progress[a] = 99;
    expect(collectQuestTrackerEntries(state)[0].progress).toBe(5);
  });

  it('drops a quest once it is completed', () => {
    QuestState.active[a] = 1;
    QuestState.completed[a] = 1;
    expect(collectQuestTrackerEntries(state)).toEqual([]);
  });

  it('shows the distance to the quest own nearest marker', () => {
    QuestState.active[a] = 1;
    setWaypoint(state, {
      id: 'far',
      x: 0,
      y: 0,
      z: 100,
      kind: 'objective',
      questIndex: a,
    });
    setWaypoint(state, {
      id: 'near',
      x: 3,
      y: 0,
      z: 4,
      kind: 'objective',
      questIndex: a,
    });
    // Belongs to another quest — must not be picked up as "closest".
    setWaypoint(state, {
      id: 'other',
      x: 1,
      y: 0,
      z: 0,
      kind: 'objective',
      questIndex: b,
    });

    expect(collectQuestTrackerEntries(state)[0].distance).toBeCloseTo(5, 5);
  });

  it('floats the pinned quest to the top and flags it', () => {
    QuestState.active[a] = 1;
    QuestState.active[b] = 1;
    setTrackedQuest(state, 'quest_b');

    const entries = collectQuestTrackerEntries(state);
    expect(entries[0].questId).toBe('quest_b');
    expect(entries[0].tracked).toBe(true);
    expect(entries[1].tracked).toBe(false);
  });

  it('caps the row count', () => {
    QuestState.active[a] = 1;
    QuestState.active[b] = 1;
    expect(collectQuestTrackerEntries(state, 1).length).toBe(1);
    expect(collectQuestTrackerEntries(state, 0)).toEqual([]);
  });
});
