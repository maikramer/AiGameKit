import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { PlayerController } from '../../../../src/plugins/player';
import { Transform } from '../../../../src/plugins/transforms';
import {
  getTrackedWaypoint,
  getWaypoints,
  getTrackedWaypointId,
  setWaypoint,
} from '../../../../src/plugins/hud/waypoints';
import { getInteractionTargets } from '../../../../src/plugins/hud/widgets/interaction-prompt';
import {
  QUEST_WAYPOINT_PREFIX,
  QuestBeaconSystem,
  getAllActiveQuestDefs,
  getTrackedQuest,
  questPromptKey,
  resolveTrackedQuestId,
  setTrackedQuest,
} from '../../../../src/plugins/quests/beacon';
import {
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
  QUEST_STATE_TAKEN,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import {
  registerQuest,
  type QuestDef,
} from '../../../../src/plugins/quests/registry';
import {
  QuestProgressSystem,
  notifyEnemyKilled,
} from '../../../../src/plugins/quests/systems';

function killDef(id: string): QuestDef {
  return {
    id,
    npc: `${id}_npc`,
    title: `Title ${id}`,
    lines_intro: [],
    lines_progress: [],
    lines_complete: [],
    objective: { type: 'kill', target: 'wolf', count: 2 },
  };
}

function visitDef(id: string, targets: string): QuestDef {
  return {
    id,
    npc: `${id}_npc`,
    title: `Title ${id}`,
    lines_intro: [],
    lines_progress: [],
    lines_complete: [],
    objective: { type: 'visit', target: targets, count: 2, radius: 8 },
  };
}

function makeState(): State {
  const state = new State();
  state.registerComponent('transform', Transform);
  state.registerComponent('player', PlayerController);
  state.registerComponent('quest-giver', QuestGiver);
  resetQuestState();
  return state;
}

function addGiver(
  state: State,
  questIdx: number,
  x: number,
  z: number
): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  state.addComponent(eid, QuestGiver);
  Transform.posX[eid] = x;
  Transform.posZ[eid] = z;
  QuestGiver.questId[eid] = questIdx;
  QuestGiver.state[eid] = QUEST_STATE_AVAILABLE;
  QuestGiver.acknowledged[eid] = 0;
  return eid;
}

function questWaypointIds(state: State): string[] {
  return [...getWaypoints(state).keys()].filter((id) =>
    id.startsWith(QUEST_WAYPOINT_PREFIX)
  );
}

describe('QuestBeaconSystem giver markers', () => {
  let state: State;
  let idx: number;

  beforeEach(() => {
    state = makeState();
    idx = registerQuest(state, killDef('forest_wolves'));
  });

  it('publishes a marker for an NPC offering a quest', () => {
    const npc = addGiver(state, idx, 10, 20);
    QuestBeaconSystem.update!(state);

    const ids = questWaypointIds(state);
    expect(ids.length).toBe(1);
    const wp = getWaypoints(state).get(ids[0])!;
    expect(wp.kind).toBe('quest-available');
    expect(wp.x).toBe(10);
    expect(wp.z).toBe(20);
    expect(wp.eid).toBe(npc);
    expect(wp.label).toBe('Title forest_wolves');
  });

  it('goes silent while the objective is out in the world', () => {
    const npc = addGiver(state, idx, 10, 20);
    QuestGiver.state[npc] = QUEST_STATE_TAKEN;
    QuestState.active[idx] = 1;
    QuestBeaconSystem.update!(state);
    expect(questWaypointIds(state)).toEqual([]);
  });

  it('lights back up as a hand-in once the objective is met', () => {
    const npc = addGiver(state, idx, 10, 20);
    QuestGiver.state[npc] = QUEST_STATE_COMPLETED;
    QuestBeaconSystem.update!(state);

    const ids = questWaypointIds(state);
    expect(ids.length).toBe(1);
    expect(getWaypoints(state).get(ids[0])!.kind).toBe('quest-turnin');
  });

  it('stops advertising an NPC whose ending the player already heard', () => {
    const npc = addGiver(state, idx, 10, 20);
    QuestGiver.state[npc] = QUEST_STATE_COMPLETED;
    QuestGiver.acknowledged[npc] = 1;
    QuestBeaconSystem.update!(state);
    expect(questWaypointIds(state)).toEqual([]);
  });

  it('prunes its own markers but leaves game-owned ones alone', () => {
    const npc = addGiver(state, idx, 10, 20);
    setWaypoint(state, { id: 'game:pin', x: 0, y: 0, z: 0, kind: 'custom' });
    QuestBeaconSystem.update!(state);
    expect(questWaypointIds(state).length).toBe(1);

    QuestGiver.state[npc] = QUEST_STATE_TAKEN;
    QuestBeaconSystem.update!(state);
    expect(questWaypointIds(state)).toEqual([]);
    expect(getWaypoints(state).has('game:pin')).toBe(true);
  });
});

describe('QuestBeaconSystem interaction prompts', () => {
  it('registers every giver so the [F] hint can appear', () => {
    const state = makeState();
    const idx = registerQuest(state, killDef('forest_wolves'));
    const npc = addGiver(state, idx, 1, 1);

    QuestBeaconSystem.update!(state);
    const target = getInteractionTargets(state).get(npc);
    expect(target).toBeDefined();
    expect(target!.key).toBe('F');
    expect(target!.i18nKey).toBe('quests.prompt.talk');
  });

  it('relabels the prompt as the quest advances', () => {
    expect(questPromptKey(QUEST_STATE_AVAILABLE)).toBe('quests.prompt.talk');
    expect(questPromptKey(QUEST_STATE_TAKEN)).toBe('quests.prompt.progress');
    expect(questPromptKey(QUEST_STATE_COMPLETED)).toBe('quests.prompt.turnin');
  });
});

describe('QuestBeaconSystem visit objectives', () => {
  let state: State;
  let idx: number;

  beforeEach(() => {
    state = makeState();
    idx = registerQuest(state, visitDef('peaks_survey', 'cairn-a cairn-b'));
  });

  function addLandmark(name: string, x: number, z: number): number {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posX[eid] = x;
    Transform.posZ[eid] = z;
    state.setEntityName(name, eid);
    return eid;
  }

  it('marks each named landmark of an active visit quest', () => {
    addLandmark('cairn-a', 30, 0);
    addLandmark('cairn-b', 0, 40);
    QuestState.active[idx] = 1;

    QuestBeaconSystem.update!(state);
    const ids = questWaypointIds(state).filter((id) =>
      id.includes('objective:')
    );
    expect(ids.length).toBe(2);
    for (const id of ids) {
      expect(getWaypoints(state).get(id)!.kind).toBe('objective');
    }
  });

  it('marks nothing while the quest has not been accepted', () => {
    addLandmark('cairn-a', 30, 0);
    QuestBeaconSystem.update!(state);
    expect(questWaypointIds(state)).toEqual([]);
  });

  it('skips landmarks that are not in the world yet', () => {
    addLandmark('cairn-a', 30, 0);
    QuestState.active[idx] = 1;
    QuestBeaconSystem.update!(state);
    expect(
      questWaypointIds(state).filter((id) => id.includes('objective:')).length
    ).toBe(1);
  });
});

describe('quest tracking', () => {
  let state: State;
  let idx: number;

  beforeEach(() => {
    state = makeState();
    idx = registerQuest(state, visitDef('peaks_survey', 'cairn-a'));
  });

  it('starts untracked', () => {
    expect(getTrackedQuest(state)).toBeNull();
  });

  it('resolves the pinned quest to its objective marker', () => {
    const landmark = state.createEntity();
    state.addComponent(landmark, Transform);
    Transform.posX[landmark] = 12;
    state.setEntityName('cairn-a', landmark);
    addGiver(state, idx, -5, -5);
    QuestState.active[idx] = 1;

    setTrackedQuest(state, 'peaks_survey');
    QuestBeaconSystem.update!(state);

    const trackedId = getTrackedWaypointId(state);
    expect(trackedId).not.toBeNull();
    expect(trackedId!).toContain('objective:');
  });

  it('falls back to the giver marker when the quest has no objective marker', () => {
    addGiver(state, idx, -5, -5);
    setTrackedQuest(state, 'peaks_survey');
    QuestBeaconSystem.update!(state);

    const trackedId = getTrackedWaypointId(state);
    expect(trackedId).not.toBeNull();
    expect(trackedId!).toContain('giver:');
  });

  it('clears the pin', () => {
    addGiver(state, idx, -5, -5);
    setTrackedQuest(state, 'peaks_survey');
    QuestBeaconSystem.update!(state);
    setTrackedQuest(state, null);
    QuestBeaconSystem.update!(state);
    expect(getTrackedWaypointId(state)).toBeNull();
    expect(getTrackedQuest(state)).toBeNull();
  });

  it('ignores a pin on an unregistered quest id', () => {
    addGiver(state, idx, -5, -5);
    setTrackedQuest(state, 'no_such_quest');
    QuestBeaconSystem.update!(state);
    expect(getTrackedWaypointId(state)).toBeNull();
  });
});

describe('getAllActiveQuestDefs', () => {
  it('lists accepted, unfinished quests even with no giver entity present', () => {
    const state = makeState();
    const a = registerQuest(state, killDef('a'));
    const b = registerQuest(state, killDef('b'));
    QuestState.active[a] = 1;
    QuestState.active[b] = 1;
    QuestState.completed[b] = 1;

    const active = getAllActiveQuestDefs(state);
    expect(active.map((e) => e.def.id)).toEqual(['a']);
    expect(active[0].index).toBe(a);
  });
});

describe('quest navigation while a quest is active', () => {
  it('keeps the arrow quiet instead of pointing at new quests', () => {
    const state = makeState();
    const activeIdx = registerQuest(state, killDef('forest_wolves'));
    const otherIdx = registerQuest(state, killDef('city_wolves'));
    const takenNpc = addGiver(state, activeIdx, 10, 20);
    QuestGiver.state[takenNpc] = QUEST_STATE_TAKEN;
    QuestState.active[activeIdx] = 1;
    addGiver(state, otherIdx, -30, -40);

    QuestBeaconSystem.update!(state);

    // The only published marker is the other quest's giver badge; neither the
    // tracked waypoint nor the arrow may land on it.
    expect(questWaypointIds(state).length).toBe(1);
    expect(getTrackedWaypointId(state)).toBeNull();
    expect(getTrackedWaypoint(state, 0, 0)).toBeNull();
    expect(resolveTrackedQuestId(state)).toBe('forest_wolves');
  });

  it('follows the first active quest without an explicit pin', () => {
    const state = makeState();
    const idx = registerQuest(state, visitDef('peaks_survey', 'cairn-a'));
    const landmark = state.createEntity();
    state.addComponent(landmark, Transform);
    Transform.posX[landmark] = 12;
    state.setEntityName('cairn-a', landmark);
    QuestState.active[idx] = 1;

    QuestBeaconSystem.update!(state);

    expect(getTrackedQuest(state)).toBeNull();
    expect(getTrackedWaypointId(state)).toContain('objective:');
  });

  it('anchors an active kill quest to the last kill site', () => {
    const state = makeState();
    const idx = registerQuest(state, killDef('forest_wolves'));
    const npc = addGiver(state, idx, 10, 20);
    QuestGiver.state[npc] = QUEST_STATE_TAKEN;
    QuestState.active[idx] = 1;

    notifyEnemyKilled(state, 'wolf', { x: 33, y: 1, z: 44 });
    QuestProgressSystem.update!(state);
    QuestBeaconSystem.update!(state);

    const field = getWaypoints(state).get(
      `${QUEST_WAYPOINT_PREFIX}objective:forest_wolves:last-seen`
    );
    expect(field).toBeDefined();
    expect(field!.kind).toBe('objective');
    expect(field!.x).toBe(33);
    expect(field!.z).toBe(44);
    expect(getTrackedWaypointId(state)).toBe(field!.id);
  });

  it('hands the arrow to the turn-in once the objective is met', () => {
    const state = makeState();
    const idx = registerQuest(state, killDef('forest_wolves'));
    const npc = addGiver(state, idx, 10, 20);
    QuestGiver.state[npc] = QUEST_STATE_TAKEN;
    QuestState.active[idx] = 1;

    notifyEnemyKilled(state, 'wolf', { x: 33, y: 1, z: 44 });
    notifyEnemyKilled(state, 'wolf', { x: 35, y: 1, z: 46 });
    QuestProgressSystem.update!(state);
    QuestBeaconSystem.update!(state);

    expect(questWaypointIds(state)).toEqual([
      `${QUEST_WAYPOINT_PREFIX}giver:${npc}`,
    ]);
    expect(getWaypoints(state).get(questWaypointIds(state)[0])!.kind).toBe(
      'quest-turnin'
    );
    // The pin is gone with the completed quest; the arrow lands on the
    // hand-in through auto-selection, which outranks every other badge.
    expect(getTrackedWaypoint(state, 0, 0)!.id).toBe(
      questWaypointIds(state)[0]
    );
  });

  it('an explicit pin overrides the first-active pick', () => {
    const state = makeState();
    const firstIdx = registerQuest(state, visitDef('peaks_survey', 'cairn-a'));
    const landmark = state.createEntity();
    state.addComponent(landmark, Transform);
    state.setEntityName('cairn-a', landmark);
    QuestState.active[firstIdx] = 1;
    const secondIdx = registerQuest(state, killDef('forest_wolves'));
    QuestState.active[secondIdx] = 1;

    setTrackedQuest(state, 'forest_wolves');
    QuestBeaconSystem.update!(state);

    // Pinned kill quest has no world marker yet, so the arrow stays quiet
    // even though the first active quest has an objective to point at.
    expect(resolveTrackedQuestId(state)).toBe('forest_wolves');
    expect(getTrackedWaypoint(state, 0, 0)).toBeNull();
  });
});
