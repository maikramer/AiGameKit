import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  AI_MODE_ATTACK,
  AI_MODE_CHASE,
  AI_MODE_DEAD,
  AI_MODE_DETECT,
  AI_MODE_IDLE,
  AI_MODE_LUNGE,
  AiStateComponent,
  COMBAT_DAMAGED,
  CombatPlugin,
  FactionComponent,
  Health,
  MeleeAiBehaviour,
  RpgAiPlugin,
  State,
  Transform,
  Rigidbody,
  XMLParser,
  bindCombatState,
  createMeleeAi,
  getMeleeAiConfig,
  getDataRegistry,
  onEvent,
  parseXMLToEntities,
  setFaction,
  setMeleeAiConfig,
  type MeleeAiConfig,
} from 'vibegame';

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
  time: { deltaTime: number; elapsed: number };
  exists: (eid: number) => boolean;
  destroyEntity: (eid: number) => void;
  // The chase FSM ensures a NavMeshAgent on its entity; in these unit tests the
  // navmesh is never ready (isNavMeshReady() === false), so the agent is only
  // attached (no-op) and never driven.
  hasComponent: (eid: number, component: unknown) => boolean;
  addComponent: (eid: number, component: unknown) => void;
}

function makeStubState(): StubState {
  return {
    time: { deltaTime: DT, elapsed: 0 },
    exists: () => true,
    destroyEntity: () => {},
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

function resetAi(eid: number): void {
  AiStateComponent.mode[eid] = AI_MODE_IDLE;
  AiStateComponent.target[eid] = 0;
  AiStateComponent.cooldown[eid] = 0;
  AiStateComponent.leash[eid] = 0;
}

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

describe('MeleeAiBehaviour — FSM transitions', () => {
  it('idle→detect when target enters detectRange', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 15, 0);
    place(HERO_EID, 0, 0);

    const ai = new (createMeleeAi(makeConfig()))();
    ai.update(state as unknown as State, CREATURE_EID);

    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_DETECT);
    expect(AiStateComponent.target[CREATURE_EID]).toBe(HERO_EID);
  });

  it('detect→chase after detect grace while still in detectRange', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 15, 0);
    place(HERO_EID, 0, 0);

    const ai = new (createMeleeAi(makeConfig()))();
    ai.update(state as unknown as State, CREATURE_EID);
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_DETECT);
    place(CREATURE_EID, 10, 0);
    // DETECT_GRACE ≈ 0.28s → ~20 frames at 16ms.
    for (let i = 0; i < 25; i++)
      ai.update(state as unknown as State, CREATURE_EID);

    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_CHASE);
  });

  it('chase→attack when target enters attackRange', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 15, 0);
    place(HERO_EID, 0, 0);

    const ai = new (createMeleeAi(makeConfig()))();
    ai.update(state as unknown as State, CREATURE_EID);
    for (let i = 0; i < 25; i++)
      ai.update(state as unknown as State, CREATURE_EID);
    place(CREATURE_EID, 2, 0);
    ai.update(state as unknown as State, CREATURE_EID);

    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_ATTACK);
  });

  it('stays idle when target is out of detectRange', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 0, 0);
    place(HERO_EID, 40, 0);

    const ai = new (createMeleeAi(makeConfig()))();
    ai.update(state as unknown as State, CREATURE_EID);

    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_IDLE);
    expect(AiStateComponent.target[CREATURE_EID]).toBe(0);
  });
});

describe('MeleeAiBehaviour — attack damage via damageHealth + events', () => {
  it('applies attackDamage to the target and emits combat:damaged', () => {
    const state = makeStubState();
    bindCombatState(state as unknown as State);
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 2, 0);
    place(HERO_EID, 0, 0);

    const damaged: unknown[] = [];
    onEvent(state as unknown as State, COMBAT_DAMAGED, (p) => damaged.push(p));

    const ai = new (createMeleeAi(
      makeConfig({
        attackCooldown: 0,
        lungeWindup: 0,
        lungeDuration: 0.01,
        lungeRecovery: 0,
      })
    ))();

    ai.update(state as unknown as State, CREATURE_EID);
    ai.update(state as unknown as State, CREATURE_EID);
    ai.update(state as unknown as State, CREATURE_EID);

    expect(Health.current[HERO_EID]).toBe(90);
    expect(damaged.length).toBe(1);
    expect((damaged[0] as { target: number; amount: number }).target).toBe(
      HERO_EID
    );
    expect((damaged[0] as { amount: number }).amount).toBe(10);
  });

  it('does not damage while attackCooldown is active', () => {
    const state = makeStubState();
    bindCombatState(state as unknown as State);
    resetAi(CREATURE_EID);
    AiStateComponent.cooldown[CREATURE_EID] = 10;
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 2, 0);
    place(HERO_EID, 0, 0);

    const ai = new (createMeleeAi(makeConfig({ attackCooldown: 10 })))();
    ai.update(state as unknown as State, CREATURE_EID);
    ai.update(state as unknown as State, CREATURE_EID);

    expect(Health.current[HERO_EID]).toBe(100);
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_ATTACK);
  });
});

describe('MeleeAiBehaviour — leash returns to idle when target flees', () => {
  it('clears target and returns to idle when target leaves leashRadius', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 5, 0);
    place(HERO_EID, 0, 0);

    const ai = new (createMeleeAi(makeConfig()))();
    ai.update(state as unknown as State, CREATURE_EID);
    for (let i = 0; i < 25; i++)
      ai.update(state as unknown as State, CREATURE_EID);
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_CHASE);

    place(HERO_EID, 50, 0);
    ai.update(state as unknown as State, CREATURE_EID);

    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_IDLE);
    expect(AiStateComponent.target[CREATURE_EID]).toBe(0);
  });
});

describe('MeleeAiBehaviour — lunge does not stick when target leaves melee', () => {
  it('finishes an in-flight lunge instead of freezing in AI_MODE_LUNGE', () => {
    const state = makeStubState();
    bindCombatState(state as unknown as State);
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 1.2, 0);
    place(HERO_EID, 0, 0);

    const ai = new (createMeleeAi(
      makeConfig({
        attackCooldown: 0,
        lungeWindup: 0.05,
        lungeDuration: 0.2,
        lungeRecovery: 0.05,
      })
    ))();

    // Enter attack and start windup/lunge.
    for (let i = 0; i < 8; i++)
      ai.update(state as unknown as State, CREATURE_EID);

    // Target steps outside attackRange but stays in detectRange mid-lunge.
    place(HERO_EID, 5, 0);
    for (let i = 0; i < 40; i++)
      ai.update(state as unknown as State, CREATURE_EID);

    // Must not remain stuck in LUNGE (presentation would loop the jump clip).
    expect(AiStateComponent.mode[CREATURE_EID]).not.toBe(AI_MODE_LUNGE);
    expect(
      [AI_MODE_CHASE, AI_MODE_ATTACK, AI_MODE_IDLE, AI_MODE_DETECT].includes(
        AiStateComponent.mode[CREATURE_EID] as 0 | 1 | 2 | 3
      )
    ).toBe(true);
  });

  it('pushes lunge XZ into Rigidbody so CCT sync cannot wipe the dash', () => {
    const state = makeStubState();
    state.hasComponent = (_eid, component) => component === Rigidbody;
    bindCombatState(state as unknown as State);
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 2.5, 0);
    place(HERO_EID, 0, 0);
    Rigidbody.posX[CREATURE_EID] = 2.5;
    Rigidbody.posY[CREATURE_EID] = 0;
    Rigidbody.posZ[CREATURE_EID] = 0;
    Rigidbody.poseDirty[CREATURE_EID] = 0;

    const ai = new (createMeleeAi(
      makeConfig({
        attackRange: 3,
        attackCooldown: 0,
        lungeWindup: 0,
        lungeDuration: 0.2,
        lungeRecovery: 0.05,
        lungeStandoff: 0.8,
      })
    ))();

    // Drive through windup into the lunge burst (not recovery/steer).
    for (let i = 0; i < 8; i++)
      ai.update(state as unknown as State, CREATURE_EID);

    expect(Rigidbody.poseDirty[CREATURE_EID]).toBe(1);
    expect(Rigidbody.posX[CREATURE_EID]).toBeLessThan(2.5);
    expect(Rigidbody.posX[CREATURE_EID]).toBeCloseTo(
      Transform.posX[CREATURE_EID],
      4
    );
  });
});

describe('MeleeAiBehaviour — death', () => {
  it('enters dead mode when its own health hits zero', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 0);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 2, 0);
    place(HERO_EID, 0, 0);

    const ai = new (createMeleeAi(makeConfig()))();
    ai.update(state as unknown as State, CREATURE_EID);

    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_DEAD);
    expect(AiStateComponent.target[CREATURE_EID]).toBe(0);
  });
});

describe('createMeleeAi factory + MeleeAiBehaviour', () => {
  it('returns a parameterless MonoBehaviour subclass', () => {
    const Cls = createMeleeAi(makeConfig());
    const inst = new Cls();
    expect(inst).toBeInstanceOf(MeleeAiBehaviour);
    expect(typeof inst.update).toBe('function');
  });

  it('factory-baked config drives the FSM', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 50);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 15, 0);
    place(HERO_EID, 0, 0);

    const inst = new (createMeleeAi(makeConfig()))();
    inst.update(state as unknown as State, CREATURE_EID);
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_DETECT);
  });
});

describe('RpgAiSystem integration (state.step)', () => {
  it('acquires a hostile target via FactionComponent + isHostile and transitions to detect', () => {
    const state = new State();
    state.registerPlugin(CombatPlugin);
    state.registerPlugin(RpgAiPlugin);
    getDataRegistry(state).register('faction-hostility', 'default', {
      pairs: [['player', 'enemy']],
    });

    const hero = state.createEntity();
    state.addComponent(hero, Health);
    state.addComponent(hero, FactionComponent);
    setFaction(state, hero, 'player');
    resetHealth(hero, 100);
    Transform.posX[hero] = 0;
    Transform.posZ[hero] = 0;

    const creature = state.createEntity();
    state.addComponent(creature, AiStateComponent);
    state.addComponent(creature, Health);
    state.addComponent(creature, FactionComponent);
    setFaction(state, creature, 'enemy');
    resetHealth(creature, 50);
    Transform.posX[creature] = 15;
    Transform.posZ[creature] = 0;
    setMeleeAiConfig(state, creature, makeConfig({ targetEid: undefined }));

    state.step(DT);

    expect(AiStateComponent.mode[creature]).toBe(AI_MODE_DETECT);
    expect(AiStateComponent.target[creature]).toBe(hero);
  });
});

describe('<MeleeAi preset> recipe + registry', () => {
  it("preset lookup resolves config via getDataRegistry('melee-ai')", () => {
    const state = new State();
    state.registerPlugin(CombatPlugin);
    state.registerPlugin(RpgAiPlugin);
    const goblin = makeConfig({ targetEid: undefined, chaseSpeed: 2.0 });
    getDataRegistry(state).register('melee-ai', 'goblin', goblin);

    const xml = `<Scene><MeleeAi preset="goblin" pos="15 0 0"></MeleeAi></Scene>`;
    const root = XMLParser.parse(xml).root;
    const results = parseXMLToEntities(state, root);

    expect(results.length).toBe(1);
    const eid = results[0].entity;
    expect(state.hasComponent(eid, AiStateComponent)).toBe(true);
    const resolved = getMeleeAiConfig(state, eid);
    expect(resolved).toBeDefined();
    expect(resolved?.chaseSpeed).toBe(2.0);
    expect(resolved?.detectRange).toBe(18);
  });

  it('falls back gracefully when preset is missing (warns, no throw)', () => {
    const state = new State();
    state.registerPlugin(CombatPlugin);
    state.registerPlugin(RpgAiPlugin);

    const xml = `<Scene><MeleeAi preset="nonexistent" pos="0 0 0"></MeleeAi></Scene>`;
    const root = XMLParser.parse(xml).root;

    expect(() => parseXMLToEntities(state, root)).not.toThrow();
  });
});

describe('exported mode constants', () => {
  it('mode enum values match the spec', () => {
    expect(AI_MODE_IDLE).toBe(0);
    expect(AI_MODE_DETECT).toBe(1);
    expect(AI_MODE_CHASE).toBe(2);
    expect(AI_MODE_ATTACK).toBe(3);
    expect(AI_MODE_LUNGE).toBe(4);
    expect(AI_MODE_DEAD).toBe(5);
  });
});
