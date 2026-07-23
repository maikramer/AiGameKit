import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../../src/core/ecs/state';
import {
  QuestState,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import {
  applyQuestStateSnapshot,
  getAllQuestDefs,
  getQuestDefByIndex,
  getQuestIndex,
  registerQuest,
  serializeQuestState,
  type QuestDef,
} from '../../../../src/plugins/quests/registry';

function def(id: string, count = 1): QuestDef {
  return {
    id,
    npc: `${id}_npc`,
    title: id,
    lines_intro: [],
    lines_progress: [],
    lines_complete: [],
    objective: { type: 'kill', target: 'mob', count },
  };
}

describe('registerQuest index allocation', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    resetQuestState();
  });

  it('allocates sequential indices for many quests in one state', () => {
    for (let i = 0; i < 35; i++) {
      const idx = registerQuest(state, def(`quest_${i}`));
      expect(idx).toBe(i);
      expect(getQuestIndex(state, `quest_${i}`)).toBe(i);
      expect(getQuestDefByIndex(state, idx)?.id).toBe(`quest_${i}`);
    }
  });

  for (let i = 0; i < 20; i++) {
    it(`re-register quest slot ${i} keeps index after update`, () => {
      const s = new State();
      const idx = registerQuest(s, def(`q${i}`));
      const idx2 = registerQuest(s, def(`q${i}`, i + 3));
      expect(idx2).toBe(idx);
      expect(getQuestDefByIndex(s, idx)?.objective.count).toBe(i + 3);
    });
  }
});

describe('serializeQuestState / applyQuestStateSnapshot', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    resetQuestState();
  });

  for (let i = 0; i < 25; i++) {
    it(`round-trips snapshot for quest slot ${i}`, () => {
      const s = new State();
      resetQuestState();
      registerQuest(s, def(`q${i}`));
      QuestState.active[0] = 1;
      QuestState.progress[0] = i + 2;
      if (i % 3 === 0) QuestState.completed[0] = 1;

      const snap = serializeQuestState(s);
      resetQuestState();
      applyQuestStateSnapshot(s, snap);

      expect(QuestState.active[0]).toBe(1);
      expect(QuestState.progress[0]).toBe(i + 2);
      if (i % 3 === 0) {
        expect(QuestState.completed[0]).toBe(1);
      } else {
        expect(QuestState.completed[0]).toBe(0);
      }
    });
  }

  it('applyQuestStateSnapshot(null) clears state', () => {
    registerQuest(state, def('clear'));
    QuestState.active[0] = 1;
    applyQuestStateSnapshot(state, null);
    expect(QuestState.active[0]).toBe(0);
  });

  it('getAllQuestDefs lists registered quests', () => {
    registerQuest(state, def('a'));
    registerQuest(state, def('b'));
    const ids = getAllQuestDefs(state)
      .map((d) => d.id)
      .sort();
    expect(ids).toEqual(['a', 'b']);
  });
});
