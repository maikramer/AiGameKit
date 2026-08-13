import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { Transform } from '../../../../src/plugins/transforms';
import { NOTA_LANDMARKS } from '../../../../examples/simple-rpg/src/data/nota-landmarks';
import {
  clearNota,
  markLandmark,
} from '../../../../examples/simple-rpg/src/game/nota';
import {
  LANDMARK_LANDING_OFFSET_M,
  PLAZA_XZ,
  landmarkLandingXZ,
  nearestRespawn,
  respawnCandidates,
  travelDestinations,
} from '../../../../examples/simple-rpg/src/game/travel';
import { LOOKOUT_GATES } from '../../../../examples/simple-rpg/src/game/city-amenities';

function makeState(): State {
  const state = new State();
  state.registerComponent('transform', Transform);
  clearNota();
  return state;
}

function placeLandmark(state: State, name: string, x: number, z: number): void {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  Transform.posX[eid] = x;
  Transform.posY[eid] = 0;
  Transform.posZ[eid] = z;
  state.setEntityName(name, eid);
}

const FOREST = NOTA_LANDMARKS['dark-forest'];
const DESERT = NOTA_LANDMARKS.desert;

describe('Nota travel — landing offset', () => {
  it('puxa o pouso em direcção à origem, sem entrar no mesh', () => {
    const land = landmarkLandingXZ(200, 0);
    expect(land.x).toBeCloseTo(200 - LANDMARK_LANDING_OFFSET_M, 5);
    expect(land.z).toBeCloseTo(0, 5);
  });

  it('não mexe num ponto na origem', () => {
    expect(landmarkLandingXZ(0, 0)).toEqual({ x: 0, z: 0 });
  });
});

describe('Nota travel — destinos', () => {
  let state: State;

  beforeEach(() => {
    state = makeState();
  });

  it('começa vazio — sem marcos anotados não há caminho', () => {
    placeLandmark(state, FOREST[0], 0, 120);
    expect(travelDestinations(state)).toEqual([]);
  });

  it('lista um marco anotado com o pouso deslocado', () => {
    placeLandmark(state, FOREST[0], 0, 120);
    markLandmark(state, FOREST[0]);
    const dests = travelDestinations(state);
    expect(dests).toHaveLength(1);
    expect(dests[0]?.id).toBe(FOREST[0]);
    expect(dests[0]?.biome).toBe('dark-forest');
    expect(dests[0]?.z).toBeCloseTo(120 - LANDMARK_LANDING_OFFSET_M, 5);
    expect(dests[0]?.x).toBeCloseTo(0, 5);
  });

  it('ignora um marco anotado cuja entidade ainda não existe', () => {
    markLandmark(state, FOREST[0]);
    expect(travelDestinations(state)).toEqual([]);
  });
});

describe('Nota travel — respawn', () => {
  let state: State;

  beforeEach(() => {
    state = makeState();
  });

  it('sem marcos, os candidatos são praça + quatro portões', () => {
    const pts = respawnCandidates(state);
    expect(pts).toContainEqual(PLAZA_XZ);
    expect(pts).toHaveLength(1 + LOOKOUT_GATES.length);
    for (const g of LOOKOUT_GATES) {
      expect(pts).toContainEqual([g.x, g.z]);
    }
  });

  it('a morte junto de um marco anotado respawna lá, não no portão', () => {
    placeLandmark(state, DESERT[2], 180, 0);
    markLandmark(state, DESERT[2]);
    const land = landmarkLandingXZ(180, 0);
    const best = nearestRespawn(respawnCandidates(state), 190, 0);
    expect(best[0]).toBeCloseTo(land.x, 5);
    expect(best[1]).toBeCloseTo(land.z, 5);
  });

  it('a morte junto do portão ainda prefere o portão se o marco está longe', () => {
    placeLandmark(state, DESERT[2], 180, 0);
    markLandmark(state, DESERT[2]);
    const best = nearestRespawn(respawnCandidates(state), 40, 0);
    expect(best).toEqual([50, 0]);
  });

  it('sem candidatos cai na praça', () => {
    expect(nearestRespawn([], 10, 10)).toEqual(PLAZA_XZ);
  });
});
