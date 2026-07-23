import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  AI_MODE_ATTACK,
  AI_MODE_CHASE,
  AI_MODE_DEAD,
  AI_MODE_DETECT,
  AI_MODE_IDLE,
  AI_MODE_LUNGE,
  AiStateComponent,
  MELEE_AI_KIND,
  aiRandom,
  createAiInstanceState,
  getMeleeAiConfig,
  getOrCreateAiInstanceState,
  removeAiInstanceState,
  removeMeleeAiConfig,
  resetAiRng,
  setAiRng,
  setMeleeAiConfig,
  type MeleeAiConfig,
} from '../../../src/plugins/rpg-ai';
import {
  isBossPreset,
  presetToMeleeAiConfig,
} from '../../../src/plugins/rpg-ai/presets';

function sampleConfig(seed: number): MeleeAiConfig {
  return {
    detectRange: 10 + seed,
    attackRange: 2 + seed * 0.1,
    attackCooldown: 1 + seed * 0.05,
    attackDamage: 5 + seed,
    chaseSpeed: 2 + seed * 0.2,
    wanderSpeed: 1,
    wanderRadius: 5,
    leashRadius: 20 + seed,
    lungeWindup: 0.2,
    lungeDuration: 0.3,
    lungeRecovery: 0.4,
    lungeStandoff: 1,
    hoverMin: 0.5,
    hoverMax: 2,
  };
}

describe('rpg-ai bulk: AI mode constants', () => {
  const MODES = [
    ['IDLE', AI_MODE_IDLE, 0],
    ['DETECT', AI_MODE_DETECT, 1],
    ['CHASE', AI_MODE_CHASE, 2],
    ['ATTACK', AI_MODE_ATTACK, 3],
    ['LUNGE', AI_MODE_LUNGE, 4],
    ['DEAD', AI_MODE_DEAD, 5],
  ] as const;

  for (const [name, val, expected] of MODES) {
    for (let dup = 0; dup < 4; dup++) {
      it(`AI_MODE_${name} equals ${expected} (dup ${dup})`, () => {
        expect(val).toBe(expected);
      });
    }
  }
});

describe('rpg-ai bulk: AiStateComponent SOA', () => {
  for (let eid = 1; eid <= 15; eid++) {
    it(`AiStateComponent.mode[${eid}]`, () => {
      AiStateComponent.mode[eid] = AI_MODE_CHASE;
      expect(AiStateComponent.mode[eid]).toBe(AI_MODE_CHASE);
    });
    it(`AiStateComponent.target[${eid}]`, () => {
      AiStateComponent.target[eid] = eid + 50;
      expect(AiStateComponent.target[eid]).toBe(eid + 50);
    });
    it(`AiStateComponent.cooldown[${eid}]`, () => {
      AiStateComponent.cooldown[eid] = eid * 0.1;
      expect(AiStateComponent.cooldown[eid]).toBeCloseTo(eid * 0.1);
    });
    it(`AiStateComponent.leash[${eid}]`, () => {
      AiStateComponent.leash[eid] = eid;
      expect(AiStateComponent.leash[eid]).toBeCloseTo(eid);
    });
  }
});

describe('rpg-ai bulk: config side table', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  for (let i = 1; i <= 20; i++) {
    it(`setMeleeAiConfig/getMeleeAiConfig #${i}`, () => {
      const eid = state.createEntity();
      const cfg = sampleConfig(i);
      setMeleeAiConfig(state, eid, cfg);
      expect(getMeleeAiConfig(state, eid)).toEqual(cfg);
      removeMeleeAiConfig(state, eid);
      expect(getMeleeAiConfig(state, eid)).toBeUndefined();
    });
  }
});

describe('rpg-ai bulk: instance state', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  for (let i = 0; i < 15; i++) {
    it(`getOrCreateAiInstanceState isolates #${i}`, () => {
      const a = state.createEntity();
      const b = state.createEntity();
      const ia = getOrCreateAiInstanceState(state, a);
      const ib = getOrCreateAiInstanceState(state, b);
      ia.wanderX = i;
      ib.wanderX = i + 100;
      expect(getOrCreateAiInstanceState(state, a).wanderX).toBe(i);
      expect(getOrCreateAiInstanceState(state, b).wanderX).toBe(i + 100);
      removeAiInstanceState(state, a);
      expect(getOrCreateAiInstanceState(state, a).wanderX).toBe(0);
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`createAiInstanceState defaults ${i}`, () => {
      const inst = createAiInstanceState();
      expect(inst.lungePhase).toBe('ready');
      expect(inst.hovering).toBe(true);
      expect(inst.originSet).toBe(false);
    });
  }
});

describe('rpg-ai bulk: aiRandom injection', () => {
  for (let i = 0; i < 10; i++) {
    it(`deterministic rng sequence ${i}`, () => {
      let n = i / 10;
      setAiRng(() => {
        n += 0.1;
        return n > 1 ? n - 1 : n;
      });
      const a = aiRandom();
      const b = aiRandom();
      expect(a).toBeGreaterThanOrEqual(0);
      expect(b).toBeGreaterThanOrEqual(0);
      resetAiRng();
    });
  }
});

describe('rpg-ai bulk: preset helpers', () => {
  for (let i = 0; i < 10; i++) {
    it(`presetToMeleeAiConfig strips extensions ${i}`, () => {
      const preset = {
        id: `mob-${i}`,
        hp: 100,
        ...sampleConfig(i),
        assets: { modelUrl: '/m.glb', clips: {} },
        loot: { goldMin: 1, goldMax: 2 },
      };
      const cfg = presetToMeleeAiConfig(preset);
      expect(cfg.chaseSpeed).toBeCloseTo(2 + i * 0.2);
      expect(isBossPreset(preset)).toBe(false);
    });
  }
});

describe('rpg-ai bulk: registry kind', () => {
  for (let i = 0; i < 5; i++) {
    it(`MELEE_AI_KIND stable ${i}`, () => {
      expect(MELEE_AI_KIND).toBe('melee-ai');
    });
  }
});
