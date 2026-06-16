import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { join } from 'node:path';
import {
  AI_MODE_DETECT,
  AiStateComponent,
  CombatPlugin,
  Health,
  MELEE_AI_KIND,
  MeleeAiBehaviour,
  RpgAiPlugin,
  State,
  Transform,
  XMLParser,
  createBossAi,
  getDataRegistry,
  getMeleeAiConfig,
  isBossPreset,
  loadMeleeAiPreset,
  parseXMLToEntities,
  presetToMeleeAiConfig,
  type MeleeAiConfig,
  type MeleeAiPreset,
} from 'vibegame';

const HERO_EID = 1;
const CREATURE_EID = 2;
const DT = 0.016;
const PRESET_DIR = join(
  import.meta.dir,
  '../../../examples/simple-rpg/public/data/ai'
);

interface StubState {
  time: { deltaTime: number; elapsed: number };
  exists: (eid: number) => boolean;
  destroyEntity: (eid: number) => void;
}

function makeStubState(): StubState {
  return {
    time: { deltaTime: DT, elapsed: 0 },
    exists: () => true,
    destroyEntity: () => {},
  };
}

function resetHealth(eid: number, hp: number): void {
  Health.current[eid] = hp;
  Health.max[eid] = hp;
}

function place(eid: number, x: number, z: number): void {
  Transform.posX[eid] = x;
  Transform.posY[eid] = 0;
  Transform.posZ[eid] = z;
}

function resetAi(eid: number): void {
  AiStateComponent.mode[eid] = 0;
  AiStateComponent.target[eid] = 0;
  AiStateComponent.cooldown[eid] = 0;
  AiStateComponent.leash[eid] = 0;
}

async function loadPresetFile(
  state: State,
  file: string
): Promise<void> {
  const text = await Bun.file(`${PRESET_DIR}/${file}`).text();
  getDataRegistry(state).loadYaml(text);
}

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

describe('creature presets — YAML load + registry lookup', () => {
  it('slime.yaml parses into a MeleeAiPreset with expected tuning', async () => {
    const state = new State();
    await loadPresetFile(state, 'slime.yaml');

    const slime = loadMeleeAiPreset(state, 'slime');
    expect(slime).toBeDefined();
    expect(slime?.id).toBe('slime');
    expect(slime?.hp).toBe(50);
    expect(slime?.chaseSpeed).toBe(1.8);
    expect(slime?.attackDamage).toBe(18);
    expect(slime?.detectRange).toBe(18);
    expect(slime?.assets.modelUrl).toBe(
      '/assets/meshes/slime_rigged_animated.glb'
    );
    expect(slime?.loot.goldMin).toBe(15);
    expect(slime?.loot.goldMax).toBe(30);
  });

  it('goblin.yaml parses into a MeleeAiPreset distinct from slime', async () => {
    const state = new State();
    await loadPresetFile(state, 'goblin.yaml');

    const goblin = getDataRegistry(state).get<MeleeAiPreset>(
      MELEE_AI_KIND,
      'goblin'
    );
    expect(goblin).toBeDefined();
    expect(goblin?.hp).toBe(40);
    expect(goblin?.chaseSpeed).toBe(2.4);
    expect(goblin?.attackDamage).toBe(12);
    expect(goblin?.assets.modelUrl).toBe(
      '/assets/meshes/goblin_rigged_animated.glb'
    );
  });

  it('boss.yaml parses into a preset with the roar extension', async () => {
    const state = new State();
    await loadPresetFile(state, 'boss.yaml');

    const boss = loadMeleeAiPreset(state, 'boss');
    expect(boss).toBeDefined();
    expect(boss?.hp).toBe(300);
    expect(boss?.chaseSpeed).toBe(2.8);
    expect(boss?.attackDamage).toBe(25);
    expect(isBossPreset(boss!)).toBe(true);
    if (isBossPreset(boss!)) {
      expect(boss.roar.duration).toBe(2.5);
      expect(boss.roar.sound).toBe('boss-roar');
    }
  });

  it('slime/goblin presets are not boss presets (no roar field)', async () => {
    const state = new State();
    await loadPresetFile(state, 'slime.yaml');
    await loadPresetFile(state, 'goblin.yaml');
    const slime = loadMeleeAiPreset(state, 'slime')!;
    const goblin = loadMeleeAiPreset(state, 'goblin')!;
    expect(isBossPreset(slime)).toBe(false);
    expect(isBossPreset(goblin)).toBe(false);
  });

  it('presetToMeleeAiConfig drops asset/hp/loot extensions', async () => {
    const state = new State();
    await loadPresetFile(state, 'slime.yaml');
    const slime = loadMeleeAiPreset(state, 'slime')!;
    const cfg = presetToMeleeAiConfig(slime);
    const keys = Object.keys(cfg) as (keyof MeleeAiConfig)[];
    expect(keys).not.toContain('hp');
    expect(keys).not.toContain('assets');
    expect(keys).not.toContain('loot');
    expect(cfg.chaseSpeed).toBe(1.8);
    expect(cfg.detectRange).toBe(18);
  });
});

describe('<MeleeAi preset> recipe resolves from YAML presets', () => {
  it('<MeleeAi preset="slime"/> wires the preset config onto the entity', async () => {
    const state = new State();
    state.registerPlugin(CombatPlugin);
    state.registerPlugin(RpgAiPlugin);
    await loadPresetFile(state, 'slime.yaml');

    const xml = `<Scene><MeleeAi preset="slime" pos="15 0 0"></MeleeAi></Scene>`;
    const root = XMLParser.parse(xml).root;
    const results = parseXMLToEntities(state, root);

    expect(results.length).toBe(1);
    const eid = results[0].entity;
    expect(state.hasComponent(eid, AiStateComponent)).toBe(true);

    const resolved = getMeleeAiConfig(state, eid);
    expect(resolved).toBeDefined();
    expect(resolved?.chaseSpeed).toBe(1.8);
    expect(resolved?.detectRange).toBe(18);
    expect(resolved?.attackDamage).toBe(18);
  });

  it('<MeleeAi preset="goblin"/> resolves goblin tuning', async () => {
    const state = new State();
    state.registerPlugin(CombatPlugin);
    state.registerPlugin(RpgAiPlugin);
    await loadPresetFile(state, 'goblin.yaml');

    const xml = `<Scene><MeleeAi preset="goblin" pos="0 0 0"></MeleeAi></Scene>`;
    const root = XMLParser.parse(xml).root;
    const [result] = parseXMLToEntities(state, root);

    const resolved = getMeleeAiConfig(state, result.entity);
    expect(resolved?.chaseSpeed).toBe(2.4);
    expect(resolved?.attackDamage).toBe(12);
  });
});

describe('createBossAi — composition over inheritance', () => {
  function makeMeleeConfig(): MeleeAiConfig {
    return {
      detectRange: 40,
      attackRange: 4,
      attackCooldown: 1.5,
      attackDamage: 25,
      chaseSpeed: 2.8,
      wanderSpeed: 0,
      wanderRadius: 0,
      leashRadius: 100,
      lungeWindup: 0,
      lungeDuration: 0,
      lungeRecovery: 0,
      lungeStandoff: 2,
      hoverMin: 0,
      hoverMax: 0,
      targetEid: HERO_EID,
    };
  }

  it('returns a MonoBehaviour that is NOT a MeleeAiBehaviour (composition)', () => {
    const Cls = createBossAi(makeMeleeConfig(), { duration: 2.5 });
    const inst = new Cls();
    expect(typeof inst.update).toBe('function');
    expect(inst).not.toBeInstanceOf(MeleeAiBehaviour);
  });

  it('roars first (melee FSM paused), then delegates to melee after duration', () => {
    const state = makeStubState();
    resetAi(CREATURE_EID);
    resetHealth(CREATURE_EID, 300);
    resetHealth(HERO_EID, 100);
    place(CREATURE_EID, 20, 0);
    place(HERO_EID, 0, 0);

    const Cls = createBossAi(makeMeleeConfig(), { duration: DT });
    const boss = new Cls();

    boss.update(state as unknown as State, CREATURE_EID);
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(0);

    boss.update(state as unknown as State, CREATURE_EID);
    expect(AiStateComponent.mode[CREATURE_EID]).toBe(AI_MODE_DETECT);
  });
});
