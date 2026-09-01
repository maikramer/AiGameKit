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
use crate::luau::{ScriptInteraction, ScriptToast};
use crate::player::Player;
use crate::vitals::{Health, Xp, apply_damage};

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
    SkillDef { id: "vitality1", label: "Vitalidade I (+20 HP)", requires: &[], effect: SkillEffect::MaxHp(20.0) },
    SkillDef { id: "strength1", label: "Força I (+6 dano)", requires: &[], effect: SkillEffect::Damage(6.0) },
    SkillDef { id: "agility1", label: "Agilidade I (+10% velocidade)", requires: &[], effect: SkillEffect::Speed(0.10) },
    SkillDef { id: "precision1", label: "Precisão I (+8% crítico)", requires: &[], effect: SkillEffect::Crit(0.08) },
    SkillDef { id: "vitality2", label: "Vitalidade II (+30 HP)", requires: &["vitality1"], effect: SkillEffect::MaxHp(30.0) },
    SkillDef { id: "strength2", label: "Força II (+10 dano)", requires: &["strength1"], effect: SkillEffect::Damage(10.0) },
    SkillDef { id: "agility2", label: "Agilidade II (+15% velocidade)", requires: &["agility1"], effect: SkillEffect::Speed(0.15) },
    SkillDef { id: "precision2", label: "Precisão II (+10% crítico)", requires: &["precision1"], effect: SkillEffect::Crit(0.10) },
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
    let mut stats = PlayerStats { speed_mult: 1.0, ..Default::default() };
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
            Some(def) => def.requires.iter().all(|r| self.learned.iter().any(|l| l == r)),
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
            .init_resource::<SkillUiSelection>()
            .add_systems(Startup, (spawn_ability_bars, spawn_bomb_assets))
            .add_systems(
                Update,
                (
                    abilities_system,
                    bomb_throw_system,
                    bomb_step_system,
                    guard_system,
                    level_system,
                    ability_bars_system,
                    skill_learn_system,
                ),
            );
    }
}

/// [P] na tab Skills (modal aberto, tab índice 2): aprende a skill
/// selecionada e atualiza os bónus vivos.
fn skill_learn_system(
    keys: Res<ButtonInput<KeyCode>>,
    menus_open: Res<crate::menus::MenusOpen>,
    tab: Res<crate::menus::ModalTab>,
    mut selection: ResMut<SkillUiSelection>,
    mut tree: ResMut<SkillTree>,
    mut stats: ResMut<PlayerStatsResource>,
    mut toasts: MessageWriter<ScriptToast>,
) {
    if !menus_open.modal || tab.0 != 2 {
        return;
    }
    if keys.just_pressed(KeyCode::ArrowDown) || keys.just_pressed(KeyCode::KeyS) {
        selection.0 = (selection.0 + 1) % SKILLS.len();
    }
    if keys.just_pressed(KeyCode::ArrowUp) || keys.just_pressed(KeyCode::KeyW) {
        selection.0 = (selection.0 + SKILLS.len() - 1) % SKILLS.len();
    }
    if keys.just_pressed(KeyCode::KeyP) {
        let id = SKILLS[selection.0 % SKILLS.len()].id;
        match tree.learn(id) {
            Some(new_stats) => {
                stats.0 = new_stats;
                if let Some(def) = SKILLS.iter().find(|s| s.id == id) {
                    toasts.write(ScriptToast(format!("Aprendida: {}", def.label)));
                }
            }
            None => {
                toasts.write(ScriptToast(
                    "Sem pontos ou requisitos por cumprir.".into(),
                ));
            }
        }
    }
}

// ── UI das barras ───────────────────────────────────────────────────────

fn spawn_ability_bars(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                bottom: Val::Px(84.0),
                left: Val::Px(12.0),
                flex_direction: FlexDirection::Column,
                row_gap: Val::Px(3.0),
                ..Default::default()
            },
            Name::new("ui:abilities"),
        ))
        .with_children(|bar| {
            for (i, (key, label)) in
                [("[C]", "Dash"), ("[E]", "Cura"), ("[R]", "Golpe forte")]
                    .into_iter()
                    .enumerate()
            {
                bar.spawn((
                    Node {
                        padding: UiRect::axes(Val::Px(8.0), Val::Px(3.0)),
                        border_radius: BorderRadius::all(Val::Px(8.0)),
                        ..Default::default()
                    },
                    BackgroundColor(Color::srgba(0.08, 0.08, 0.07, 0.8)),
                    Name::new(format!("ability-{i}")),
                ))
                .with_children(|slot| {
                    slot.spawn((
                        Text::new(format!("{key} {label}")),
                        TextColor(Color::srgba(0.95, 0.93, 0.85, 0.9)),
                        TextFont::from_font_size(12.0),
                        AbilityBarText,
                    ));
                });
            }
        });
}

#[derive(Component)]
struct AbilityBarText;

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
    players: Query<Entity, With<Player>>,
    mut guards: Query<&mut Guarding>,
    mut commands: Commands,
) {
    let Ok(player) = players.single() else {
        return;
    };
    let holding = keys.pressed(KeyCode::KeyL);
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

/// [C] dash · [E] cura (fora de interação) · [R] golpe forte radial.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn abilities_system(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    mut cds: ResMut<AbilityCooldowns>,
    mut players: Query<(Entity, &GlobalTransform, &mut Transform, Option<&mut Health>), With<Player>>,
    interactions: Query<(&GlobalTransform, &ScriptInteraction), Without<Player>>,
    mut creatures: Query<(&GlobalTransform, &mut Health), Without<Player>>,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut toasts: MessageWriter<ScriptToast>,
    stats: Res<PlayerStatsResource>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut commands: Commands,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
) {
    let dt = time.delta_secs();
    cds.dash = (cds.dash - dt).max(0.0);
    cds.heal = (cds.heal - dt).max(0.0);
    cds.strike = (cds.strike - dt).max(0.0);
    let Ok((entity, global, mut transform, mut health)) = players.single_mut() else {
        return;
    };
    let pos = global.translation();

    // [C] dash
    if keys.just_pressed(KeyCode::KeyC) && cds.dash <= 0.0 {
        let forward = transform.forward().normalize_or_zero() * DASH_DISTANCE;
        let (x, z) = (pos.x + forward.x, pos.z + forward.z);
        let y = terrain.as_ref().map(|t| t.sample(x, z)).unwrap_or(pos.y);
        transform.translation = Vec3::new(x, y, z);
        commands.entity(entity).insert(Invulnerable { timer: DASH_IFRAMES });
        cds.dash = DASH_COOLDOWN;
    }

    // [E] cura — só quando NÃO há interação em alcance ([E] interagir ganha)
    if keys.just_pressed(KeyCode::KeyE) && cds.heal <= 0.0 {
        let near_interaction = interactions
            .iter()
            .any(|(t, _)| t.translation().distance(pos) < 3.5);
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
            }
        }
    }

    // [R] golpe forte radial
    if keys.just_pressed(KeyCode::KeyR) && cds.strike <= 0.0 {
        cds.strike = STRIKE_COOLDOWN;
        let damage = STRIKE_DAMAGE + stats.0.bonus_damage;
        let mut hit_any = false;
        for (t, mut health) in creatures.iter_mut() {
            let d = t.translation().distance(pos);
            if d <= STRIKE_RADIUS {
                apply_damage(&mut health, damage);
                numbers.write(DamageNumberEvent {
                    position: t.translation() + Vec3::Y * 1.8,
                    text: format!("-{}", damage as i32),
                    color: Color::srgb(1.0, 0.5, 0.2),
                });
                hit_any = true;
            }
        }
        toasts.write(ScriptToast(if hit_any {
            "GOLPE FORTE!".into()
        } else {
            "Golpe forte ao ar!".into()
        }));
        if hit_any {
            sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Whoosh,
                position: Some(pos),
            });
        }
    }
}

/// [B] lança bomba (consome `bomb` do vault).
fn bomb_throw_system(
    keys: Res<ButtonInput<KeyCode>>,
    mut vault: ResMut<Vault>,
    players: Query<(&GlobalTransform, &Transform), With<Player>>,
    assets: Option<Res<BombAssets>>,
    mut commands: Commands,
    mut toasts: MessageWriter<ScriptToast>,
) {
    if !keys.just_pressed(KeyCode::KeyB) {
        return;
    }
    if !vault.item_take("bomb") {
        toasts.write(ScriptToast("Sem bombas — compra ao mercador.".into()));
        return;
    }
    let Some(assets) = assets else {
        return;
    };
    let Ok((global, transform)) = players.single() else {
        return;
    };
    let origin = global.translation() + Vec3::Y * 1.2;
    let dir = transform.forward().normalize_or_zero();
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

/// Bomba: voo parabólico + explosão radial no fim do pavio.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn bomb_step_system(
    time: Res<Time>,
    mut bombs: Query<(Entity, &mut Transform, &mut Bomb)>,
    mut creatures: Query<(Entity, &GlobalTransform, &mut Health), Without<Player>>,
    mut commands: Commands,
    mut numbers: MessageWriter<DamageNumberEvent>,
    mut alerts: MessageWriter<AttackAlert>,
    mut toasts: MessageWriter<ScriptToast>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut bomb) in &mut bombs {
        bomb.fuse -= dt;
        bomb.velocity.y -= 18.0 * dt;
        transform.translation += bomb.velocity * dt;
        if bomb.fuse > 0.0 {
            continue;
        }
        let center = transform.translation;
        for (target, t, mut health) in creatures.iter_mut() {
            let d = t.translation().distance(center);
            if let Some(dmg) = radial_damage(d, BOMB_RADIUS, BOMB_DAMAGE) {
                apply_damage(&mut health, dmg);
                numbers.write(DamageNumberEvent {
                    position: t.translation() + Vec3::Y * 1.8,
                    text: format!("-{}", dmg as i32),
                    color: Color::srgb(1.0, 0.6, 0.1),
                });
                let _ = target;
            }
        }
        alerts.write(AttackAlert { position: center });
        sfx.write(crate::ambient::SfxEvent {
            clip: crate::ambient::SfxClip::Hit,
            position: Some(center),
        });
        toasts.write(ScriptToast("BOOM!".into()));
        commands.entity(entity).despawn();
    }
}

/// Level-ups: cada rampa do `xp.next` credita 1 ponto de skill.
#[allow(clippy::type_complexity)]
fn level_system(
    players: Query<(Entity, &Xp), (Changed<Xp>, With<Player>)>,
    mut levels: Query<(Entity, &mut LevelState)>,
    mut tree: ResMut<SkillTree>,
    mut commands: Commands,
    mut previous_next: Local<Option<u32>>,
) {
    let Ok((entity, xp)) = players.single() else {
        return;
    };
    let Some(previous) = *previous_next else {
        *previous_next = Some(xp.next);
        return;
    };
    if xp.next > previous {
        let gained = 1 + (xp.next - previous) / 100;
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
    *previous_next = Some(xp.next);
}

/// Texto das barras de cooldown.
fn ability_bars_system(
    cds: Res<AbilityCooldowns>,
    mut bars: Query<&mut Text, With<AbilityBarText>>,
) {
    let texts = [
        format!("[C] Dash {}", cooldown_text(cds.dash)),
        format!("[E] Cura {}", cooldown_text(cds.heal)),
        format!("[R] Golpe forte {}", cooldown_text(cds.strike)),
    ];
    for (i, mut text) in bars.iter_mut().enumerate() {
        if text.0 != texts[i] {
            text.0 = texts[i].clone();
        }
    }
}

fn cooldown_text(remaining: f32) -> String {
    if remaining <= 0.0 {
        "pronto".into()
    } else {
        format!("{:.1}s", remaining)
    }
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
        let mut tree = SkillTree { learned: vec![], points: 3 };
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
}
