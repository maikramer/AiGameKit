import type { State } from '../../core';
import { popModal, pushModal } from '../rpg-pause';
import { QuestGiver, QuestState, QUEST_STATE_TAKEN } from './components';
import { getQuestIndex } from './registry';
import type { QuestDef } from './registry';

const DIALOGUE_MODAL = 'dialogue';

export type DialoguePhase = 'intro' | 'progress' | 'complete';

export interface ActiveDialogue {
  readonly speakerEid: number;
  readonly def: QuestDef;
  readonly phase: DialoguePhase;
  /** Invoked after the dialogue is closed (e.g. to refresh interaction state). */
  readonly onClose?: () => void;
}

const stateToActiveDialogue = new WeakMap<State, ActiveDialogue | null>();

export function showDialogue(state: State, payload: ActiveDialogue): void {
  stateToActiveDialogue.set(state, payload);
  pushModal(state, DIALOGUE_MODAL);
}

export function endDialogue(state: State): void {
  const active = stateToActiveDialogue.get(state);
  stateToActiveDialogue.set(state, null);
  popModal(state, DIALOGUE_MODAL);
  if (active?.onClose) active.onClose();
}

export function getActiveDialogue(state: State): ActiveDialogue | null {
  return stateToActiveDialogue.get(state) ?? null;
}

/**
 * Accept the active/indicated quest: transition the giver to `taken` and mark
 * the quest active in the global QuestState. Idempotent if already taken.
 */
export function acceptQuest(
  state: State,
  speakerEid: number,
  def: QuestDef
): void {
  QuestGiver.state[speakerEid] = QUEST_STATE_TAKEN;
  const idx = getQuestIndex(state, def.id);
  if (idx >= 0) {
    QuestState.active[idx] = 1;
    QuestState.progress[idx] = 0;
    QuestState.completed[idx] = 0;
  }
}
