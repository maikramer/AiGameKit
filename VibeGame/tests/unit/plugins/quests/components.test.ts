import { beforeEach, describe, expect, it } from 'bun:test';
import {
  MAX_QUESTS,
  QuestGiver,
  QuestState,
  resetQuestState,
} from '../../../../src/plugins/quests/components';

describe('QuestState singleton', () => {
  beforeEach(() => resetQuestState());

  for (let idx = 0; idx < 40; idx++) {
    it(`active/progress/completed round-trip index ${idx}`, () => {
      QuestState.active[idx] = 1;
      QuestState.progress[idx] = idx + 1;
      QuestState.completed[idx] = 1;
      expect(QuestState.active[idx]).toBe(1);
      expect(QuestState.progress[idx]).toBe(idx + 1);
      expect(QuestState.completed[idx]).toBe(1);
      resetQuestState();
      expect(QuestState.active[idx]).toBe(0);
      expect(QuestState.progress[idx]).toBe(0);
      expect(QuestState.completed[idx]).toBe(0);
    });
  }

  it('QuestGiver fields are typed arrays', () => {
    expect(QuestGiver.questId).toBeInstanceOf(Uint32Array);
    expect(QuestGiver.state).toBeInstanceOf(Uint8Array);
  });

  for (let eid = 0; eid < 25; eid++) {
    it(`QuestGiver round-trip on entity ${eid}`, () => {
      QuestGiver.questId[eid] = eid + 100;
      QuestGiver.state[eid] = eid % 4;
      expect(QuestGiver.questId[eid]).toBe(eid + 100);
      expect(QuestGiver.state[eid]).toBe(eid % 4);
      QuestGiver.questId[eid] = 0;
      QuestGiver.state[eid] = 0;
    });
  }

  it('MAX_QUESTS is 64', () => {
    expect(MAX_QUESTS).toBe(64);
    expect(QuestState.active.length).toBe(MAX_QUESTS);
  });
});

describe('QuestState boundary indices', () => {
  beforeEach(() => resetQuestState());

  for (const idx of [0, 1, MAX_QUESTS - 2, MAX_QUESTS - 1]) {
    it(`accepts progress at index ${idx}`, () => {
      QuestState.progress[idx] = 99;
      expect(QuestState.progress[idx]).toBe(99);
    });
  }
});
