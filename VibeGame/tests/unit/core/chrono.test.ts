import { beforeEach, describe, expect, it } from 'bun:test';
import {
  State,
  chronoMark,
  chronoRewind,
  chronoSeek,
  disableChrono,
  enableChrono,
  getChronoFrames,
  getChronoMarks,
  getChronoStatus,
  isChronoEnabled,
  onChronoSeek,
} from 'aigamekit-vibegame';

import { MAX_ENTITIES } from '../../../src/core/ecs/constants';

const Pos = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
};

function freshState(): State {
  const state = new State();
  state.headless = true;
  state.registerComponent('pos', Pos);
  return state;
}

describe('chrono', () => {
  let state: State;

  beforeEach(() => {
    state = freshState();
  });

  it('is disabled by default and enableChrono registers the recorder', () => {
    expect(isChronoEnabled(state)).toBe(false);
    enableChrono(state, { seconds: 2, hz: 10 });
    expect(isChronoEnabled(state)).toBe(true);
    expect(state.systems.size).toBeGreaterThan(0);
  });

  it('records frames at the configured rate while stepping', () => {
    enableChrono(state, { seconds: 2, hz: 10 });
    for (let i = 0; i < 30; i++) state.step(0.05); // 1.5s → ~15 frames

    const status = getChronoStatus(state);
    expect(status.enabled).toBe(true);
    expect(status.frames).toBeGreaterThanOrEqual(10);
    expect(status.frames).toBeLessThanOrEqual(status.capacity);
    expect(status.hz).toBe(10);
    expect(status.seconds).toBe(2);
  });

  it('evicts oldest frames beyond capacity', () => {
    enableChrono(state, { seconds: 0.5, hz: 10 }); // capacity 5
    for (let i = 0; i < 60; i++) state.step(0.05); // 3s of history

    expect(getChronoFrames(state).length).toBeLessThanOrEqual(5);
  });

  it('rewind restores component values and keeps named entity identity', () => {
    enableChrono(state, { seconds: 2, hz: 10 });

    const hero = state.createEntity();
    state.setEntityName('hero', hero);
    state.addComponent(hero, Pos, { x: 1, y: 2 });

    for (let i = 0; i < 10; i++) state.step(0.05); // ~0.5s, several frames

    // Mutate past the recorded history.
    Pos.x[hero] = 99;
    Pos.y[hero] = 99;
    const framesBefore = getChronoFrames(state).length;

    expect(chronoRewind(state, 0.25)).toBe(true);

    expect(Pos.x[hero]).toBe(1);
    expect(Pos.y[hero]).toBe(2);
    // Named entity keeps its eid through the seek.
    expect(state.getEntityByName('hero')).toBe(hero);
    // Frames after the seek target are dropped.
    expect(getChronoFrames(state).length).toBeLessThan(framesBefore);
    // Clock rewinds with the frame.
    const newest = getChronoFrames(state)[getChronoFrames(state).length - 1]!;
    expect(state.time.elapsed).toBeCloseTo(newest.elapsed, 5);
  });

  it('seek destroys entities created after the target frame', () => {
    enableChrono(state, { seconds: 2, hz: 10 });

    const keeper = state.createEntity();
    state.setEntityName('keeper', keeper);
    state.addComponent(keeper, Pos, { x: 7 });
    for (let i = 0; i < 10; i++) state.step(0.05);

    const latecomer = state.createEntity();
    state.setEntityName('latecomer', latecomer);
    state.addComponent(latecomer, Pos, { x: 8 });
    state.step(0.05);

    expect(chronoSeek(state, 0)).toBe(true);

    expect(state.getEntityByName('keeper')).not.toBeNull();
    expect(state.getEntityByName('latecomer')).toBeNull();
    expect(Pos.x[state.getEntityByName('keeper')!]).toBe(7);
  });

  it('seek restores removed components on matched entities', () => {
    enableChrono(state, { seconds: 2, hz: 10 });

    const hero = state.createEntity();
    state.setEntityName('hero', hero);
    state.addComponent(hero, Pos, { x: 3, y: 4 });
    for (let i = 0; i < 8; i++) state.step(0.05);

    state.removeComponent(hero, Pos);
    expect(state.hasComponent(hero, Pos)).toBe(false);

    expect(chronoSeek(state, 0)).toBe(true);

    expect(state.getEntityByName('hero')).toBe(hero);
    expect(state.hasComponent(hero, Pos)).toBe(true);
    expect(Pos.x[hero]).toBe(3);
    expect(Pos.y[hero]).toBe(4);
  });

  it('marks bookmark frames and fire seek listeners', () => {
    enableChrono(state, { seconds: 2, hz: 10 });
    for (let i = 0; i < 6; i++) state.step(0.05);

    const mark = chronoMark(state, 'before-boss');
    expect(mark).not.toBeNull();
    expect(mark!.label).toBe('before-boss');
    expect(getChronoMarks(state).length).toBe(1);

    let seeked = 0;
    onChronoSeek(state, () => seeked++);

    expect(chronoSeek(state, mark!.frameIndex)).toBe(true);
    expect(seeked).toBe(1);
  });

  it('disableChrono clears history and blocks seeks', () => {
    enableChrono(state, { seconds: 2, hz: 10 });
    for (let i = 0; i < 6; i++) state.step(0.05);
    disableChrono(state);

    expect(isChronoEnabled(state)).toBe(false);
    expect(getChronoFrames(state).length).toBe(0);
    expect(chronoRewind(state, 0.1)).toBe(false);
  });
});
