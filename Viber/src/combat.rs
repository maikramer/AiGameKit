//! Combate corpo-a-corpo + animação de criaturas.
//!
//! O ataque do player (clique esquerdo) acerta o inimigo com script mais
//! próximo dentro do alcance e cone frontal; morte dá XP, anima `death` e o
//! cadáver some. As criaturas dinâmicas ganham driver de animação
//! (walk/idle) com o MESMO congelamento do script: além do raio de
//! [`ScriptActivation`] a animação nem avança (LOD de IA total).

use bevy::animation::AnimationPlayer;
use bevy::animation::transition::AnimationTransitions;
use bevy::input::mouse::MouseButton;
use bevy::prelude::*;
use std::collections::HashMap;
use std::time::Duration;

use crate::animation::{CharacterAnimator, play_state, state_for_motion};
use crate::luau::{LuaScriptRef, ScriptActivation, ScriptInteraction, ScriptToast};
use crate::player::Player;
use crate::vitals::{Health, Xp, apply_damage, gain_xp};

/// Alcance do golpe corpo-a-corpo (m).
pub const MELEE_RANGE: f32 = 2.8;
/// Dano base do golpe do herói.
pub const MELEE_DAMAGE: f32 = 25.0;
/// Entre golpes (s).
pub const MELEE_COOLDOWN: f32 = 0.55;
/// Cone frontal aceitável (dot da direção ao alvo com o forward do herói).
const ATTACK_CONE_DOT: f32 = 0.2;
/// XP por abate.
pub const KILL_XP: u32 = 15;
/// Segundos que o cadáver dura (animação de morte) antes de sumir.
pub const CORPSE_LIFETIME: f32 = 1.4;

/// Combatente morto: anima `death` e é removido após [`CORPSE_LIFETIME`].
#[derive(Debug, Clone, Component)]
pub struct Corpse {
    pub timer: f32,
}

/// Relógio do último swing (compartilhado entre o melee e o reset do clip).
#[derive(Debug, Default, Resource)]
pub struct SwingClock(pub Option<f64>);

pub struct CombatPlugin;

impl bevy::app::Plugin for CombatPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<ButtonInput<MouseButton>>();
        app.init_resource::<SwingClock>();
        app.init_resource::<HeldWeapon>();
        // Números de dano que o melee/fireball emitem (idempotente com o
        // FeedbackPlugin) + soft-lock do alvo.
        app.add_message::<crate::feedback::DamageNumberEvent>();
        app.init_resource::<crate::feedback::CombatTarget>();
        app.add_systems(
            Update,
            (
                ensure_player_vitals,
                ensure_creature_vitals,
                cycle_weapon,
                player_melee_attack,
                cast_fireball,
                fireball_step,
                reset_hero_attack_clip,
                play_death_animation,
                drive_creature_animation,
                tick_corpses,
            ),
        );
    }
}

/// Garante vitals no herói (o HUD e o dano dos scripts dependem disto).
#[allow(clippy::type_complexity)]
pub fn ensure_player_vitals(
    players: Query<(Entity, Option<&Health>, Option<&Xp>), With<Player>>,
    mut commands: Commands,
) {
    for (entity, health, xp) in &players {
        let mut entity = commands.entity(entity);
        if health.is_none() {
            entity.insert(Health::default());
        }
        if xp.is_none() {
            entity.insert(Xp::default());
        }
    }
}

/// Bosses são GameObjects ESTÁTICOS com script (sem spawner dinâmico que
/// insira `Health`) — sem vitals eles ficam fora do set de alvos do melee.
/// Insere `Health` em qualquer scriptado sem interação (POIs/colheita ficam
/// de fora de propósito: não são combatentes).
#[allow(clippy::type_complexity)]
pub fn ensure_creature_vitals(
    creatures: Query<Entity, (With<LuaScriptRef>, Without<ScriptInteraction>, Without<Health>, Without<Player>)>,
    mut commands: Commands,
) {
    for entity in &creatures {
        commands.entity(entity).insert(Health::default());
    }
}

/// Golpe do herói: alvo com script mais próximo dentro de alcance + cone.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub fn player_melee_attack(
    keys: Res<ButtonInput<KeyCode>>,
    mouse: Res<ButtonInput<MouseButton>>,
    harvest_targets: Query<(&GlobalTransform, &ScriptInteraction), Without<Player>>,
    time: Res<Time>,
    mut last: Local<Option<f64>>,
    mut hero_attack_started: ResMut<SwingClock>,
    mut commands: Commands,
    mut toasts: bevy::ecs::message::MessageWriter<ScriptToast>,
    mut numbers: bevy::ecs::message::MessageWriter<crate::feedback::DamageNumberEvent>,
    mut combat_target: ResMut<crate::feedback::CombatTarget>,
    players: Query<&GlobalTransform, With<Player>>,
    mut hero_xp: Query<&mut Xp, With<Player>>,
    mut hero_animator: Query<&mut CharacterAnimator, With<Player>>,
    mut animation_players: Query<(&mut AnimationPlayer, &mut AnimationTransitions)>,
    mut enemies: Query<
        (Entity, &GlobalTransform, &mut Health),
        (Without<Player>, With<LuaScriptRef>),
    >,
) {
    let j_pressed = keys.just_pressed(KeyCode::KeyJ);
    let triggered =
        mouse.just_pressed(MouseButton::Left) || keys.just_pressed(KeyCode::KeyR) || j_pressed;
    if !triggered {
        return;
    }
    // J contextual: se há alvo de colheita (ScriptInteraction com tecla J) no
    // alcance, o golpe vai para a coleta — o script do alvo cuida do resto.
    if j_pressed {
        if let Ok(player) = players.single() {
            let origin = player.translation();
            let near_harvest = harvest_targets.iter().any(|(t, interaction)| {
                interaction.key == KeyCode::KeyJ
                    && t.translation().distance(origin) <= interaction.range.min(3.5)
            });
            if near_harvest {
                return;
            }
        }
    }
    if let Some(last) = *last {
        if time.elapsed_secs_f64() - last < MELEE_COOLDOWN as f64 {
            return;
        }
    }
    let Ok(player) = players.single() else {
        return;
    };
    let origin = player.translation();
    let forward = player.forward();

    // (entity, distância) do alvo mais próximo dentro do alcance e do cone.
    let mut best: Option<(Entity, f32)> = None;
    for (entity, transform, _) in &mut enemies {
        let to_target = transform.translation() - origin;
        let dist = to_target.length();
        if dist > MELEE_RANGE {
            continue;
        }
        let dir = to_target / dist.max(1e-4);
        if forward.dot(dir) < ATTACK_CONE_DOT {
            continue;
        }
        if best.map(|(_, d)| dist < d).unwrap_or(true) {
            best = Some((entity, dist));
        }
    }
    // Golpe acontece SEMPRE (whiff incluído) — feedback imediato; o dano é
    // que depende de acertar um alvo.
    let hit_entity: Option<Entity> = best.map(|(e, _)| e);
    *last = Some(time.elapsed_secs_f64());
    hero_attack_started.0 = Some(time.elapsed_secs_f64());
    // Clip de ataque do herói, por NOME ("attack" no hero_lod0). Não mexe em
    // `animator.state` — o driver de movimento só re-afirma walk/idle quando
    // o estado difere, então o golpe toca inteiro por cima.
    if let Ok(animator) = hero_animator.single_mut() {
        let node = animator
            .clip_names
            .iter()
            .position(|name| name.eq_ignore_ascii_case("attack"))
            .and_then(|i| animator.nodes.get(i).copied());
        if let Some(node) = node {
            if let Ok((mut player, mut transitions)) = animation_players.get_mut(animator.player) {
                transitions
                    .play(&mut player, node, Duration::from_millis(80))
                    .repeat();
            }
        }
    }
    if let Some(entity) = hit_entity {
        // Soft-lock do VibeGame: acertar fixa o alvo da TargetBar (TTL 8 s).
        combat_target.entity = Some(entity);
        combat_target.timer = crate::feedback::TARGET_TTL;
        let hit_pos = enemies
            .get(entity)
            .ok()
            .map(|(_, t, _)| t.translation() + Vec3::Y * 1.8);
        let killed = if let Ok((_, _, mut health)) = enemies.get_mut(entity) {
            apply_damage(&mut health, MELEE_DAMAGE);
            health.current <= 0.0
        } else {
            false
        };
        if let Some(position) = hit_pos {
            numbers.write(crate::feedback::DamageNumberEvent {
                position,
                text: format!("-{MELEE_DAMAGE}"),
                color: Color::srgb(1.0, 0.96, 0.85),
            });
        }
        if killed {
            commands.entity(entity).remove::<LuaScriptRef>();
            commands.entity(entity).insert(Corpse {
                timer: CORPSE_LIFETIME,
            });
            if let Ok(mut xp) = hero_xp.single_mut() {
                gain_xp(&mut xp, KILL_XP);
            }
            if let Some(position) = hit_pos {
                numbers.write(crate::feedback::DamageNumberEvent {
                    position: position + Vec3::Y * 0.4,
                    text: format!("+{KILL_XP} XP"),
                    color: Color::srgb(1.0, 0.8, 0.25),
                });
            }
            toasts.write(ScriptToast(format!("Inimigo derrotado (+{KILL_XP} XP)")));
        } else {
            info!(target: "viber::combat", "hit {entity:?}");
        }
    }
}

/// Toca o clip de morte assim que a entidade vira cadáver — por NOME (o
/// `AnimState` da engine não tem variante Death; os rigs spellam "death" /
/// "Animator3D_Death").
pub fn play_death_animation(
    mut dead: Query<(Entity, &mut CharacterAnimator), Added<Corpse>>,
    mut players: Query<(&mut AnimationPlayer, &mut AnimationTransitions)>,
) {
    for (_, animator) in &mut dead {
        let node = animator
            .clip_names
            .iter()
            .position(|name| {
                let lower = name.to_ascii_lowercase();
                lower == "death" || lower == "animator3d_death" || lower.ends_with("_death")
            })
            .and_then(|i| animator.nodes.get(i).copied());
        let Some(node) = node else { continue };
        if let Ok((mut player, mut transitions)) = players.get_mut(animator.player) {
            transitions.play(&mut player, node, Duration::ZERO).repeat();
        }
    }
}

/// Anima as criaturas com script (walk/idle pela velocidade real). Congeladas
/// além do raio de ativação: a animação nem avança (LOD de IA total).
#[allow(clippy::type_complexity)]
pub fn drive_creature_animation(
    mut creatures: Query<
        (
            Entity,
            &Transform,
            &mut CharacterAnimator,
            Option<&ScriptActivation>,
        ),
        (With<LuaScriptRef>, Without<Player>),
    >,
    player: Query<&GlobalTransform, (With<Player>, Without<LuaScriptRef>)>,
    mut players: Query<(&mut AnimationPlayer, &mut AnimationTransitions)>,
    mut last: Local<HashMap<Entity, Vec3>>,
    time: Res<Time>,
) {
    let dt = time.delta_secs().max(1e-4);
    let player_pos = player.single().ok().map(|g| g.translation());
    for (entity, transform, mut animator, activation) in &mut creatures {
        let radius = activation.map(|a| a.radius).unwrap_or(45.0);
        if let Some(p) = player_pos {
            if transform.translation.distance(p) > radius {
                continue; // congelada: nem o driver avança
            }
        }
        let previous = last.insert(entity, transform.translation);
        let speed = previous
            .map(|prev| prev.distance(transform.translation) / dt)
            .unwrap_or(0.0);
        let state = state_for_motion(speed, true, 0.0);
        if animator.state != Some(state) {
            play_state(&mut animator, &mut players, state);
        }
    }
}

/// Cadáveres somem após a animação de morte.
pub fn tick_corpses(
    mut corpses: Query<(Entity, &mut Corpse)>,
    mut commands: Commands,
    time: Res<Time>,
) {
    let dt = time.delta_secs();
    for (entity, mut corpse) in &mut corpses {
        corpse.timer -= dt;
        if corpse.timer <= 0.0 {
            commands.entity(entity).despawn();
        }
    }
}

// ── Arma na mão (grips copiados do VibeGame `data/held-items.json`) ────
// Bone: `hand_r` (rig Mixamo do herói; candidates do held-item.ts).
// Ordem de busca espelha HAND_BONE_CANDIDATES + fuzzy "righthand".

/// Quando o swing termina, devolve o controle do clip ao driver de movimento
/// (`animator.state = None` faz o drive re-afirmar idle/walk no próximo frame).
#[allow(clippy::type_complexity)]
pub fn reset_hero_attack_clip(
    clock: Res<SwingClock>,
    time: Res<Time>,
    mut hero: Query<&mut CharacterAnimator, With<Player>>,
) {
    let Some(started) = clock.0 else { return };
    if time.elapsed_secs_f64() - started < MELEE_COOLDOWN as f64 {
        return;
    }
    if let Ok(mut animator) = hero.single_mut() {
        if animator.state.is_none() {
            return;
        }
        animator.state = None;
    }
}

// ── Troca de armas ([V]: espada → machado → lança) ─────────────────────
// Grips copiados do VibeGame `dist/data/held-items.json`.

#[derive(Debug, Clone, Resource, Default)]
pub struct HeldWeapon {
    /// Índice em [`WEAPON_TABLE`].
    pub idx: usize,
    /// Osso da mão do herói (preenchido na primeira detecção).
    pub bone: Option<Entity>,
    /// Entidade da arma atual (filha do osso) — trocada no [V].
    pub current: Option<Entity>,
    /// Nomes de nó já varridos (evita re-busca por frame).
    pub searched: bool,
}

/// (url, pos, rot XYZ rad, scale, rótulo)
#[allow(clippy::type_complexity)]
pub const WEAPON_TABLE: [(&str, [f32; 3], [f32; 3], f32, &str); 3] = [
    (
        "assets/meshes/props/sword_hero_lod0.glb",
        [0.12, 0.04, 0.04],
        [-1.33, 12.71, 0.96],
        1.0,
        "espada",
    ),
    (
        "assets/meshes/props/axe_lod0.glb",
        [0.23, 0.11, 0.01],
        [2.98, 12.71, std::f32::consts::FRAC_PI_2],
        1.0,
        "machado",
    ),
    (
        "assets/meshes/props/spear_lod0.glb",
        [0.2, 0.01, 0.04],
        [-1.33, 12.71, 0.96],
        1.0,
        "lança",
    ),
];

/// Anexa a arma inicial (espada) e troca no [V].
#[allow(clippy::type_complexity)]
pub fn cycle_weapon(
    mut commands: Commands,
    keys: Res<ButtonInput<KeyCode>>,
    asset_server: Res<AssetServer>,
    heroes: Query<Entity, With<Player>>,
    names: Query<&Name>,
    children: Query<&Children>,
    mut held: ResMut<HeldWeapon>,
) {
    let cycle = keys.just_pressed(KeyCode::KeyV);
    if held.bone.is_none() && !held.searched {
        let Ok(hero) = heroes.single() else { return };
        held.bone = find_hand_bone(hero, &children, &names);
        held.searched = true;
    }
    let Some(bone) = held.bone else { return };
    let first_attach = held.current.is_none();
    if !first_attach && !cycle {
        return;
    }
    if cycle {
        held.idx = (held.idx + 1) % WEAPON_TABLE.len();
    }
    if let Some(old) = held.current.take() {
        commands.entity(old).despawn();
    }
    let (url, pos, rot, scale, _label) = WEAPON_TABLE[held.idx];
    let handle = crate::meshopt::load_gltf(&asset_server, url.to_owned());
    let mut transform = Transform::from_translation(Vec3::new(pos[0], pos[1], pos[2]));
    transform.rotation = Quat::from_euler(EulerRot::XYZ, rot[0], rot[1], rot[2]);
    transform.scale = Vec3::splat(scale);
    let spawned = commands
        .spawn((
            transform,
            Visibility::Inherited,
            crate::recipes::spawn::GltfScenePending { handle },
        ))
        .id();
    commands.entity(bone).add_child(spawned);
    held.current = Some(spawned);
}

/// Procura o osso da mão por subárvore (mesmos candidates do held-item.ts).
fn find_hand_bone(
    root: Entity,
    children: &Query<&Children>,
    names: &Query<&Name>,
) -> Option<Entity> {
    let name = names.get(root).ok()?.to_ascii_lowercase();
    let is_hand = matches!(
        name.as_str(),
        "hand_r" | "righthand" | "right_hand" | "right hand"
    ) || (name.contains("hand_r") && !name.contains("finger"))
        || (name.contains("righthand") && !name.contains("finger"));
    if is_hand {
        return Some(root);
    }
    for child in children.get(root).ok()?.iter() {
        if let Some(found) = find_hand_bone(child, children, names) {
            return Some(found);
        }
    }
    None
}

// ── Skill: bola de fogo (botão direito) ────────────────────────────────
// Projétil em frente ao herói; explode no primeiro inimigo (com Health)
// num raio de 2.5 m (40 de dano em área) ou ao fim da vida.

const FIREBALL_SPEED: f32 = 18.0;
const FIREBALL_LIFE: f32 = 2.0;
const FIREBALL_DAMAGE: f32 = 40.0;
const FIREBALL_RADIUS: f32 = 2.5;
const FIREBALL_COOLDOWN: f32 = 1.2;

#[derive(Debug, Component)]
pub struct Fireball {
    pub vel: Vec3,
    pub life: f32,
}

#[allow(clippy::type_complexity)]
pub fn cast_fireball(
    mut commands: Commands,
    mouse: Res<ButtonInput<MouseButton>>,
    time: Res<Time>,
    mut last: Local<Option<f64>>,
    players: Query<&GlobalTransform, With<Player>>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    if !mouse.just_pressed(MouseButton::Right) {
        return;
    }
    if let Some(last) = *last {
        if time.elapsed_secs_f64() - last < FIREBALL_COOLDOWN as f64 {
            return;
        }
    }
    let Ok(player) = players.single() else {
        return;
    };
    *last = Some(time.elapsed_secs_f64());
    let origin = player.translation() + player.forward() * 0.8 + Vec3::Y * 1.2;
    let mesh = meshes.add(Sphere::new(0.16));
    let material = materials.add(StandardMaterial {
        base_color: Color::srgb(1.0, 0.45, 0.1),
        emissive: LinearRgba::rgb(2.5, 0.9, 0.15),
        unlit: true,
        ..StandardMaterial::default()
    });
    commands.spawn((
        Transform::from_translation(origin),
        Visibility::Inherited,
        Mesh3d(mesh),
        MeshMaterial3d(material),
        Fireball {
            vel: player.forward() * FIREBALL_SPEED,
            life: FIREBALL_LIFE,
        },
    ));
}

/// Move a bola de fogo, detecta impacto e aplica dano em área.
#[allow(clippy::type_complexity)]
pub fn fireball_step(
    mut commands: Commands,
    time: Res<Time>,
    mut balls: Query<(Entity, &mut Transform, &mut Fireball)>,
    mut enemies: Query<
        (Entity, &GlobalTransform, &mut Health),
        (Without<Player>, With<LuaScriptRef>),
    >,
    mut toasts: bevy::ecs::message::MessageWriter<ScriptToast>,
    mut hero_xp: Query<&mut Xp, With<Player>>,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut ball) in &mut balls {
        ball.life -= dt;
        if ball.life <= 0.0 {
            commands.entity(entity).despawn();
            continue;
        }
        transform.translation += ball.vel * dt;
        // Impacto: qualquer inimigo num raio de contato.
        let mut impact: Option<Vec3> = None;
        for (_, t, _) in &enemies {
            if t.translation().distance(transform.translation) < 1.2 {
                impact = Some(t.translation());
                break;
            }
        }
        if let Some(center) = impact {
            // Dano em área + XP por abate.
            let mut kills: u32 = 0;
            for (entity, t, mut health) in &mut enemies {
                if t.translation().distance(center) <= FIREBALL_RADIUS {
                    apply_damage(&mut health, FIREBALL_DAMAGE);
                    if health.current <= 0.0 {
                        kills += 1;
                        commands.entity(entity).remove::<LuaScriptRef>();
                        commands.entity(entity).insert(Corpse {
                            timer: CORPSE_LIFETIME,
                        });
                    }
                }
            }
            if kills > 0 {
                if let Ok(mut xp) = hero_xp.single_mut() {
                    gain_xp(&mut xp, KILL_XP * kills);
                }
                toasts.write(ScriptToast(format!(
                    "Bola de fogo! {kills} abatido(s) (+{} XP)",
                    KILL_XP * kills
                )));
            }
            commands.entity(entity).despawn();
        }
    }
}
