import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { PlayerController } from '../../../../src/plugins/player';
import { Transform } from '../../../../src/plugins/transforms';
import {
  QuestGiver,
  QuestState,
  resetQuestState,
} from '../../../../src/plugins/quests/components';
import {
  QuestVisitSystem,
  getVisitedTargets,
} from '../../../../src/plugins/quests/systems';
import { registerQuest } from '../../../../src/plugins/quests/registry';
import type { QuestDef } from '../../../../src/plugins/quests/registry';
import {
  NOTA_LANDMARKS,
  SURVEY_QUEST,
} from '../../../../examples/simple-rpg/src/data/nota-landmarks';
import {
  biomeProgress,
  clearNota,
  initNota,
  isBiomeFixed,
  isMarked,
  markLandmark,
  notaSnapshot,
  restoreNota,
} from '../../../../examples/simple-rpg/src/game/nota';

/**
 * A Nota, fase F1 — o critério de aceite em forma de teste. O módulo guarda o
 * estado em variáveis de módulo (é um singleton de jogo), por isso cada teste
 * começa por `clearNota()`.
 */

function surveyDef(biome: keyof typeof NOTA_LANDMARKS): QuestDef {
  return {
    id: SURVEY_QUEST[biome],
    npc: `npc_${SURVEY_QUEST[biome]}`,
    title: SURVEY_QUEST[biome],
    lines_intro: [],
    lines_progress: ['Faltam {remaining}.'],
    lines_complete: [],
    objective: {
      type: 'visit',
      target: NOTA_LANDMARKS[biome].join(' '),
      count: NOTA_LANDMARKS[biome].length,
      radius: 10,
    },
  };
}

function makeState(): State {
  const state = new State();
  state.registerComponent('transform', Transform);
  state.registerComponent('player', PlayerController);
  state.registerComponent('quest-giver', QuestGiver);
  resetQuestState();
  clearNota();
  initNota(state);
  return state;
}

const FOREST = NOTA_LANDMARKS['dark-forest'];

describe('A Nota — anotar marcos', () => {
  let state: State;

  beforeEach(() => {
    state = makeState();
  });

  it('começa vazia', () => {
    const snap = notaSnapshot();
    expect(snap.marked).toEqual([]);
    expect(snap.fixed).toEqual([]);
    expect(snap.signed).toBeNull();
  });

  it('regista um marco do catálogo', () => {
    expect(markLandmark(state, FOREST[0])).toBe(true);
    expect(isMarked(FOREST[0])).toBe(true);
    expect(notaSnapshot().marked).toEqual([FOREST[0]]);
  });

  it('é idempotente — a segunda interação não duplica nem volta a contar', () => {
    expect(markLandmark(state, FOREST[0])).toBe(true);
    expect(markLandmark(state, FOREST[0])).toBe(false);
    expect(notaSnapshot().marked.length).toBe(1);
  });

  it('ignora um marco fora do catálogo', () => {
    expect(markLandmark(state, 'forest-watch-tome')).toBe(false);
    expect(markLandmark(state, '')).toBe(false);
    expect(notaSnapshot().marked).toEqual([]);
  });

  it('conta o progresso do bioma', () => {
    expect(biomeProgress('dark-forest')).toBe(0);
    markLandmark(state, FOREST[0]);
    markLandmark(state, FOREST[1]);
    expect(biomeProgress('dark-forest')).toBe(2);
    expect(isBiomeFixed('dark-forest')).toBe(false);
  });

  it('fixa o bioma ao terceiro marco, e só esse bioma', () => {
    for (const name of FOREST) markLandmark(state, name);
    expect(isBiomeFixed('dark-forest')).toBe(true);
    expect(isBiomeFixed('desert')).toBe(false);
    expect(notaSnapshot().fixed).toEqual(['dark-forest']);
  });

  it('devolve os marcos ordenados', () => {
    markLandmark(state, FOREST[2]);
    markLandmark(state, FOREST[0]);
    expect(notaSnapshot().marked).toEqual([...[FOREST[2], FOREST[0]]].sort());
  });
});

describe('A Nota — save / load', () => {
  let state: State;

  beforeEach(() => {
    state = makeState();
  });

  it('faz round-trip de marked e fixed', () => {
    for (const name of FOREST) markLandmark(state, name);
    markLandmark(state, NOTA_LANDMARKS.desert[0]);
    const saved = notaSnapshot();

    clearNota();
    expect(notaSnapshot().marked).toEqual([]);

    restoreNota(state, saved);
    expect(notaSnapshot()).toEqual(saved);
    expect(isBiomeFixed('dark-forest')).toBe(true);
  });

  it('recalcula fixed em vez de acreditar no save', () => {
    // Um save adulterado (ou de uma versão antiga do catálogo) não pode
    // afirmar um bioma fixado que os marcos contradizem.
    restoreNota(state, {
      marked: [FOREST[0]],
      fixed: ['dark-forest', 'desert'],
      signed: null,
    });
    expect(notaSnapshot().fixed).toEqual([]);
    expect(isBiomeFixed('dark-forest')).toBe(false);
  });

  it('descarta nomes que já não estão no catálogo', () => {
    restoreNota(state, { marked: [FOREST[0], 'marco-que-ja-nao-existe'] });
    expect(notaSnapshot().marked).toEqual([FOREST[0]]);
  });

  it('aceita um save vazio ou antigo sem campos', () => {
    markLandmark(state, FOREST[0]);
    restoreNota(state, {});
    expect(notaSnapshot().marked).toEqual([]);
  });
});

describe('A Nota — crédito das quests de traçado', () => {
  let state: State;
  let idx: number;

  beforeEach(() => {
    state = makeState();
    idx = registerQuest(state, surveyDef('dark-forest'));
    QuestState.active[idx] = 1;
  });

  it('anotar um marco avança a quest do bioma', () => {
    markLandmark(state, FOREST[0]);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
  });

  it('os três marcos completam a quest', () => {
    for (const name of FOREST) markLandmark(state, name);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(3);
    expect(QuestState.completed[idx]).toBe(1);
  });

  it('não credita duas vezes o mesmo marco', () => {
    markLandmark(state, FOREST[0]);
    markLandmark(state, FOREST[0]);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(1);
  });

  it('um load repõe os marcos contados, para não se re-creditarem', () => {
    restoreNota(state, { marked: [FOREST[0], FOREST[1]] });
    expect(getVisitedTargets(state, SURVEY_QUEST['dark-forest']).size).toBe(2);

    // Marcar de novo um marco restaurado não pode mexer no progresso.
    markLandmark(state, FOREST[0]);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });

  it('não conta por proximidade — só o gesto conta', () => {
    const player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);

    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.setEntityName(FOREST[0], eid);

    // Jogador em cima do marco, sem nunca ter carregado em F.
    QuestVisitSystem.update!(state);
    QuestVisitSystem.update!(state);
    expect(QuestState.progress[idx]).toBe(0);
  });
});
