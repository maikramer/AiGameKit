import { describe, expect, it } from 'bun:test';
import {
  AI_LUNGE_PHASE_LUNGE,
  AI_LUNGE_PHASE_READY,
  AI_LUNGE_PHASE_WINDUP,
  AI_MODE_ATTACK,
  AI_MODE_LUNGE,
  AiStateComponent,
  Health,
  State,
  Transform,
  getOrCreateAiInstanceState,
  runMeleeAiFrame,
  staggerAi,
  type MeleeAiConfig,
} from 'aigamekit-vibegame';

const HERO_EID = 1;
const CREATURE_EID = 2;
const DT = 0.016;

function makeConfig(overrides: Partial<MeleeAiConfig> = {}): MeleeAiConfig {
  return {
    detectRange: 18,
    attackRange: 3,
    attackCooldown: 2.5,
    attackDamage: 10,
    chaseSpeed: 2,
    wanderSpeed: 1,
    wanderRadius: 5,
    leashRadius: 30,
    lungeWindup: 0.25,
    lungeDuration: 0.3,
    lungeRecovery: 0.5,
    lungeStandoff: 2.2,
    hoverMin: 2,
    hoverMax: 5,
    targetEid: HERO_EID,
    ...overrides,
  };
}

interface StubState {
  time: { deltaTime: number };
  exists: (eid: number) => boolean;
  hasComponent: (eid: number, component: unknown) => boolean;
  addComponent: (eid: number, component: unknown) => void;
}

function makeStubState(): StubState {
  return {
    time: { deltaTime: DT },
    exists: () => true,
    hasComponent: () => false,
    addComponent: () => {},
  };
}

function place(eid: number, x: number, z: number): void {
  Transform.posX[eid] = x;
  Transform.posY[eid] = 0;
  Transform.posZ[eid] = z;
}

function resetHealth(eid: number, hp: number): void {
  Health.current[eid] = hp;
  Health.max[eid] = hp;
}

/** Drive the FSM until it reaches the windup phase (attack ring + cooldown 0). */
function driveToWindup(state: State, config: MeleeAiConfig): void {
  AiStateComponent.mode[CREATURE_EID] = 0;
  AiStateComponent.target[CREATURE_EID] = 0;
  AiStateComponent.cooldown[CREATURE_EID] = 0;
  resetHealth(CREATURE_EID, 50);
  resetHealth(HERO_EID, 100);
  place(CREATURE_EID, 2, 0);
  place(HERO_EID, 0, 0);
  const inst = getOrCreateAiInstanceState(state, CREATURE_EID);
  inst.originX = 2;
  inst.originZ = 0;
  inst.originSet = true;
  inst.staggerTimer = 0;
  inst.lungePhase = 'ready';
  const eager = { ...config, attackCooldown: 0 };
  // First frame enters ATTACK + windup (cooldown 0).
  runMeleeAiFrame(state, CREATURE_EID, eager, inst);
  runMeleeAiFrame(state, CREATURE_EID, eager, inst);
  expect(AiStateComponent.lungePhase[CREATURE_EID]).toBe(AI_LUNGE_PHASE_WINDUP);
}

describe('staggerAi — hit interrupt', () => {
  it('interrupts an in-flight windup and freezes the FSM', () => {
    const state = makeStubState() as unknown as State;
    const config = makeConfig();
    driveToWindup(state, config);
    const inst = getOrCreateAiInstanceState(state, CREATURE_EID);

    staggerAi(state, CREATURE_EID, 0.32);
    expect(AiStateComponent.lungePhase[CREATURE_EID]).toBe(
      AI_LUNGE_PHASE_READY
    );
    expect(AiStateComponent.staggerTimer[CREATURE_EID]).toBeCloseTo(0.32, 5);

    // While staggered, frames do not re-enter windup even with cooldown 0.
    const eager = { ...config, attackCooldown: 0 };
    for (let i = 0; i < 10; i++) {
      runMeleeAiFrame(state, CREATURE_EID, eager, inst);
    }
    expect(AiStateComponent.lungePhase[CREATURE_EID]).toBe(
      AI_LUNGE_PHASE_READY
    );
    expect(AiStateComponent.staggerTimer[CREATURE_EID]).toBeGreaterThan(0);
  });

  it('interrupts the lunge burst itself (mid-dash poise break)', () => {
    const state = makeStubState() as unknown as State;
    const config = makeConfig();
    driveToWindup(state, config);
    const inst = getOrCreateAiInstanceState(state, CREATURE_EID);

    // Burn through the windup so the burst is in flight.
    for (let i = 0; i < Math.ceil(0.25 / DT) + 1; i++) {
      runMeleeAiFrame(state, CREATURE_EID, config, inst);
    }
    expect(AiStateComponent.lungePhase[CREATURE_EID]).toBe(
      AI_LUNGE_PHASE_LUNGE
    );
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_LUNGE);

    staggerAi(state, CREATURE_EID, 0.3);
    expect(AiStateComponent.lungePhase[CREATURE_EID]).toBe(
      AI_LUNGE_PHASE_READY
    );
    // LUNGE mode is demoted so presentation doesn't hold the jump clip.
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_ATTACK);
  });

  it('recovers: after the stagger expires the attack cycle resumes', () => {
    const state = makeStubState() as unknown as State;
    const config = makeConfig();
    driveToWindup(state, config);
    const inst = getOrCreateAiInstanceState(state, CREATURE_EID);
    staggerAi(state, CREATURE_EID, 0.32);

    // 0.32s / 16ms ≈ 21 frames to expire, then the eager cycle re-arms.
    const eager = { ...config, attackCooldown: 0 };
    let sawWindup = false;
    for (let i = 0; i < 40; i++) {
      runMeleeAiFrame(state, CREATURE_EID, eager, inst);
      if (AiStateComponent.lungePhase[CREATURE_EID] === AI_LUNGE_PHASE_WINDUP) {
        sawWindup = true;
        break;
      }
    }
    expect(AiStateComponent.staggerTimer[CREATURE_EID]).toBe(0);
    expect(sawWindup).toBe(true);
  });

  it('overlapping grants extend, never shorten', () => {
    const state = makeStubState() as unknown as State;
    resetHealth(CREATURE_EID, 50);
    AiStateComponent.staggerTimer[CREATURE_EID] = 0;
    staggerAi(state, CREATURE_EID, 0.3);
    staggerAi(state, CREATURE_EID, 0.1);
    expect(AiStateComponent.staggerTimer[CREATURE_EID]).toBeCloseTo(0.3, 5);
  });

  it('ignores non-positive durations', () => {
    const state = makeStubState() as unknown as State;
    resetHealth(CREATURE_EID, 50);
    AiStateComponent.staggerTimer[CREATURE_EID] = 0;
    staggerAi(state, CREATURE_EID, 0);
    expect(AiStateComponent.staggerTimer[CREATURE_EID]).toBe(0);
  });
});
