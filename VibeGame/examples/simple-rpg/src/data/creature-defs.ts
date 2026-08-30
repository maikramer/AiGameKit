// Creature configs — single source of truth for every mob/boss build.
// Each wrapper script (scripts/enemies/*, scripts/bosses/*, archer, slime,
// goblin-wander, boss) just picks its def and hands it to
// createCreatureBehaviours — the per-file configs used to live inline in a
// dozen near-identical copies.
import type { CreatureConfig } from '../scripts/creature';
import { addGold } from '../game/economy';
import { everSpawned, aliveInBiome } from '../scripts/enemy-registry';

/** Every creature drops gold into the vault (positional args ignored). */
const goldLoot: CreatureConfig['onDeathLoot'] = (_state, gold, x, y, z) =>
  addGold(gold, x, y, z);

/** Standard clip map for the character GLB packs (idle/walk/run/lunge/…). */
const STANDARD_CLIPS = {
  idle: 'idle',
  walk: 'walk',
  run: 'run',
  lunge: 'jump',
  death: 'death',
  hit: 'hit',
  attack: 'attack',
} as const;

export const CREATURE_DEFS: Record<string, CreatureConfig> = {
  // ── Mobs ──────────────────────────────────────────────────────────────
  wolf: {
    // Hit-and-run: almost no telegraph, a fast long dash in, then a long
    // recovery where it backs off (lowHpKite + strafe). It never stands in
    // melee range.
    modelUrl: '/assets/meshes/characters/wolf_lod2.glb',
    clips: STANDARD_CLIPS,
    hp: 55,
    chaseSpeed: 3.6,
    wanderSpeed: 1.3,
    wanderRadius: 14,
    attackDamage: 12,
    detectRange: 20,
    leashRadius: 34,
    attackCooldown: 2.4,
    lootGoldMin: 6,
    lootGoldMax: 14,
    strafe: true,
    lowHpKiteFrac: 0.3,
    enrageBelowFrac: 0.25,
    lungeWindup: 0.1,
    lungeDuration: 0.35,
    lungeRecovery: 0.7,
    lungeStandoff: 1.2,
    enemyType: 'wolf',
    // Rosnido ao activar (primeira detecção do jogador).
    roarSound: 'wolf-growl',
    behaviorProfile: { separate: true, flock: true },
    onDeathLoot: goldLoot,
  },

  slime: {
    // Tanky blob: no strafe, no kite — it presses into melee and stays there,
    // oozing onto the player. Slow but durable; short lunge, short recovery so
    // it keeps contact rather than darting away.
    modelUrl: '/assets/meshes/characters/slime_lod2.glb',
    clips: STANDARD_CLIPS,
    hp: 95,
    chaseSpeed: 1.6,
    wanderSpeed: 0.4,
    wanderRadius: 8,
    attackDamage: 16,
    attackCooldown: 1.8,
    lootGoldMin: 15,
    lootGoldMax: 30,
    lungeWindup: 0.3,
    lungeDuration: 0.2,
    lungeRecovery: 0.3,
    lungeStandoff: 0.6,
    enemyType: 'slime',
    roarSound: 'slime-squish',
    onDeathLoot: goldLoot,
  },

  bandit: {
    // Same melee timing/feel as goblin-wander (short jab, quick recovery).
    // Identity (mesh, HP, loot, enemyType) stays bandit so desert quests keep
    // counting kills.
    modelUrl: '/assets/meshes/characters/bandit_lod2.glb',
    // Cortes B/C (diferentes dos do goblin) para variedade no mesmo bioma.
    clips: {
      ...STANDARD_CLIPS,
      attack: ['attack', 'swordb', 'swordc'],
      // O pack traz `knockback` — golpe pesado/crítico atira o bandido para
      // trás em vez de repetir sempre o mesmo `hit`.
      knockback: 'knockback',
    },
    hp: 80,
    chaseSpeed: 2.7,
    wanderSpeed: 1.1,
    wanderRadius: 12,
    attackDamage: 13,
    detectRange: 16,
    leashRadius: 28,
    attackCooldown: 1.6,
    lootGoldMin: 15,
    lootGoldMax: 35,
    strafe: true,
    lowHpKiteFrac: 0.35,
    // Match goblin-wander lunge — longer windup/standoff made the CCT dash
    // miss after the first swing (see rpg-ai applyLungeMovement Rigidbody
    // sync).
    lungeWindup: 0.18,
    lungeDuration: 0.22,
    lungeRecovery: 0.35,
    lungeStandoff: 0.8,
    enemyType: 'bandit',
    behaviorProfile: { separate: true },
    onDeathLoot: goldLoot,
  },

  bogling: {
    // Swarming pest: fragile, frantic, very short windup — throws itself at
    // the player in numbers. Dies fast but interrupts casts/movement.
    modelUrl: '/assets/meshes/characters/bogling_lod2.glb',
    // Praga do pântano: arranhos zumbis em vez de golpes de espada.
    clips: {
      ...STANDARD_CLIPS,
      attack: ['attack', 'zombiescratch'],
    },
    hp: 45,
    chaseSpeed: 3.0,
    wanderSpeed: 1.0,
    wanderRadius: 6,
    attackDamage: 7,
    attackCooldown: 1.4,
    lootGoldMin: 4,
    lootGoldMax: 10,
    lungeWindup: 0.12,
    lungeDuration: 0.2,
    lungeRecovery: 0.3,
    lungeStandoff: 0.7,
    enemyType: 'bogling',
    behaviorProfile: { separate: true, flock: true },
    onDeathLoot: goldLoot,
  },

  scorpion: {
    // Heavy bruiser: long, readable telegraph before the sting — a player who
    // watches can sidestep. When it does connect, it hurts. Enrage shortens
    // the windup window dramatically (becomes dangerous at low HP).
    modelUrl: '/assets/meshes/characters/scorpion_lod2.glb',
    // 0.5s de windup é o telegrafo mais longo do bestiário: dá para armar a
    // cauda (`roar` do pack Animator3D) antes da picada.
    clips: { ...STANDARD_CLIPS, windup: 'roar' },
    hp: 90,
    chaseSpeed: 1.6,
    wanderSpeed: 0.5,
    wanderRadius: 4,
    attackDamage: 18,
    attackCooldown: 2.8,
    lootGoldMin: 8,
    lootGoldMax: 18,
    enrageBelowFrac: 0.4,
    lungeWindup: 0.5,
    lungeDuration: 0.28,
    lungeRecovery: 0.6,
    lungeStandoff: 1.0,
    enrageSpeedMult: 1.5,
    enrageCooldownMult: 0.45,
    enemyType: 'scorpion',
    onDeathLoot: goldLoot,
  },

  shade: {
    // shade_*.glb ships static (no skin/clips) — the XML visual uses the
    // bogling LOD triple at 1.4x, so the animator clips must come from that
    // same master.
    // Evasive glass-cannon: high damage but fragile, so it kites hard at low
    // HP. Long-ish windup telegraphs the strike; the threat is it relentless
    // re-engages from orbit rather than committing.
    modelUrl: '/assets/meshes/characters/bogling_lod0.glb',
    // Espírito: shamble zumbi no wander/idle (eerie) + lunge normal na carga.
    clips: { ...STANDARD_CLIPS, idle: 'zombieidle', walk: 'zombiewalk' },
    hp: 40,
    chaseSpeed: 2.2,
    wanderSpeed: 0.9,
    wanderRadius: 10,
    attackDamage: 15,
    detectRange: 18,
    leashRadius: 30,
    attackCooldown: 2.6,
    lootGoldMin: 10,
    lootGoldMax: 20,
    strafe: true,
    lowHpKiteFrac: 0.45,
    lungeWindup: 0.32,
    lungeDuration: 0.26,
    lungeRecovery: 0.45,
    lungeStandoff: 1.0,
    enemyType: 'shade',
    behaviorProfile: { separate: true },
    onDeathLoot: goldLoot,
  },

  goblin: {
    // Agile skirmisher: short telegraph, quick jab, fast recovery — hits often
    // but soft. Packs weave around the player rather than body-blocking.
    modelUrl: '/assets/meshes/characters/goblin_lod2.glb',
    // Ataques em ciclo: slash base + variações A/B da UAL2.
    clips: {
      ...STANDARD_CLIPS,
      attack: ['attack', 'sworda', 'swordb'],
      knockback: 'knockback',
      // Guincho antes do salto: o tell fica legível na silhueta, não só no
      // brilho do windup (0.18s ainda dá para ler o arranque do clip).
      windup: 'roar',
    },
    hp: 65,
    chaseSpeed: 2.7,
    wanderSpeed: 1.1,
    wanderRadius: 12,
    attackDamage: 9,
    detectRange: 16,
    leashRadius: 28,
    attackCooldown: 1.6,
    lootGoldMin: 8,
    lootGoldMax: 18,
    strafe: true,
    lowHpKiteFrac: 0.35,
    lungeWindup: 0.18,
    lungeDuration: 0.22,
    lungeRecovery: 0.35,
    lungeStandoff: 0.8,
    enemyType: 'goblin',
    behaviorProfile: { separate: true },
    onDeathLoot: goldLoot,
  },

  archer: {
    // Plains skirmisher — same mesh as bandit, same melee feel as
    // goblin-wander. Was a ranged placeholder (`enemy-arrow`); identical
    // bandit GLB + attack clip in ATTACK mode looked like melee swings that
    // never dealt close damage (lunge suppressed by rangedTemplate). Until a
    // dedicated archer asset exists, fight like the melee bandit / goblin.
    modelUrl: '/assets/meshes/characters/bandit_lod2.glb',
    clips: STANDARD_CLIPS,
    hp: 60,
    chaseSpeed: 2.7,
    wanderSpeed: 1.1,
    wanderRadius: 12,
    attackDamage: 9,
    detectRange: 16,
    leashRadius: 28,
    attackCooldown: 1.6,
    lootGoldMin: 12,
    lootGoldMax: 26,
    strafe: true,
    lowHpKiteFrac: 0.35,
    lungeWindup: 0.18,
    lungeDuration: 0.22,
    lungeRecovery: 0.35,
    lungeStandoff: 0.8,
    enemyType: 'bandit',
    behaviorProfile: { separate: true },
    onDeathLoot: goldLoot,
  },

  // ── Bosses ─────────────────────────────────────────────────────────────
  boss_ogre: {
    // Boss ogre — same engine MeleeAi FSM as the creatures (one AI brain),
    // with boss extras layered on by the shared presentation: dormant until
    // every normal enemy is dead (gate), an intro roar on reveal, relentless
    // pursuit (huge detect + leash), strafing, and an enrage phase at low HP.
    modelUrl: '/assets/meshes/characters/boss_ogre_lod2.glb',
    clips: {
      idle: 'idle',
      walk: 'walk',
      run: 'walk',
      // Golpes pesados UAL2: combo overhand (machado) alternado com hook/punch.
      lunge: 'swordheavy',
      death: 'death',
      roar: 'roar',
      hit: 'hit',
      // Reação pesada separada da leve: pancada normal encolhe (`hit`),
      // crítico/finisher atira para trás (`knockback`).
      knockback: 'knockback',
      // Rugido como telegrafo do golpe pesado (windup default 0.25s).
      windup: 'roar',
      attack: ['punch', 'hook'],
    },
    hp: 350,
    chaseSpeed: 3.0,
    wanderSpeed: 0, // stays put until it spots the player, then hunts
    wanderRadius: 1,
    attackDamage: 25,
    attackRange: 2.2,
    attackCooldown: 1.6,
    detectRange: 120, // relentless — always sees the player once awake
    leashRadius: 1000, // never leashes home
    strafe: true,
    enrageBelowFrac: 0.3,
    runTimeScale: 1.5,
    lootGoldMin: 100,
    lootGoldMax: 150,
    isBoss: true,
    defeatedText: 'ORN FOI DESFEITO!',
    roarSound: 'boss-roar',
    // Gate: appear only after every enemy in the frozen-peaks biome is dead.
    gateUntil: () => everSpawned() && aliveInBiome('frozen-peaks') === 0,
    enemyType: 'boss_ogre',
    onDeathLoot: goldLoot,
  },

  bog_warden: {
    // Swamp elite boss — always-active at the far south of the swamp biome.
    modelUrl: '/assets/meshes/characters/bog_warden_boss_lod2.glb',
    // Guardião: gancho (puxão) e golpe pesado alternados.
    clips: {
      idle: 'idle',
      walk: 'walk',
      run: 'walk',
      lunge: 'hook',
      death: 'death',
      hit: 'hit',
      knockback: 'knockback',
      windup: 'roar',
      attack: ['attack', 'swordheavy'],
    },
    hp: 200,
    chaseSpeed: 2.8,
    wanderSpeed: 0.6,
    wanderRadius: 6,
    attackDamage: 20,
    attackRange: 2.0,
    attackCooldown: 1.9,
    detectRange: 24,
    leashRadius: 45,
    strafe: true,
    enrageBelowFrac: 0.3,
    runTimeScale: 1.5,
    lootGoldMin: 80,
    lootGoldMax: 120,
    isBoss: true,
    defeatedText: 'O CONTADOR FOI DESFEITO!',
    enemyType: 'boss_bogwarden',
    onDeathLoot: goldLoot,
  },

  sand_worm: {
    // Desert elite boss — always-active at the far east of the desert biome.
    modelUrl: '/assets/meshes/characters/sand_worm_lod2.glb',
    clips: {
      idle: 'idle',
      walk: 'walk',
      run: 'walk',
      lunge: 'jump',
      death: 'death',
      hit: 'hit',
      windup: 'roar',
      attack: 'attack',
    },
    hp: 220,
    chaseSpeed: 3.4,
    wanderSpeed: 0.8,
    wanderRadius: 8,
    attackDamage: 22,
    attackRange: 2.2,
    attackCooldown: 2.0,
    detectRange: 26,
    leashRadius: 50,
    strafe: false,
    enrageBelowFrac: 0.35,
    runTimeScale: 1.5,
    lootGoldMin: 90,
    lootGoldMax: 130,
    isBoss: true,
    defeatedText: 'SARRA FOI DESFEITA!',
    enemyType: 'boss_sandworm',
    onDeathLoot: goldLoot,
  },

  witch: {
    // Forest elite boss — same engine MeleeAi FSM as the creatures, tuned as
    // an always-active elite at the far north of the dark forest biome (no
    // global gate — it guards its lair until approached).
    modelUrl: '/assets/meshes/characters/witch_boss_lod2.glb',
    // NB: o rig da bruxa não é humanoid-detectável (chapéu/robe = cadeias
    // extra) — game-pack cai no procedural (Animator3D_*); 'spellcast' etc.
    // nunca existem neste GLB.
    clips: {
      idle: 'idle',
      walk: 'walk',
      run: 'walk',
      lunge: 'jump',
      death: 'death',
      hit: 'hit',
      attack: 'attack',
    },
    hp: 185,
    chaseSpeed: 3.0,
    wanderSpeed: 0.6,
    wanderRadius: 6,
    attackDamage: 18,
    attackRange: 1.8,
    attackCooldown: 1.8,
    detectRange: 24,
    leashRadius: 45,
    strafe: true,
    enrageBelowFrac: 0.3,
    runTimeScale: 1.5,
    lootGoldMin: 70,
    lootGoldMax: 110,
    isBoss: true,
    defeatedText: 'VÉSPER FOI DESFEITA!',
    enemyType: 'boss_witch',
    onDeathLoot: goldLoot,
  },
};
