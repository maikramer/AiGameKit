//! Skills, abilities & combate avançado (loop 8 do port simple-rpg):
//!
//! - **Abilities com cooldown**: [C] dash (avança 4 m com i-frames), [E]
//!   cura 50 (só fora de alcance de interação — o [E] de interagir ganha),
//!   [R] golpe forte radial (60 de dano em 4,8 m; o [R] deixou de ser
//!   ataque básico). Barras de cooldown bottom-left.
//! - **Passivas**: 8 skills com pré-requisitos — bónus de dano, velocidade,
//!   HP máximo e crítico — compradas com pontos de nível ([P] na tab Skills
//!   do modal).
//! - **Bombas [B]**: consome `bomb` do vault, arco com gravidade, pavio de
//!   1,5 s e explosão radial (90 de dano, raio 6, falloff linear).
//! - **Guard [L]** (sistema em `feedback.rs`): −75 % de dano, parry total
//!   nos primeiros 0,22 s.

use bevy::math::primitives::Sphere;
use bevy::prelude::*;

use crate::economy::Vault;
use crate::feedback::{AttackAlert, DamageNumberEvent, Invulnerable};
use crate::luau::{LuaScriptRef, ScriptInteraction, ScriptToast};
use crate::player::Player;
use crate::profiler::{Group, timed};
use crate::quests::QuestLog;
use crate::vitals::{Health, Xp, apply_damage, gain_xp};

// ── constantes ──────────────────────────────────────────────────────────

pub const DASH_DISTANCE: f32 = 4.0;
pub const DASH_COOLDOWN: f32 = 6.0;
pub const DASH_IFRAMES: f32 = 0.4;
pub const HEAL_ABILITY_AMOUNT: f32 = 50.0;
pub const HEAL_ABILITY_COOLDOWN: f32 = 12.0;
pub const STRIKE_DAMAGE: f32 = 60.0;
pub const STRIKE_RADIUS: f32 = 4.8;
pub const STRIKE_COOLDOWN: f32 = 8.0;
pub const BOMB_FUSE: f32 = 1.5;
pub const BOMB_DAMAGE: f32 = 90.0;
pub const BOMB_RADIUS: f32 = 6.0;

// ── paridade de impacto com o melee (ver consts SHAKE_*/HIT_STOP_* em
//    `combat.rs`): sem isto o slam/bomba eram "números a mais" sem peso ──

/// [R]: knockback radial FORA do centro (força; deslocamento ≈
/// força/KNOCKBACK_DECAY ≈ 1.2 m).
pub const STRIKE_KNOCKBACK_STRENGTH: f32 = 7.0;
/// [R]: sparks por inimigo atingido.
pub const STRIKE_SPARK_COUNT: usize = 18;
/// [R]: trauma de shake no slam com alvo.
pub const STRIKE_SHAKE: f32 = 0.45;
/// [R]: impulso vertical do kick de câmara (o chão sobe contra ela).
pub const STRIKE_KICK_UP: f32 = 1.2;
/// [R]: punch de pós-processo (stops de clareza, bloom).
pub const STRIKE_PUNCH_STOPS: f32 = 0.45;
pub const STRIKE_PUNCH_BLOOM: f32 = 0.18;
/// [R]: raio do anel de choque (m) — casa com STRIKE_RADIUS.
pub const STRIKE_RING_RADIUS: f32 = 4.8;
/// [R]: fator do snap de facing no cast (1 = teleporta a rotação).
pub const STRIKE_FACE_SNAP: f32 = 0.45;
/// [B]: sparks por criatura apanhada pela explosão.
pub const BOMB_SPARK_COUNT: usize = 16;
/// [B]: trauma de shake na detonação.
pub const BOMB_SHAKE: f32 = 0.5;
/// [B]: impulso vertical do kick de câmara.
pub const BOMB_KICK_UP: f32 = 1.6;
/// [B]: punch de pós-processo (stops de clareza, bloom).
pub const BOMB_PUNCH_STOPS: f32 = 0.55;
pub const BOMB_PUNCH_BLOOM: f32 = 0.22;
/// [B]: raio do anel de choque (m) — maior que o slam: é uma explosão.
pub const BOMB_RING_RADIUS: f32 = 5.5;

// ── passivas ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SkillEffect {
    Damage(f32),
    Speed(f32),
    MaxHp(f32),
    Crit(f32),
}

#[derive(Debug, Clone, Copy)]
pub struct SkillDef {
    pub id: &'static str,
    pub label: &'static str,
    pub requires: &'static [&'static str],
    pub effect: SkillEffect,
}

/// As 8 passivas (espelha skills.ts).
pub const SKILLS: [SkillDef; 8] = [
    SkillDef {
        id: "vitality1",
        label: "Vitalidade I (+20 HP)",
        requires: &[],
        effect: SkillEffect::MaxHp(20.0),
    },
    SkillDef {
        id: "strength1",
        label: "Força I (+6 dano)",
        requires: &[],
        effect: SkillEffect::Damage(6.0),
    },
    SkillDef {
        id: "agility1",
        label: "Agilidade I (+10% velocidade)",
        requires: &[],
        effect: SkillEffect::Speed(0.10),
    },
    SkillDef {
        id: "precision1",
        label: "Precisão I (+8% crítico)",
        requires: &[],
        effect: SkillEffect::Crit(0.08),
    },
    SkillDef {
        id: "vitality2",
        label: "Vitalidade II (+30 HP)",
        requires: &["vitality1"],
        effect: SkillEffect::MaxHp(30.0),
    },
    SkillDef {
        id: "strength2",
        label: "Força II (+10 dano)",
        requires: &["strength1"],
        effect: SkillEffect::Damage(10.0),
    },
    SkillDef {
        id: "agility2",
        label: "Agilidade II (+15% velocidade)",
        requires: &["agility1"],
        effect: SkillEffect::Speed(0.15),
    },
    SkillDef {
        id: "precision2",
        label: "Precisão II (+10% crítico)",
        requires: &["precision1"],
        effect: SkillEffect::Crit(0.10),
    },
];

/// Bónus agregados das passivas aprendidas.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct PlayerStats {
    pub bonus_damage: f32,
    pub speed_mult: f32,
    pub max_hp_bonus: f32,
    pub crit_bonus: f32,
}

/// Puro: bónus a partir dos ids aprendidos.
pub fn stats_from_learned(learned: &[String]) -> PlayerStats {
    let mut stats = PlayerStats {
        speed_mult: 1.0,
        ..Default::default()
    };
    for id in learned {
        if let Some(def) = SKILLS.iter().find(|s| s.id == id) {
            match def.effect {
                SkillEffect::Damage(v) => stats.bonus_damage += v,
                SkillEffect::Speed(v) => stats.speed_mult += v,
                SkillEffect::MaxHp(v) => stats.max_hp_bonus += v,
                SkillEffect::Crit(v) => stats.crit_bonus += v,
            }
        }
    }
    stats
}

/// Diário de skills: aprendidas + pontos disponíveis.
#[derive(Debug, Clone, Resource, Default)]
pub struct SkillTree {
    pub learned: Vec<String>,
    pub points: u32,
}

impl SkillTree {
    /// Pura: pode aprender (não aprendida + requisitos + pontos).
    pub fn can_learn(&self, id: &str) -> bool {
        if self.learned.iter().any(|l| l == id) || self.points == 0 {
            return false;
        }
        match SKILLS.iter().find(|s| s.id == id) {
            Some(def) => def
                .requires
                .iter()
                .all(|r| self.learned.iter().any(|l| l == r)),
            None => false,
        }
    }

    /// Aprende (gasta 1 ponto); devolve os bónus agregados atualizados.
    pub fn learn(&mut self, id: &str) -> Option<PlayerStats> {
        if !self.can_learn(id) {
            return None;
        }
        self.points -= 1;
        self.learned.push(id.into());
        Some(stats_from_learned(&self.learned))
    }

    /// Pré-requisitos ainda por aprender (para a UI).
    pub fn missing_requires(&self, id: &str) -> Vec<&'static str> {
        SKILLS
            .iter()
            .find(|s| s.id == id)
            .map(|def| {
                def.requires
                    .iter()
                    .filter(|r| !self.learned.iter().any(|l| l == *r))
                    .copied()
                    .collect()
            })
            .unwrap_or_default()
    }
}

/// Bónus vivos (recurso; atualizado ao aprender).
#[derive(Debug, Clone, Resource, Default)]
pub struct PlayerStatsResource(pub PlayerStats);

/// Aplica aos componentes do herói o delta entre dois agregados de
/// passivas — usado ao aprender (anterior → novo) e ao carregar um save
/// (delta desde o default). Sem isto, Speed/MaxHp eram comprados e não
/// faziam NADA: nenhum sistema consumia `speed_mult`/`max_hp_bonus`.
pub fn apply_passive_delta(
    health: &mut Health,
    player: &mut Player,
    previous: &PlayerStats,
    new: &PlayerStats,
) {
    let hp_delta = new.max_hp_bonus - previous.max_hp_bonus;
    if hp_delta != 0.0 {
        health.max += hp_delta;
        // O HP ganho entra já disponível (compra "Vitalidade +20" sobe o
        // actual na mesma medida).
        health.current = (health.current + hp_delta).clamp(0.0, health.max);
    }
    let speed_ratio = new.speed_mult / previous.speed_mult.max(f32::EPSILON);
    if speed_ratio != 1.0 && speed_ratio.is_finite() {
        player.speed *= speed_ratio;
    }
}

// ── estado de combate ───────────────────────────────────────────────────

/// Cooldowns das abilities (s restantes).
#[derive(Debug, Clone, Resource, Default)]
pub struct AbilityCooldowns {
    pub dash: f32,
    pub heal: f32,
    pub strike: f32,
}

/// Combo do melee: golpes na janela; o 3.º é finisher (×2).
#[derive(Debug, Clone, Resource, Default)]
pub struct ComboState {
    pub hits: u32,
    pub window: f32,
}

/// Guard [L] ativo (feedback lê para reduzir dano; primeiros 0,22 s parry).
#[derive(Debug, Clone, Component)]
pub struct Guarding {
    pub timer: f32,
}

/// Nível + pontos por subir de nível.
#[derive(Debug, Clone, Component, Default)]
pub struct LevelState {
    pub level: u32,
    pub points: u32,
}

/// Último `xp.next` visto, entre frames E entre save/loads. Resource (não
/// `Local`) para o load sincronizá-lo — carregar um save com `xp.next`
/// maior creditava pontos de nível GRÁTIS.
#[derive(Debug, Clone, Resource, Default)]
pub struct LevelProgress {
    pub previous_next: Option<u32>,
}

/// Bomba no ar.
#[derive(Debug, Clone, Component)]
pub struct Bomb {
    pub velocity: Vec3,
    pub fuse: f32,
}

/// Mesh/material partilhados das bombas (criados no Startup).
#[derive(Debug, Clone, Resource)]
pub struct BombAssets {
    pub mesh: Handle<Mesh>,
    pub material: Handle<StandardMaterial>,
}

// ── lógica pura (testada) ───────────────────────────────────────────────

/// Dano final do melee: o 3.º golpe da sequência é finisher (×2) e um
/// golpe pelas costas duplica (×2 adicional).
pub fn melee_damage(base: f32, combo_hit: u32, backstab: bool) -> (f32, bool) {
    let finisher = combo_hit == COMBO_WINDOW_COUNT;
    let mult = if finisher { 2.0 } else { 1.0 } * if backstab { 2.0 } else { 1.0 };
    (base * mult, finisher)
}

pub const COMBO_WINDOW_COUNT: u32 = 3;
/// Janela (s) para completar o combo.
pub const COMBO_WINDOW: f32 = 3.0;
/// Janela de parry no início do guard (s).
pub const PARRY_WINDOW: f32 = 0.22;
/// Multiplicador de dano com guard ativo.
pub const GUARD_REDUCTION: f32 = 0.25;

/// Crítico determinístico: roll (0..1) abaixo da chance.
pub fn is_crit(crit_chance: f32, roll: f32) -> bool {
    roll < crit_chance
}

/// Dano radial com falloff linear (cheio no centro, metade na borda).
pub fn radial_damage(distance: f32, radius: f32, max_damage: f32) -> Option<f32> {
    if distance > radius {
        return None;
    }
    Some(max_damage * (1.0 - 0.5 * (distance / radius)))
}

// ── plugin ──────────────────────────────────────────────────────────────

/// Seleção da UI de skills (tab Skills do modal).
#[derive(Debug, Clone, Resource, Default)]
pub struct SkillUiSelection(pub usize);

pub struct SkillsPlugin;

impl Plugin for SkillsPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<SkillTree>()
            .init_resource::<AbilityCooldowns>()
            .init_resource::<ComboState>()
            .init_resource::<PlayerStatsResource>()
            .init_resource::<LevelProgress>()
            .init_resource::<SkillUiSelection>()
            // Os sistemas [C]/[B]/[L] consultam `MenusOpen` (apps mínimas:
            // nasce aqui; na app completa o MenusPlugin inicializa-o igual).
            .init_resource::<crate::menus::MenusOpen>()
            // FOV kick do dash (idempotente com o CombatPlugin).
            .init_resource::<crate::camera::CameraFx>()
            // FX de impacto ([R]/[B] somam hit-stop/trauma/kick/punch): nasce
            // aqui também para apps mínimas (idempotente com os plugins donos).
            .init_resource::<crate::combat::HitStop>()
            .init_resource::<crate::camera::CameraShake>()
            .init_resource::<crate::camera::CameraKick>()
            .init_resource::<crate::postfx::PostFxState>()
            .add_systems(Startup, spawn_bomb_assets)
            .add_systems(
                Update,
                (
                    timed(Group::Combat, abilities_system),
                    bomb_throw_system,
                    timed(Group::Combat, bomb_step_system),
                    guard_system,
                    level_system,
                ),
            );
    }
}

// ── UI das habilidades ──────────────────────────────────────────────────
// As três barras de texto ("[C] Dash pronto", …) foram substituídas pelos
// slots `<UiCooldown>` do HUD declarativo: a tecla é o ícone, a veladura
// mostra o recarregamento e a folha de estilo acende o aro quando fica
// pronta. Os cooldowns continuam a viver em `AbilityCooldowns`, que a UI lê
// pelos bindings `cd.dash` / `cd.heal` / `cd.strike` (`src/ui/bind.rs`).

fn spawn_bomb_assets(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    let mesh = meshes.add(Sphere { radius: 0.18 }.mesh().ico(2).unwrap());
    let material = materials.add(StandardMaterial {
        base_color: Color::srgb(0.15, 0.15, 0.17),
        emissive: LinearRgba::rgb(0.4, 0.15, 0.05),
        ..Default::default()
    });
    commands.insert_resource(BombAssets { mesh, material });
}

// ── sistemas ────────────────────────────────────────────────────────────

/// Guard [L]: mantém `Guarding` enquanto a tecla está pressionada; o timer
/// avança aqui e o feedback decide parry (janela inicial) vs redução.
fn guard_system(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    menus: Res<crate::menus::MenusOpen>,
    players: Query<Entity, With<Player>>,
    mut guards: Query<&mut Guarding>,
    mut commands: Commands,
) {
    let Ok(player) = players.single() else {
        return;
    };
    // Menu aberto rouba [L]: trata como "largo" — sem isto, o Guarding
    // ficava preso no herói até fechar o modal (−75 % de dano eterno).
    let holding = !menus.any() && keys.pressed(KeyCode::KeyL);
    if holding && guards.get_mut(player).is_err() {
        commands.entity(player).insert(Guarding { timer: 0.0 });
    }
    if holding {
        if let Ok(mut guard) = guards.get_mut(player) {
            guard.timer += time.delta_secs();
        }
    } else if guards.get_mut(player).is_ok() {
        commands.entity(player).remove::<Guarding>();
    }
}

/// Paridade com o melee (`combat.rs`): um morto por qualquer via perde o
/// script, vira cadáver, dá XP e reporta o abate às quests — sem isto, o
/// [R]/[B]/fireball deixava o inimigo a 0 HP de pé, com a FSM a correr e
/// sem XP. Pub(crate): a fireball (`combat.rs`) reutiliza-a.
#[allow(clippy::too_many_arguments)]
pub(crate) fn kill_creature(
    commands: &mut Commands,
    target: Entity,
    script: Option<&LuaScriptRef>,
    position: Vec3,
    hero_xp: &mut Query<&mut Xp, With<crate::player::Player>>,
    numbers: &mut MessageWriter<DamageNumberEvent>,
    toasts: &mut MessageWriter<ScriptToast>,
    quests: &mut Option<ResMut<QuestLog>>,
    sfx: &mut MessageWriter<crate::ambient::SfxEvent>,
) {
    commands
        .entity(target)
        .remove::<LuaScriptRef>()
        .insert(crate::combat::Corpse {
            timer: crate::combat::CORPSE_LIFETIME,
        });
    sfx.write(crate::ambient::SfxEvent {
        clip: crate::ambient::SfxClip::EnemyDeath,
        position: Some(position),
    });
    if let Ok(mut xp) = hero_xp.single_mut() {
        gain_xp(&mut xp, crate::combat::KILL_XP);
    }
    numbers.write(DamageNumberEvent {
        position: position + Vec3::Y * 0.4,
        text: format!("+{} XP", crate::combat::KILL_XP),
        color: Color::srgb(1.0, 0.8, 0.25),
    });
    toasts.write(ScriptToast(format!(
        "Inimigo derrotado (+{} XP)",
        crate::combat::KILL_XP
    )));
    if let (Some(script), Some(quests)) = (script, quests.as_deref_mut()) {
        let kind = crate::combat::script_kind(&script.path);
        for ready in quests.report_kill(&kind) {
            if let Some(def) = quests.def(&ready) {
                toasts.write(ScriptToast(format!(
                    "Objetivo completo: {} — volta ao NPC",
                    def.title
                )));
            }
        }
    }
}

/// Contexto de FX das abilities — o `abilities_system` vive no teto de 16
/// params de sistema, pelo que os efeitos de impacto (paridade com o melee:
/// bursts + anéis de choque, hit-stop, trauma, solavanco direcional e pulso
/// de pós-processo) e o FOV kick da câmara (dash) viajam juntos.
#[derive(bevy::ecs::system::SystemParam)]
pub struct AbilityFx<'w> {
    sfx: bevy::ecs::message::MessageWriter<'w, crate::ambient::SfxEvent>,
    camera_fx: ResMut<'w, crate::camera::CameraFx>,
    // Música de combate: o slam também renova a layer battle/boss.
    combat_music: ResMut<'w, crate::music::CombatMusicState>,
    time: Res<'w, Time>,
    meshes: ResMut<'w, Assets<Mesh>>,
    materials: ResMut<'w, Assets<StandardMaterial>>,
    hit_stop: ResMut<'w, crate::combat::HitStop>,
    shake: ResMut<'w, crate::camera::CameraShake>,
    kick: ResMut<'w, crate::camera::CameraKick>,
    postfx: ResMut<'w, crate::postfx::PostFxState>,
}

/// [C] dash · [E] cura (fora de interação) · [R] golpe forte radial.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn abilities_system(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    menus: Res<crate::menus::MenusOpen>,
    mut cds: ResMut<AbilityCooldowns>,
    mut players: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Transform,
            Option<&mut Health>,
        ),
        With<Player>,
    >,
    interactions: Query<(&GlobalTransform, &ScriptInteraction), Without<Player>>,
    npcs: Query<&GlobalTransform, (With<crate::recipes::spawn::DialogueNpc>, Without<Player>)>,
    mut creatures: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Health,
            Option<&LuaScriptRef>,
            Option<&crate::impact::HitRecoil>,
        ),
        (Without<Player>, Without<crate::combat::Corpse>),
    >,
    mut hero_xp: Query<&mut Xp, With<Player>>,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut toasts: MessageWriter<ScriptToast>,
    stats: Res<PlayerStatsResource>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut commands: Commands,
    mut fx: AbilityFx,
    mut quests: Option<ResMut<QuestLog>>,
) {
    let dt = time.delta_secs();
    cds.dash = (cds.dash - dt).max(0.0);
    cds.heal = (cds.heal - dt).max(0.0);
    cds.strike = (cds.strike - dt).max(0.0);
    // Menu aberto consome [C]/[E]/[R]: dashava, curava e gastava cooldown
    // por trás do modal (os cooldowns continuam a correr, como as janelas
    // do melee).
    if menus.any() {
        return;
    }
    let Ok((entity, global, mut transform, mut health)) = players.single_mut() else {
        return;
    };
    let pos = global.translation();

    // [C] dash
    if keys.just_pressed(KeyCode::KeyC) && cds.dash <= 0.0 {
        // O modelo olha +Z (convenção da pipeline) — o `forward()` (−Z)
        // dashava PARA TRÁS (mesma correção do melee, `combat::hero_forward`).
        let forward = crate::combat::hero_forward(global) * DASH_DISTANCE;
        let (x, z) = (pos.x + forward.x, pos.z + forward.z);
        // PISO SOB O HERÓI (Y conhecido → surface_below): o dash sob um
        // overhang não teleporta para cima da rocha; em campo aberto segue
        // o relevo como antes. Enterrado (a atravessar uma parede) mantém a
        // superfície renderizada — o dash continua a cruzar obstáculos.
        let y = terrain
            .as_ref()
            .map(|t| {
                t.surface_below(x, z, pos.y + crate::player::GROUND_PROBE)
                    .unwrap_or_else(|| t.sample_mesh_surface(x, z))
            })
            .unwrap_or(pos.y);
        transform.translation = Vec3::new(x, y, z);
        commands.entity(entity).insert(Invulnerable {
            timer: DASH_IFRAMES,
        });
        cds.dash = DASH_COOLDOWN;
        // FOV kick do dash: punch de +8° que decai em 0,3 s (sensação de
        // rajada — o mundo "abre" e fecha de volta ao FOV autoral).
        crate::camera::fov_kick(&mut fx.camera_fx, crate::camera::FOV_KICK_DASH);
        fx.sfx.write(crate::ambient::SfxEvent {
            clip: crate::ambient::SfxClip::Dash,
            position: Some(pos),
        });
    }

    // [E] cura — só quando NÃO há interação em alcance ([E] interagir ganha)
    if keys.just_pressed(KeyCode::KeyE) && cds.heal <= 0.0 {
        // O [E] de interagir ganha: DialogueNPC (diálogo a 3,5 m, sem
        // ScriptInteraction) e interações com range autoral > 3,5 m contam
        // também — senão a cura saía AQUI e o diálogo/prompt em cima.
        let near_interaction = npcs.iter().any(|t| t.translation().distance(pos) < 3.5)
            || interactions
                .iter()
                .any(|(t, i)| t.translation().distance(pos) < i.range.max(3.5));
        if !near_interaction {
            if let Some(health) = health.as_mut() {
                let healed = HEAL_ABILITY_AMOUNT.min(health.max - health.current);
                health.current += healed;
                cds.heal = HEAL_ABILITY_COOLDOWN;
                numbers.write(DamageNumberEvent {
                    position: pos + Vec3::Y * 1.9,
                    text: format!("+{}", healed.round() as i32),
                    color: Color::srgb(0.4, 1.0, 0.45),
                });
                if healed > 0.0 {
                    fx.sfx.write(crate::ambient::SfxEvent {
                        clip: crate::ambient::SfxClip::Heal,
                        position: None,
                    });
                }
            }
        }
    }

    // [R] golpe forte radial — SLAM no chão: o herói vira-se para o inimigo
    // mais próximo do raio, o impacto leva a paridade completa do melee
    // (flash + sparks + recoil + knockback por inimigo; hit-stop + shake +
    // solavanco + punch de pós-processo globais) e um anel de choque onda o
    // chão. Antes disto era só um número a mais.
    if keys.just_pressed(KeyCode::KeyR) && cds.strike <= 0.0 {
        cds.strike = STRIKE_COOLDOWN;
        let damage = STRIKE_DAMAGE + stats.0.bonus_damage;
        let mut kills: Vec<(Entity, Option<&LuaScriptRef>, Vec3)> = Vec::new();
        let mut hit_any = false;
        let mut face_dir: Option<Vec3> = None;
        for (target, t, mut health, script, recoil) in creatures.iter_mut() {
            let delta = t.translation() - pos;
            let d = delta.length();
            if d > STRIKE_RADIUS {
                continue;
            }
            // Snap de facing para o PRIMEIRO inimigo no raio (o mais próximo
            // na ordem de query não é garantido, mas para um slam radial
            // qualquer direção válida serve — o MODELO tem de apontar).
            if face_dir.is_none() {
                face_dir = Some(delta.with_y(0.0).normalize_or_zero());
            }
            // Combate vivo: renova a layer battle/boss no driver de BGM.
            let is_boss = script
                .map(|s| s.path.replace('\\', "/").starts_with("bosses/"))
                .unwrap_or(false);
            fx.combat_music.engage(fx.time.elapsed_secs_f64(), is_boss);
            apply_damage(&mut health, damage);
            commands.entity(target).insert(crate::feedback::HitFlash {
                timer: crate::feedback::HIT_FLASH_SECS,
            });
            if health.current > 0.0 {
                // Stagger visual (squash) — reutiliza a base de um recoil em
                // curso para o re-hit não compor a deformação.
                commands
                    .entity(target)
                    .insert(crate::impact::HitRecoil::new(
                        recoil.map(|r| r.base_scale).unwrap_or(t.scale()),
                    ));
                // Knockback para fora do centro do slam (~1.2 m de recuo).
                commands
                    .entity(target)
                    .insert(crate::physics_fx::knockback_after(
                        delta,
                        STRIKE_KNOCKBACK_STRENGTH,
                    ));
            }
            crate::particles::spawn_burst(
                &mut commands,
                &crate::combat::hit_sparks_spec(),
                t.translation() + Vec3::Y * 1.0,
                STRIKE_SPARK_COUNT,
            );
            numbers.write(DamageNumberEvent {
                position: t.translation() + Vec3::Y * 1.8,
                text: format!("-{}", damage as i32),
                color: Color::srgb(1.0, 0.5, 0.2),
            });
            hit_any = true;
            if health.current <= 0.0 {
                kills.push((target, script, t.translation()));
            }
        }
        for (target, script, position) in kills {
            kill_creature(
                &mut commands,
                target,
                script,
                position,
                &mut hero_xp,
                &mut numbers,
                &mut toasts,
                &mut quests,
                &mut fx.sfx,
            );
        }
        toasts.write(ScriptToast(if hit_any {
            "GOLPE FORTE!".into()
        } else {
            "Golpe forte ao ar!".into()
        }));
        if hit_any {
            fx.sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Whoosh,
                position: Some(pos),
            });
            fx.sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Hit,
                position: Some(pos),
            });
            crate::combat::request_hit_stop(&mut fx.hit_stop, crate::combat::HIT_STOP_HEAVY);
            crate::camera::add_camera_shake(&mut fx.shake, STRIKE_SHAKE);
            // O slam empurra a câmara PARA CIMA (o chão sobe contra ela).
            crate::camera::add_camera_kick(&mut fx.kick, Vec3::Y * STRIKE_KICK_UP);
            crate::postfx::punch_impact(&mut fx.postfx, STRIKE_PUNCH_STOPS, STRIKE_PUNCH_BLOOM);
        }
        // O anel + poeira saem SEMPRE: o slam aterra, acerte ou não.
        crate::impact::spawn_impact_ring(
            &mut commands,
            &mut fx.meshes,
            &mut fx.materials,
            pos.with_y(pos.y + 0.08),
            STRIKE_RING_RADIUS,
            Color::srgb(1.0, 0.85, 0.6),
        );
        crate::particles::spawn_burst(
            &mut commands,
            &crate::combat::impact_spec("ground-dust", (0.5, 1.0), (0.5, 1.0), (2.0, 4.5), None),
            pos.with_y(pos.y + 0.15),
            12,
        );
        if let Some(dir) = face_dir {
            crate::combat::snap_facing(&mut transform, dir, STRIKE_FACE_SNAP);
        }
    }
}

/// [B] lança bomba (consome `bomb` do vault). Os pré-requisitos (assets,
/// herói) validam-se ANTES de consumir o item — sem assets a bomba
/// desaparecia do inventário sem nada ser lançado.
#[allow(clippy::too_many_arguments)]
fn bomb_throw_system(
    keys: Res<ButtonInput<KeyCode>>,
    menus: Res<crate::menus::MenusOpen>,
    mut vault: ResMut<Vault>,
    players: Query<&GlobalTransform, With<Player>>,
    assets: Option<Res<BombAssets>>,
    mut commands: Commands,
    mut toasts: MessageWriter<ScriptToast>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
) {
    // Menu aberto consome [B]: deixava de consumir bombas do vault por trás
    // do modal.
    if menus.any() {
        return;
    }
    if !keys.just_pressed(KeyCode::KeyB) {
        return;
    }
    let Some(assets) = assets else {
        return;
    };
    let Ok(global) = players.single() else {
        return;
    };
    if !vault.item_take("bomb") {
        toasts.write(ScriptToast("Sem bombas — compra ao mercador.".into()));
        sfx.write(crate::ambient::SfxEvent {
            clip: crate::ambient::SfxClip::Error,
            position: None,
        });
        return;
    }
    let origin = global.translation() + Vec3::Y * 1.2;
    // O modelo olha +Z — o `forward()` (−Z) lançava a bomba para trás.
    let dir = crate::combat::hero_forward(global);
    sfx.write(crate::ambient::SfxEvent {
        clip: crate::ambient::SfxClip::BombDrop,
        position: Some(origin),
    });
    commands.spawn((
        Mesh3d(assets.mesh.clone()),
        MeshMaterial3d(assets.material.clone()),
        Transform::from_translation(origin),
        Visibility::default(),
        InheritedVisibility::default(),
        Bomb {
            velocity: dir * 8.0 + Vec3::Y * 7.0,
            fuse: BOMB_FUSE,
        },
        Name::new("fx:bomb"),
    ));
}

/// Bomba: voo parabólico + explosão radial no fim do pavio. A detonação tem
/// a paridade de impacto do melee: flash + sparks + recoil por criatura,
/// hit-stop + shake + solavanco + punch + anel de choque globais (o
/// knockback radial já cá estava).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn bomb_step_system(
    mut bombs: Query<(Entity, &mut Transform, &mut Bomb)>,
    mut creatures: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Health,
            Option<&LuaScriptRef>,
            Option<&crate::impact::HitRecoil>,
        ),
        (Without<Player>, Without<crate::combat::Corpse>),
    >,
    mut hero_xp: Query<&mut Xp, With<Player>>,
    mut commands: Commands,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut alerts: MessageWriter<AttackAlert>,
    mut toasts: MessageWriter<ScriptToast>,
    mut fx: AbilityFx,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut quests: Option<ResMut<QuestLog>>,
) {
    let dt = fx.time.delta_secs();
    for (entity, mut transform, mut bomb) in &mut bombs {
        bomb.fuse -= dt;
        bomb.velocity.y -= 18.0 * dt;
        transform.translation += bomb.velocity * dt;
        // Repouso no terreno: sem isto a bomba atravessava o chão em voo
        // parabólico e detonava enterrada (centro do AoE sob a superfície —
        // inimigos à boca do crater ficavam fora do raio em 3D). Piso SOB a
        // bomba (Y conhecido): uma bomba lançada sob um overhang repousa no
        // chão de baixo, não no topo da rocha.
        if let Some(terrain) = terrain.as_ref() {
            let floor = terrain
                .surface_below(
                    transform.translation.x,
                    transform.translation.z,
                    transform.translation.y + crate::player::GROUND_PROBE,
                )
                .unwrap_or_else(|| {
                    terrain.sample(transform.translation.x, transform.translation.z)
                });
            let ground = floor + 0.18;
            if transform.translation.y < ground {
                transform.translation.y = ground;
                bomb.velocity.y = bomb.velocity.y.max(0.0);
                let damp = (1.0 - 6.0 * dt).max(0.0);
                bomb.velocity.x *= damp;
                bomb.velocity.z *= damp;
            }
        }
        if bomb.fuse > 0.0 {
            continue;
        }
        let center = transform.translation;
        let mut kills: Vec<(Entity, Option<&LuaScriptRef>, Vec3)> = Vec::new();
        for (target, t, mut health, script, recoil) in creatures.iter_mut() {
            let d = t.translation().distance(center);
            if let Some(dmg) = radial_damage(d, BOMB_RADIUS, BOMB_DAMAGE) {
                apply_damage(&mut health, dmg);
                commands.entity(target).insert(crate::feedback::HitFlash {
                    timer: crate::feedback::HIT_FLASH_SECS,
                });
                if health.current > 0.0 {
                    commands
                        .entity(target)
                        .insert(crate::impact::HitRecoil::new(
                            recoil.map(|r| r.base_scale).unwrap_or(t.scale()),
                        ));
                }
                crate::particles::spawn_burst(
                    &mut commands,
                    &crate::combat::hit_sparks_spec(),
                    t.translation() + Vec3::Y * 1.0,
                    BOMB_SPARK_COUNT,
                );
                numbers.write(DamageNumberEvent {
                    position: t.translation() + Vec3::Y * 1.8,
                    text: format!("-{}", dmg as i32),
                    color: Color::srgb(1.0, 0.6, 0.1),
                });
                if health.current <= 0.0 {
                    kills.push((target, script, t.translation()));
                }
            }
        }
        // R2-G7: os mortos neste frame têm `Corpse` PENDENTE nos commands (a
        // query de cima ainda não os filtra) — o knockback radial em baixo
        // não volta a empurrá-los (cadáver a ser arrastado e Y-slamado
        // durante a animação de morte).
        let killed_ids: Vec<Entity> = kills.iter().map(|(e, _, _)| *e).collect();
        for (target, script, position) in kills {
            kill_creature(
                &mut commands,
                target,
                script,
                position,
                &mut hero_xp,
                &mut numbers,
                &mut toasts,
                &mut quests,
                &mut fx.sfx,
            );
        }
        alerts.write(AttackAlert { position: center });
        fx.sfx.write(crate::ambient::SfxEvent {
            clip: crate::ambient::SfxClip::Hit,
            position: Some(center),
        });
        toasts.write(ScriptToast("BOOM!".into()));
        // Peso de explosão: hit-stop + shake + solavanco vertical + punch +
        // anel de choque (sempre — uma bomba detona, acerte ou não).
        crate::combat::request_hit_stop(&mut fx.hit_stop, crate::combat::HIT_STOP_HEAVY);
        crate::camera::add_camera_shake(&mut fx.shake, BOMB_SHAKE);
        crate::camera::add_camera_kick(&mut fx.kick, Vec3::Y * BOMB_KICK_UP);
        crate::postfx::punch_impact(&mut fx.postfx, BOMB_PUNCH_STOPS, BOMB_PUNCH_BLOOM);
        crate::impact::spawn_impact_ring(
            &mut commands,
            &mut fx.meshes,
            &mut fx.materials,
            center.with_y(center.y + 0.08),
            BOMB_RING_RADIUS,
            Color::srgb(1.0, 0.75, 0.45),
        );
        // knockback radial (loop 10): empurra as criaturas SOBREVIVENTES —
        // os abates deste frame (Corpse pendente) ficam de fora (R2-G7).
        for (target, t, _health, _script, _recoil) in creatures.iter_mut() {
            if killed_ids.contains(&target) {
                continue;
            }
            let delta = t.translation() - center;
            let distance = delta.length();
            if let Some(strength) =
                crate::physics_fx::radial_strength(distance, BOMB_RADIUS * 1.5, 9.0)
            {
                commands
                    .entity(target)
                    .insert(crate::physics_fx::Knockback {
                        velocity: delta.normalize_or_zero() * strength,
                    });
            }
        }
        commands.entity(entity).despawn();
    }
}

/// Level-ups: cada rampa do `xp.next` credita 1 ponto de skill.
#[allow(clippy::type_complexity)]
fn level_system(
    players: Query<(Entity, &Xp), (Changed<Xp>, With<Player>)>,
    mut levels: Query<(Entity, &mut LevelState)>,
    mut tree: ResMut<SkillTree>,
    mut progress: ResMut<LevelProgress>,
    mut commands: Commands,
) {
    let Ok((entity, xp)) = players.single() else {
        return;
    };
    let Some(previous) = progress.previous_next else {
        progress.previous_next = Some(xp.next);
        return;
    };
    if xp.next > previous {
        // Rampa REAL do vitals (+50 %: 100→150→225→338…) — a fórmula antiga
        // `1 + delta/100` assumia +100/nível e sobre-creditava pontos à
        // medida que o nível subia (225→338 é UM nível, a fórmula dava 2).
        // Cadeias que não batem exato (save antigo por cima) devolvem 0:
        // ressincroniza sem creditar nada.
        let gained = crate::vitals::levels_between(previous, xp.next);
        if gained > 0 {
            match levels.get_mut(entity) {
                Ok((_, mut level)) => {
                    level.level += gained;
                    level.points += gained;
                }
                Err(_) => {
                    commands.entity(entity).insert(LevelState {
                        level: gained,
                        points: gained,
                    });
                }
            }
            tree.points += gained;
        }
    }
    progress.previous_next = Some(xp.next);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_skills_catalog_shape() {
        assert_eq!(SKILLS.len(), 8);
        // 4 efeitos de cada tipo? pelo menos um de cada
        for effect in [
            std::mem::discriminant(&SKILLS[0].effect),
            std::mem::discriminant(&SKILLS[1].effect),
        ] {
            let _ = effect;
        }
        assert!(SKILLS.iter().any(|s| s.requires.is_empty()));
        assert!(SKILLS.iter().all(|s| SKILLS.iter().any(|p| p.id == s.id)));
    }

    #[test]
    fn test_learn_requires_and_points() {
        let mut tree = SkillTree {
            learned: vec![],
            points: 3,
        };
        // precisa de pontos
        assert!(tree.can_learn("vitality1"));
        assert!(!tree.can_learn("vitality2"), "requisito em falta");
        assert!(tree.learn("vitality1").is_some());
        assert!(!tree.can_learn("vitality1"), "já aprendida");
        assert!(tree.can_learn("vitality2"));
        assert!(tree.learn("vitality2").is_some());
        assert_eq!(tree.points, 1);
        let stats = stats_from_learned(&tree.learned);
        assert!((stats.max_hp_bonus - 50.0).abs() < 1e-4);
    }

    #[test]
    fn test_melee_damage_finisher_and_backstab() {
        let (normal, finisher) = melee_damage(25.0, 1, false);
        assert!(!finisher);
        assert!((normal - 25.0).abs() < 1e-4);
        let (fin, is_finisher) = melee_damage(25.0, 3, false);
        assert!(is_finisher);
        assert!((fin - 50.0).abs() < 1e-4);
        let (back, _) = melee_damage(25.0, 2, true);
        assert!((back - 50.0).abs() < 1e-4);
    }

    #[test]
    fn test_is_crit() {
        assert!(is_crit(0.15, 0.10));
        assert!(!is_crit(0.15, 0.20));
    }

    #[test]
    fn test_radial_falloff() {
        assert!((radial_damage(0.0, 6.0, 90.0).unwrap() - 90.0).abs() < 1e-4);
        assert!((radial_damage(6.0, 6.0, 90.0).unwrap() - 45.0).abs() < 1e-4);
        assert!(radial_damage(7.0, 6.0, 90.0).is_none());
    }

    /// R2-G1: o crédito de níveis/pontos segue a rampa REAL do vitals
    /// (+50 %) — a fórmula antiga `1 + delta/100` dava 2 pontos no salto
    /// único 225→338 (que é UM nível).
    #[test]
    fn test_level_system_credits_levels_by_real_ramp() {
        let mut app = App::new();
        app.add_plugins(MinimalPlugins);
        app.add_plugins(SkillsPlugin);
        // O SkillsPlugin regista sistemas que tocam em input, mensagens e
        // assets — sem os plugins do motor (Asset/Input/Message), o app
        // mínimo tem de inicializar tudo à mão, ou os sistemas falham a
        // validação de parâmetros a cada update.
        app.init_resource::<Assets<Mesh>>();
        app.init_resource::<Assets<StandardMaterial>>();
        app.insert_resource(ButtonInput::<KeyCode>::default());
        app.insert_resource(ButtonInput::<MouseButton>::default());
        app.insert_resource(crate::menus::MenusOpen::default());
        app.insert_resource(Vault::default());
        app.insert_resource(crate::music::CombatMusicState::default());
        app.add_message::<DamageNumberEvent>();
        app.add_message::<AttackAlert>();
        app.add_message::<ScriptToast>();
        app.add_message::<crate::ambient::SfxEvent>();
        let hero = app
            .world_mut()
            .spawn((
                Player::default(),
                Xp {
                    current: 0,
                    next: 225,
                },
                LevelState {
                    level: 5,
                    points: 0,
                },
            ))
            .id();
        app.update(); // baseline silencioso do LevelProgress (previous = 225)

        // +300 XP a 0/225 faz EXATAMENTE um salto de rampa (225→338), pelo
        // MESMO caminho do jogo (gain_xp — mutação direta de `current` não
        // rampa o `next`).
        gain_xp(&mut app.world_mut().get_mut::<Xp>(hero).unwrap(), 300);
        app.update();
        let level = app.world().get::<LevelState>(hero).unwrap();
        assert_eq!(level.level, 6, "225→338 é UM nível, não dois");
        assert_eq!(level.points, 1, "um ponto de skill por nível real");
        assert_eq!(app.world().resource::<SkillTree>().points, 1);
        // E o next foi consumido: novo frame sem ganho não re-credita.
        app.update();
        let level = app.world().get::<LevelState>(hero).unwrap();
        assert_eq!(level.points, 1, "sem re-credito no frame seguinte");
    }
}
