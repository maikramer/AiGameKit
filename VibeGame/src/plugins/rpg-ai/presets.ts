import type { State } from '../../core';
import { getDataRegistry } from '../rpg-core/registry';
import { MELEE_AI_KIND } from './components';
import type { MeleeAiConfig } from './components';
import { MeleeAiBehaviour, MonoBehaviour } from '../../extras/melee-ai-base';

export interface CreatureClips {
  idle: string;
  walk: string;
  run: string;
  lunge: string;
  death: string;
  roar?: string;
}

export interface CreatureAssets {
  modelUrl: string;
  clips: CreatureClips;
}

export interface CreatureLoot {
  goldMin: number;
  goldMax: number;
}

/**
 * Full preset stored under the `melee-ai` registry kind: the MeleeAiConfig
 * tuning plus asset/loot extensions consumed by spawn wiring (not the FSM).
 */
export interface MeleeAiPreset extends MeleeAiConfig {
  id?: string;
  hp: number;
  assets: CreatureAssets;
  loot: CreatureLoot;
}

/** Roar intro sub-state — boss-only extension layered over the melee base. */
export interface BossRoarConfig {
  duration: number;
  sound?: string;
}

export interface BossAiPreset extends MeleeAiPreset {
  roar: BossRoarConfig;
}

export function isBossPreset(preset: MeleeAiPreset): preset is BossAiPreset {
  const roar = (preset as Partial<BossAiPreset>).roar;
  return (
    typeof roar === 'object' &&
    roar !== null &&
    typeof roar.duration === 'number'
  );
}

export function loadMeleeAiPreset(
  state: State,
  name: string
): MeleeAiPreset | undefined {
  return getDataRegistry(state).get<MeleeAiPreset>(MELEE_AI_KIND, name);
}

/**
 * Project a preset down to the pure {@link MeleeAiConfig} consumed by the FSM,
 * dropping the asset/hp/loot extensions.
 */
export function presetToMeleeAiConfig(preset: MeleeAiPreset): MeleeAiConfig {
  const cfg: MeleeAiConfig = {
    detectRange: preset.detectRange,
    attackRange: preset.attackRange,
    attackCooldown: preset.attackCooldown,
    attackDamage: preset.attackDamage,
    chaseSpeed: preset.chaseSpeed,
    wanderSpeed: preset.wanderSpeed,
    wanderRadius: preset.wanderRadius,
    leashRadius: preset.leashRadius,
    lungeWindup: preset.lungeWindup,
    lungeDuration: preset.lungeDuration,
    lungeRecovery: preset.lungeRecovery,
    lungeStandoff: preset.lungeStandoff,
    hoverMin: preset.hoverMin,
    hoverMax: preset.hoverMax,
  };
  if (preset.targetEid !== undefined) cfg.targetEid = preset.targetEid;
  return cfg;
}

/**
 * Boss AI built by composition: it owns a {@link MeleeAiBehaviour} instance for
 * the melee FSM and layers a one-time "roar" intro sub-state on top. The roar
 * plays on first update for `roar.duration` seconds (boss stands still), after
 * which every update delegates to the composed melee behaviour. No inheritance
 * from MeleeAiBehaviour — the roar layer wraps the melee base.
 */
export class BossAiBehaviour extends MonoBehaviour {
  private readonly melee: MeleeAiBehaviour;
  private roaring = true;
  private roarTimer: number;

  constructor(meleeConfig: MeleeAiConfig, roarConfig: BossRoarConfig) {
    super();
    this.melee = new MeleeAiBehaviour(meleeConfig);
    this.roarTimer = roarConfig.duration;
  }

  update(state: State, eid: number): void {
    if (this.roaring) {
      this.roarTimer -= state.time.deltaTime;
      if (this.roarTimer <= 0) this.roaring = false;
      return;
    }
    this.melee.update(state, eid);
  }

  override onDestroy(state: State, eid: number): void {
    this.melee.onDestroy(state, eid);
  }
}

/**
 * Factory mirroring {@link createMeleeAi}: returns a parameterless
 * MonoBehaviour subclass that bakes in the melee config + roar intro.
 */
export function createBossAi(
  meleeConfig: MeleeAiConfig,
  roarConfig: BossRoarConfig
): typeof MonoBehaviour {
  return class ConfiguredBossAi extends BossAiBehaviour {
    constructor() {
      super(meleeConfig, roarConfig);
    }
  };
}
