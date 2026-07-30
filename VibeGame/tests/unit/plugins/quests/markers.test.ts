import { describe, expect, it } from 'bun:test';

import {
  QUEST_MARKER_STYLES,
  resolveQuestMarkerKind,
} from '../../../../src/plugins/quests/markers';
import {
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
  QUEST_STATE_FAILED,
  QUEST_STATE_TAKEN,
} from '../../../../src/plugins/quests/components';

describe('resolveQuestMarkerKind', () => {
  it('shows ! over an NPC with a quest to offer', () => {
    expect(resolveQuestMarkerKind(QUEST_STATE_AVAILABLE, 0, 5, false)).toBe(
      'available'
    );
  });

  it('shows a dim ? while the objective is unfinished', () => {
    expect(resolveQuestMarkerKind(QUEST_STATE_TAKEN, 2, 5, false)).toBe(
      'progress'
    );
  });

  it('switches to a hand-in badge once the count is reached', () => {
    expect(resolveQuestMarkerKind(QUEST_STATE_TAKEN, 5, 5, false)).toBe(
      'turnin'
    );
    expect(resolveQuestMarkerKind(QUEST_STATE_TAKEN, 9, 5, false)).toBe(
      'turnin'
    );
  });

  it('keeps asking the player to come back after the field completion', () => {
    expect(resolveQuestMarkerKind(QUEST_STATE_COMPLETED, 5, 5, false)).toBe(
      'turnin'
    );
  });

  it('goes quiet once the ending lines were heard', () => {
    expect(resolveQuestMarkerKind(QUEST_STATE_COMPLETED, 5, 5, true)).toBe(
      'none'
    );
  });

  it('shows nothing for a failed quest', () => {
    expect(resolveQuestMarkerKind(QUEST_STATE_FAILED, 0, 5, false)).toBe(
      'none'
    );
  });

  it('ignores acknowledgement while the quest is still running', () => {
    expect(resolveQuestMarkerKind(QUEST_STATE_TAKEN, 1, 5, true)).toBe(
      'progress'
    );
  });

  it('defines a distinct glyph and colour per badge', () => {
    const kinds = ['available', 'progress', 'turnin'] as const;
    const glyphs = kinds.map((k) => QUEST_MARKER_STYLES[k].glyph);
    const colors = kinds.map((k) => QUEST_MARKER_STYLES[k].color);
    expect(new Set(colors).size).toBe(3);
    expect(glyphs).toContain('!');
    expect(glyphs).toContain('✓');
  });
});
