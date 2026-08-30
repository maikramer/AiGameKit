import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { claimStackSlot } from '../../../src/plugins/floating-text/stacking';
import { spawnFloatingText } from '../../../src/plugins/floating-text/utils';
import { Transform } from '../../../src/plugins/transforms/components';

/**
 * Vertical stacking: texts sharing a `stackKey` spawn at increasing Y offsets
 * (0, gap, 2*gap, …) so feedback at the same target (damage, loot, popups)
 * doesn't overlap. Entries expire after their lifetime so the stack resets.
 */
describe('floating-text stacking', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('claims increasing offsets for the same key', () => {
    const eidA = state.createEntity();
    const eidB = state.createEntity();
    const eidC = state.createEntity();

    const a = claimStackSlot(state, 'k', eidA, 1.0, 0.5);
    const b = claimStackSlot(state, 'k', eidB, 1.0, 0.5);
    const c = claimStackSlot(state, 'k', eidC, 1.0, 0.5);

    expect(a.yOffset).toBe(0);
    expect(b.yOffset).toBe(0.5);
    expect(c.yOffset).toBe(1.0);
  });

  it('keeps separate keys independent', () => {
    const e1 = state.createEntity();
    const e2 = state.createEntity();

    const wood = claimStackSlot(state, 'wood', e1, 1.0, 0.5);
    const stone = claimStackSlot(state, 'stone', e2, 1.0, 0.5);

    expect(wood.yOffset).toBe(0);
    expect(stone.yOffset).toBe(0); // different key → independent
  });

  it('reclaims a slot after its entry expires', () => {
    const e1 = state.createEntity();
    const e2 = state.createEntity();
    const e3 = state.createEntity();

    claimStackSlot(state, 'k', e1, 1.0, 0.5);
    claimStackSlot(state, 'k', e2, 1.0, 0.5);

    // Advance past the expiry (duration 1.0 + 0.2 margin). state.step bumps
    // state.time.elapsed via the scheduler.
    for (let i = 0; i < 80; i++) state.step(1 / 60);

    const slot = claimStackSlot(state, 'k', e3, 1.0, 0.5);
    expect(slot.yOffset).toBe(0); // stack reset after expiry
  });

  it('releases the slot early when the text entity is destroyed', () => {
    const e1 = state.createEntity();
    const e2 = state.createEntity();

    claimStackSlot(state, 'k', e1, 5.0, 0.5);
    state.destroyEntity(e1); // onDestroy hook should drop e1's entry

    const slot = claimStackSlot(state, 'k', e2, 5.0, 0.5);
    expect(slot.yOffset).toBe(0); // e1's slot was released → back to base
  });

  it('spawnFloatingText with stackKey offsets the spawn Y upward', () => {
    const eid1 = spawnFloatingText(state, 'first', {
      x: 1,
      y: 2,
      z: 3,
      duration: 1.0,
      stackKey: 'harvest@1,3',
      stackGap: 0.5,
    });
    const eid2 = spawnFloatingText(state, 'second', {
      x: 1,
      y: 2,
      z: 3,
      duration: 1.0,
      stackKey: 'harvest@1,3',
      stackGap: 0.5,
    });

    // World mode: the stacked offset lands on Transform.posY.
    expect(Transform.posY[eid1]).toBe(2); // first → offset 0
    expect(Transform.posY[eid2]).toBe(2.5); // second → offset 0.5
  });

  it('stackBaseY anchors the stack to a fixed Y regardless of options.y', () => {
    const eid1 = spawnFloatingText(state, 'first', {
      x: 0,
      y: 99, // ignored when stackBaseY is set
      z: 0,
      duration: 1.0,
      stackKey: 'k',
      stackBaseY: 5,
      stackGap: 0.5,
    });
    const eid2 = spawnFloatingText(state, 'second', {
      x: 0,
      y: 99,
      z: 0,
      duration: 1.0,
      stackKey: 'k',
      stackBaseY: 5,
      stackGap: 0.5,
    });

    expect(Transform.posY[eid1]).toBe(5);
    expect(Transform.posY[eid2]).toBe(5.5);
  });
});
