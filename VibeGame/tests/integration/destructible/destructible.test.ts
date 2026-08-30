import { beforeEach, describe, expect, it } from 'bun:test';
import { State, defineQuery } from 'aigamekit-vibegame';
import {
  Destructible,
  DestructiblePlugin,
  onDestructibleDestroyed,
  ParticleEmitter,
  PlayerController,
  InputState,
  Transform,
} from 'aigamekit-vibegame';

const STEP = 1 / 60;

function createPlayer(state: State, x: number, z: number): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  Transform.posX[eid] = x;
  Transform.posZ[eid] = z;
  Transform.scaleX[eid] = 1;
  Transform.scaleY[eid] = 1;
  Transform.scaleZ[eid] = 1;
  Transform.rotW[eid] = 1;
  state.addComponent(eid, PlayerController);
  state.addComponent(eid, InputState);
  return eid;
}

function createRock(state: State, x: number, z: number): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  Transform.posX[eid] = x;
  Transform.posZ[eid] = z;
  Transform.scaleX[eid] = 1;
  Transform.scaleY[eid] = 1;
  Transform.scaleZ[eid] = 1;
  Transform.rotW[eid] = 1;
  state.addComponent(eid, Destructible);
  Destructible.hits[eid] = 1;
  Destructible.range[eid] = 3.5;
  Destructible.impactFraction[eid] = 0.75;
  Destructible.preset[eid] = 5;
  Destructible.burstCount[eid] = 60;
  return eid;
}

describe('Destructible Integration', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(DestructiblePlugin);
    await state.initializePlugins();
  });

  it('breaks after the impact delay and fires the destroyed hook', () => {
    const player = createPlayer(state, 0, 0);
    const rock = createRock(state, 2, 0);

    const destroyed: Array<{ eid: number; x: number; z: number }> = [];
    onDestructibleDestroyed(state, (eid, x, _y, z) => {
      destroyed.push({ eid, x, z });
    });

    InputState.primaryAction[player] = 1;
    state.step(STEP);
    InputState.primaryAction[player] = 0;

    // swing committed but the blow hasn't landed yet
    expect(Destructible.hitsTaken[rock]).toBe(1);
    expect(Destructible.pendingImpact[rock]).toBeGreaterThan(0);
    expect(state.exists(rock)).toBe(true);

    // fallback impact delay is 0.5s (no animator in headless tests)
    for (let i = 0; i < 40; i++) state.step(STEP);

    expect(state.exists(rock)).toBe(false);
    expect(destroyed).toEqual([{ eid: rock, x: 2, z: 0 }]);

    // the break burst spawned a one-shot particle emitter at the rock
    const emitters = defineQuery([ParticleEmitter])(state.world);
    expect(emitters.length).toBe(1);
    expect(ParticleEmitter.burst[emitters[0]]).toBe(1);
    expect(Transform.posX[emitters[0]]).toBe(2);
  });

  it('ignores swings outside the attack range', () => {
    const player = createPlayer(state, 0, 0);
    const rock = createRock(state, 10, 0);

    InputState.primaryAction[player] = 1;
    state.step(STEP);

    expect(Destructible.hitsTaken[rock]).toBe(0);
    expect(Destructible.pendingImpact[rock]).toBe(0);
  });

  it('multi-hit props survive until the final blow', () => {
    const player = createPlayer(state, 0, 0);
    const rock = createRock(state, 2, 0);
    Destructible.hits[rock] = 2;
    Destructible.sparkOnHit[rock] = 0;

    // first swing + landing
    InputState.primaryAction[player] = 1;
    state.step(STEP);
    InputState.primaryAction[player] = 0;
    for (let i = 0; i < 40; i++) state.step(STEP);
    expect(state.exists(rock)).toBe(true);
    expect(Destructible.hitsTaken[rock]).toBe(1);

    // second swing breaks it
    InputState.primaryAction[player] = 1;
    state.step(STEP);
    InputState.primaryAction[player] = 0;
    for (let i = 0; i < 40; i++) state.step(STEP);
    expect(state.exists(rock)).toBe(false);
  });

  it('break-style fall/shatter degrada para burst sem cena (headless)', () => {
    const player = createPlayer(state, 0, 0);
    const tree = createRock(state, 2, 0);
    Destructible.breakStyle[tree] = 1; // fall
    Destructible.cutHeight[tree] = 0.6;

    InputState.primaryAction[player] = 1;
    state.step(STEP);
    InputState.primaryAction[player] = 0;
    for (let i = 0; i < 40; i++) state.step(STEP);

    // sem grupo visual/cena o efeito de queda não arranca, mas o prop quebra
    // na mesma com o burst clássico
    expect(state.exists(tree)).toBe(false);
    const emitters = defineQuery([ParticleEmitter])(state.world);
    expect(emitters.length).toBe(1);
  });

  it('break-style split degrada para burst sem cena (headless)', () => {
    const player = createPlayer(state, 0, 0);
    const tree = createRock(state, 2, 0);
    Destructible.breakStyle[tree] = 3; // split
    Destructible.cutHeight[tree] = 0.6;

    InputState.primaryAction[player] = 1;
    state.step(STEP);
    InputState.primaryAction[player] = 0;
    for (let i = 0; i < 40; i++) state.step(STEP);

    // sem grupo visual/cena o split vertical não arranca, mas o prop quebra
    // na mesma com o burst clássico (igual ao fall/shatter)
    expect(state.exists(tree)).toBe(false);
    const emitters = defineQuery([ParticleEmitter])(state.world);
    expect(emitters.length).toBe(1);
  });

  it('hit não-final usa hit-preset e hit-burst-count', () => {
    const player = createPlayer(state, 0, 0);
    const tree = createRock(state, 2, 0);
    Destructible.hits[tree] = 3;
    Destructible.sparkOnHit[tree] = 1;
    Destructible.hitPreset[tree] = 10; // woodchips
    Destructible.hitBurstCount[tree] = 22;

    InputState.primaryAction[player] = 1;
    state.step(STEP);
    InputState.primaryAction[player] = 0;
    for (let i = 0; i < 40; i++) state.step(STEP);

    expect(state.exists(tree)).toBe(true);
    const emitters = defineQuery([ParticleEmitter])(state.world);
    expect(emitters.length).toBe(1);
    expect(ParticleEmitter.preset[emitters[0]]).toBe(10);
    expect(ParticleEmitter.burstCount[emitters[0]]).toBe(22);
  });

  it('commits the swing to the nearest prop only', () => {
    const player = createPlayer(state, 0, 0);
    const near = createRock(state, 1.5, 0);
    const far = createRock(state, 3, 0);

    InputState.primaryAction[player] = 1;
    state.step(STEP);

    expect(Destructible.hitsTaken[near]).toBe(1);
    expect(Destructible.hitsTaken[far]).toBe(0);
  });
});
