import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { PlayerController } from '../../../../src/plugins/player';
import { Transform } from '../../../../src/plugins/transforms';
import {
  QuestGiverFacingSystem,
  shortestAngleDelta,
  stepTowardYaw,
} from '../../../../src/plugins/quests/facing';
import {
  QuestGiver,
  resetQuestState,
} from '../../../../src/plugins/quests/components';

const DEG2RAD = Math.PI / 180;

describe('yaw math', () => {
  it('takes the short way around the circle', () => {
    expect(shortestAngleDelta(3.0, -3.0)).toBeCloseTo(2 * Math.PI - 6.0, 5);
    expect(shortestAngleDelta(0, Math.PI / 2)).toBeCloseTo(Math.PI / 2, 5);
  });

  it('snaps to the target once within one step', () => {
    expect(stepTowardYaw(0, 0.05, 0.1)).toBe(0.05);
  });

  it('moves at most one step otherwise', () => {
    expect(stepTowardYaw(0, 1.0, 0.1)).toBeCloseTo(0.1, 5);
    expect(stepTowardYaw(0, -1.0, 0.1)).toBeCloseTo(-0.1, 5);
  });
});

describe('QuestGiverFacingSystem', () => {
  let state: State;
  let player: number;
  let npc: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('player', PlayerController);
    state.registerComponent('quest-giver', QuestGiver);
    resetQuestState();

    player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, PlayerController);

    npc = state.createEntity();
    state.addComponent(npc, Transform);
    state.addComponent(npc, QuestGiver);
    Transform.eulerY[npc] = 0;
    state.time.deltaTime = 1; // one big step: land on the target in one tick
  });

  it('turns toward a player standing next to it', () => {
    Transform.posX[player] = 3;
    Transform.posZ[player] = 0;
    QuestGiverFacingSystem.update!(state);
    // atan2(dx=3, dz=0) = +90°.
    expect(Transform.eulerY[npc]).toBeCloseTo(90, 1);
  });

  it('keeps the quaternion in step so the hierarchy cannot undo the turn', () => {
    Transform.posX[player] = 0;
    Transform.posZ[player] = 3;
    Transform.eulerY[npc] = 180;
    QuestGiverFacingSystem.update!(state);
    expect(Transform.eulerY[npc]).toBeCloseTo(0, 1);
    expect(Transform.rotW[npc]).toBeCloseTo(1, 3);
    expect(Transform.dirty[npc]).toBe(1);
  });

  it('ignores a player who is far away and returns to the posted heading', () => {
    Transform.eulerY[npc] = 45;
    // First tick captures 45° as the rest heading.
    Transform.posX[player] = 500;
    QuestGiverFacingSystem.update!(state);
    expect(Transform.eulerY[npc]).toBeCloseTo(45, 1);

    // Walk up, get looked at, walk away again.
    Transform.posX[player] = 2;
    QuestGiverFacingSystem.update!(state);
    expect(Transform.eulerY[npc]).toBeCloseTo(90, 1);

    Transform.posX[player] = 500;
    QuestGiverFacingSystem.update!(state);
    expect(Transform.eulerY[npc]).toBeCloseTo(45, 1);
  });

  it('slews rather than snapping over a normal frame', () => {
    state.time.deltaTime = 1 / 60;
    Transform.posX[player] = 3;
    QuestGiverFacingSystem.update!(state);
    const turned = Math.abs(Transform.eulerY[npc] * DEG2RAD);
    expect(turned).toBeGreaterThan(0);
    expect(turned).toBeLessThan(Math.PI / 2);
  });

  it('does nothing without a player', () => {
    state.removeComponent(player, PlayerController);
    Transform.eulerY[npc] = 12;
    QuestGiverFacingSystem.update!(state);
    expect(Transform.eulerY[npc]).toBe(12);
  });
});
