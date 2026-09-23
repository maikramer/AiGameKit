//! `<ProjectileTemplate id="enemy-arrow" speed="16" damage="8" max-life="2.5"
//! sensor-radius="0.3" faction="enemy" />` — projéteis declarativos disparados
//! por script (`viber.fire_projectile(id [, x, y, z])`).
//!
//! O mundo declara o COMO (velocidade, dano, vida, raio do sensor, facção,
//! gravidade, cor/tamanho); o script decide o QUANDO e o PARA ONDE. Sem alvo
//! explícito o projétil aponta ao peito do herói.
//!
//! - `faction="enemy"` acerta o herói (path único de dano: `PlayerHurt`, com
//!   i-frames/knockback do feedback);
//! - `faction="player"` acerta criaturas com `Health` (paridade com a bola de
//!   fogo: número, flash, recoil, alerta de aggro e abate via
//!   `skills::kill_creature`);
//! - `faction="neutral"` só colide com o terreno.
//!
//! Com `gravity > 0` a mira resolve o arco balístico baixo que passa no alvo
//! ([`launch_velocity`]); fora de alcance sai a 45° (o máximo alcance).

use std::collections::HashMap;

use bevy::prelude::*;

use crate::combat::Corpse;
use crate::luau::{LuaScriptRef, ScriptToast};
use crate::player::Player;
use crate::vitals::{Health, Xp};
use crate::worldsys::EngineConfigs;

/// Teto de projéteis vivos — um script em loop não enche o mundo.
pub const MAX_LIVE_PROJECTILES: usize = 256;
/// Raio da cápsula do herói/criaturas para o teste do sensor (m).
const BODY_RADIUS: f32 = 0.45;
/// Cápsula do corpo: dos pés (+0.3) ao topo (+1.6).
const BODY_LOW: f32 = 0.3;
const BODY_HIGH: f32 = 1.6;
/// Altura do peito — o alvo por omissão.
pub const CHEST_HEIGHT: f32 = 1.1;
/// Altura da boca de disparo acima da origem do atirador.
pub const MUZZLE_HEIGHT: f32 = 1.2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Faction {
    /// Acerta o herói.
    Enemy,
    /// Acerta criaturas com `Health`.
    Player,
    /// Só terreno.
    Neutral,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ProjectileTemplate {
    pub id: String,
    pub speed: f32,
    pub damage: f32,
    pub max_life: f32,
    pub sensor_radius: f32,
    pub faction: Faction,
    /// m/s² para baixo (0 = linha reta).
    pub gravity: f32,
    pub color: [f32; 3],
    /// Raio da esfera visível (m).
    pub size: f32,
}

impl ProjectileTemplate {
    /// Lê um `<ProjectileTemplate>`; `Err` só quando falta o `id`. Valores
    /// inválidos caem no default com warning (o resto da tag vale).
    pub fn from_config(
        config: &crate::worldsys::EngineConfigData,
        warnings: &mut Vec<String>,
    ) -> Result<Self, String> {
        let id = config
            .attr("id")
            .map(str::trim)
            .filter(|id| !id.is_empty())
            .ok_or("<ProjectileTemplate> sem id")?
            .to_string();
        let mut num = |key: &str, default: f32, min: f32| -> f32 {
            match config.attr(key) {
                None => default,
                Some(raw) => match raw.trim().parse::<f32>() {
                    Ok(v) if v.is_finite() && v >= min => v,
                    _ => {
                        warnings.push(format!(
                            "projectile '{id}': {key}='{raw}' inválido — {default}"
                        ));
                        default
                    }
                },
            }
        };
        let speed = num("speed", 16.0, 0.1);
        let damage = num("damage", 8.0, 0.0);
        let max_life = num("max-life", 2.5, 0.05);
        let sensor_radius = num("sensor-radius", 0.3, 0.01);
        let gravity = num("gravity", 0.0, 0.0);
        let size = num("size", 0.12, 0.01);
        let faction = match config
            .attr("faction")
            .map(|f| f.trim().to_ascii_lowercase())
        {
            None => Faction::Enemy,
            Some(f) => match f.as_str() {
                "enemy" | "hostile" => Faction::Enemy,
                "player" | "hero" | "ally" => Faction::Player,
                "neutral" | "none" => Faction::Neutral,
                _ => {
                    warnings.push(format!(
                        "projectile '{id}': faction='{f}' desconhecida — enemy"
                    ));
                    Faction::Enemy
                }
            },
        };
        let color = match config.attr("color") {
            None => match faction {
                Faction::Enemy => [1.0, 0.35, 0.2],
                Faction::Player => [0.35, 0.75, 1.0],
                Faction::Neutral => [0.9, 0.9, 0.8],
            },
            Some(raw) => crate::xml::values::parse_color(raw, "color").unwrap_or_else(|err| {
                warnings.push(format!("projectile '{id}': {err}"));
                [1.0, 0.35, 0.2]
            }),
        };
        Ok(Self {
            id,
            speed,
            damage,
            max_life,
            sensor_radius,
            faction,
            gravity,
            color,
            size,
        })
    }
}

/// Os `<ProjectileTemplate>` do mundo, por `id`.
#[derive(Debug, Clone, Default, Resource)]
pub struct ProjectileTemplates(pub HashMap<String, ProjectileTemplate>);

/// Um disparo pedido (por script) e ainda por nascer.
#[derive(Debug, Clone, PartialEq)]
pub struct ProjectileRequest {
    pub template: String,
    /// Boca de disparo (mundo).
    pub origin: Vec3,
    pub target: Vec3,
    /// Quem disparou (a entidade do script) — o projétil nunca a acerta:
    /// sai de dentro da cápsula dela.
    pub shooter: Option<Entity>,
}

/// Fila de disparos (`viber.fire_projectile` → [`spawn_projectiles`]).
#[derive(Debug, Default, Resource)]
pub struct ProjectileQueue {
    pub requests: Vec<ProjectileRequest>,
}

/// Um projétil em voo.
#[derive(Debug, Clone, Component)]
pub struct Projectile {
    pub vel: Vec3,
    pub life: f32,
    pub damage: f32,
    pub sensor_radius: f32,
    pub faction: Faction,
    pub gravity: f32,
    pub shooter: Option<Entity>,
}

/// Velocidade inicial que leva `origin` a `target` a `speed` m/s.
///
/// Sem gravidade é a linha reta. Com gravidade, o arco BAIXO (o mais rápido
/// a chegar) da equação balística; alvo fora de alcance → 45° na direção
/// dele, o que mais se aproxima.
pub fn launch_velocity(origin: Vec3, target: Vec3, speed: f32, gravity: f32) -> Vec3 {
    let delta = target - origin;
    if delta.length_squared() < 1e-8 {
        return Vec3::Z * speed;
    }
    if gravity <= 0.0 {
        return delta.normalize() * speed;
    }
    let flat = Vec3::new(delta.x, 0.0, delta.z);
    let d = flat.length();
    if d < 1e-4 {
        return Vec3::Y * speed * delta.y.signum();
    }
    let dir = flat / d;
    let (v2, g, h) = (speed * speed, gravity, delta.y);
    let disc = v2 * v2 - g * (g * d * d + 2.0 * h * v2);
    let angle = if disc >= 0.0 {
        ((v2 - disc.sqrt()) / (g * d)).atan()
    } else {
        std::f32::consts::FRAC_PI_4
    };
    dir * speed * angle.cos() + Vec3::Y * speed * angle.sin()
}

/// Menor distância entre o passo do projétil (`a → b`) e o eixo da cápsula de
/// um corpo com os pés em `feet`, mais a fração do passo (0..1) onde ela
/// acontece. Varrido: testar só a posição final deixava um passo longo
/// (soluço de frame, template rápido) atravessar o corpo sem acertar.
fn swept_body_distance(a: Vec3, b: Vec3, feet: Vec3) -> (f32, f32) {
    let low = feet + Vec3::Y * BODY_LOW;
    let axis = Vec3::Y * (BODY_HIGH - BODY_LOW);
    let step = b - a;
    let r = a - low;
    let (aa, ee, f) = (step.length_squared(), axis.length_squared(), axis.dot(r));
    let (s, t) = if aa <= 1e-12 {
        (0.0, (f / ee).clamp(0.0, 1.0))
    } else {
        let c = step.dot(r);
        let bb = step.dot(axis);
        let denom = aa * ee - bb * bb;
        let mut s = if denom > 1e-12 {
            ((bb * f - c * ee) / denom).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let mut t = (bb * s + f) / ee;
        if t < 0.0 {
            t = 0.0;
            s = (-c / aa).clamp(0.0, 1.0);
        } else if t > 1.0 {
            t = 1.0;
            s = ((bb - c) / aa).clamp(0.0, 1.0);
        }
        (s, t)
    };
    ((a + step * s).distance(low + axis * t), s)
}

fn install_templates(
    mut templates: ResMut<ProjectileTemplates>,
    configs: Option<Res<EngineConfigs>>,
) {
    let Some(configs) = configs else { return };
    let mut warnings = Vec::new();
    for config in configs.all("projectiletemplate") {
        match ProjectileTemplate::from_config(config, &mut warnings) {
            Ok(template) => {
                if templates.0.contains_key(&template.id) {
                    warnings.push(format!(
                        "projectile '{}' declarado 2× — fica o último",
                        template.id
                    ));
                }
                templates.0.insert(template.id.clone(), template);
            }
            Err(err) => warnings.push(err),
        }
    }
    for warning in warnings {
        warn!("{warning}");
    }
    if !templates.0.is_empty() {
        let mut ids: Vec<_> = templates.0.keys().cloned().collect();
        ids.sort();
        info!("projectile: {} template(s) — {}", ids.len(), ids.join(", "));
    }
}

/// Visual partilhado por template (uma mesh + um material por `id`).
#[derive(Default)]
struct ProjectileVisuals(HashMap<String, (Handle<Mesh>, Handle<StandardMaterial>)>);

#[allow(clippy::too_many_arguments)]
fn spawn_projectiles(
    mut commands: Commands,
    mut queue: ResMut<ProjectileQueue>,
    templates: Res<ProjectileTemplates>,
    live: Query<(), With<Projectile>>,
    meshes: Option<ResMut<Assets<Mesh>>>,
    materials: Option<ResMut<Assets<StandardMaterial>>>,
    mut visuals: Local<ProjectileVisuals>,
    mut warned: Local<std::collections::HashSet<String>>,
) {
    if queue.requests.is_empty() {
        return;
    }
    let mut live_count = live.iter().count();
    let (mut meshes, mut materials) = (meshes, materials);
    for request in std::mem::take(&mut queue.requests) {
        let Some(template) = templates.0.get(&request.template) else {
            if warned.insert(request.template.clone()) {
                warn!(
                    "viber.fire_projectile: template '{}' não declarado (<ProjectileTemplate id=…>)",
                    request.template
                );
            }
            continue;
        };
        if live_count >= MAX_LIVE_PROJECTILES {
            if warned.insert("__cap".into()) {
                warn!(
                    "projectile: teto de {MAX_LIVE_PROJECTILES} projéteis vivos — disparos descartados"
                );
            }
            continue;
        }
        live_count += 1;
        let vel = launch_velocity(
            request.origin,
            request.target,
            template.speed,
            template.gravity,
        );
        let mut entity = commands.spawn((
            Name::new(format!("projectile:{}", template.id)),
            Transform::from_translation(request.origin),
            Visibility::Inherited,
            Projectile {
                vel,
                life: template.max_life,
                damage: template.damage,
                sensor_radius: template.sensor_radius,
                faction: template.faction,
                gravity: template.gravity,
                shooter: request.shooter,
            },
        ));
        if let (Some(meshes), Some(materials)) = (meshes.as_deref_mut(), materials.as_deref_mut()) {
            let (mesh, material) = visuals
                .0
                .entry(template.id.clone())
                .or_insert_with(|| {
                    let [r, g, b] = template.color;
                    (
                        meshes.add(Sphere::new(template.size)),
                        materials.add(StandardMaterial {
                            base_color: Color::srgb(r, g, b),
                            emissive: LinearRgba::rgb(r * 2.5, g * 2.5, b * 2.5),
                            unlit: true,
                            ..StandardMaterial::default()
                        }),
                    )
                })
                .clone();
            entity.insert((Mesh3d(mesh), MeshMaterial3d(material)));
        }
    }
}

/// Mensagens de impacto (agrupadas: o `step_projectiles` passa o teto de
/// parâmetros com elas soltas).
#[derive(bevy::ecs::system::SystemParam)]
struct ImpactOut<'w> {
    hurts: MessageWriter<'w, crate::feedback::PlayerHurt>,
    numbers: MessageWriter<'w, crate::feedback::DamageNumberEvent>,
    alerts: MessageWriter<'w, crate::feedback::AttackAlert>,
    toasts: MessageWriter<'w, ScriptToast>,
    sfx: MessageWriter<'w, crate::ambient::SfxEvent>,
    quests: Option<ResMut<'w, crate::quests::QuestLog>>,
    events: Option<ResMut<'w, crate::luau::ScriptEventQueue>>,
}

fn impact_burst(commands: &mut Commands, at: Vec3) {
    crate::particles::spawn_burst(commands, &crate::combat::hit_sparks_spec(), at, 8);
}

#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn step_projectiles(
    mut commands: Commands,
    time: Res<Time>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut out: ImpactOut,
    mut projectiles: Query<(Entity, &mut Transform, &mut Projectile)>,
    players: Query<(Entity, &GlobalTransform), With<Player>>,
    mut creatures: Query<
        (
            Entity,
            &GlobalTransform,
            &mut Health,
            Option<&LuaScriptRef>,
            Option<&crate::impact::HitRecoil>,
        ),
        (Without<Player>, Without<Corpse>, Without<Projectile>),
    >,
    mut hero_xp: Query<&mut Xp, With<Player>>,
) {
    let dt = time.delta_secs();
    if dt <= 0.0 {
        return;
    }
    let hero = players
        .iter()
        .next()
        .map(|(entity, transform)| (entity, transform.translation()));
    for (entity, mut transform, mut shot) in &mut projectiles {
        shot.life -= dt;
        if shot.life <= 0.0 {
            commands.entity(entity).despawn();
            continue;
        }
        let prev = transform.translation;
        shot.vel.y -= shot.gravity * dt;
        let pos = prev + shot.vel * dt;
        transform.translation = pos;
        if let Some(dir) = shot.vel.try_normalize() {
            transform.look_to(dir, Vec3::Y);
        }

        let reach = shot.sensor_radius + BODY_RADIUS;
        match shot.faction {
            Faction::Enemy => {
                if let Some((_, feet)) = hero.filter(|(e, _)| shot.shooter != Some(*e)) {
                    let (gap, along) = swept_body_distance(prev, pos, feet);
                    if gap <= reach {
                        out.hurts.write(crate::feedback::PlayerHurt {
                            amount: shot.damage,
                            status: false,
                            from: Some(prev - shot.vel.normalize_or_zero() * 2.0),
                        });
                        impact_burst(&mut commands, prev.lerp(pos, along));
                        commands.entity(entity).despawn();
                        continue;
                    }
                }
            }
            Faction::Player => {
                // O PRIMEIRO corpo ao longo do passo, não o primeiro da query.
                let hit = creatures
                    .iter()
                    .filter(|(e, _, h, ..)| h.current > 0.0 && shot.shooter != Some(*e))
                    .filter_map(|(e, t, ..)| {
                        let (gap, along) = swept_body_distance(prev, pos, t.translation());
                        (gap <= reach).then_some((e, along))
                    })
                    .min_by(|a, b| a.1.total_cmp(&b.1));
                if let Some((target, along)) = hit {
                    let pos = prev.lerp(pos, along);
                    if let Ok((target, t, mut health, script, recoil)) = creatures.get_mut(target) {
                        crate::vitals::apply_damage(&mut health, shot.damage);
                        let at = t.translation();
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
                        out.numbers.write(crate::feedback::DamageNumberEvent {
                            position: at + Vec3::Y * 1.8,
                            text: format!("-{}", shot.damage.round() as i32),
                            color: Color::srgb(0.55, 0.85, 1.0),
                        });
                        out.alerts
                            .write(crate::feedback::AttackAlert { position: at });
                        // Criaturas FSM sem script também morrem (paridade
                        // com melee/abilities/fireball).
                        if health.current <= 0.0 {
                            crate::skills::kill_creature(
                                &mut commands,
                                target,
                                script,
                                at,
                                &mut hero_xp,
                                &mut out.numbers,
                                &mut out.toasts,
                                &mut out.quests,
                                &mut out.sfx,
                                &mut out.events,
                            );
                        }
                    }
                    impact_burst(&mut commands, pos);
                    commands.entity(entity).despawn();
                    continue;
                }
            }
            Faction::Neutral => {}
        }

        // Terreno: a superfície sólida sob a posição ANTERIOR (grutas e
        // overhangs contam) ficou acima da atual → atravessou o chão.
        if let Some(rt) = runtime.as_deref() {
            if rt.in_field(pos.x, pos.z)
                && rt
                    .surface_below(pos.x, pos.z, prev.y.max(pos.y) + 0.01)
                    .is_some_and(|ground| pos.y <= ground)
            {
                impact_burst(&mut commands, pos);
                commands.entity(entity).despawn();
            }
        }
    }
}

/// Liga os `<ProjectileTemplate>` e o `viber.fire_projectile`.
pub struct ProjectilePlugin;

impl Plugin for ProjectilePlugin {
    fn build(&self, app: &mut App) {
        // `add_message` é idempotente: com o preset RPG os plugins de
        // combate já os registaram; com `gameplay: none` é aqui.
        app.add_message::<crate::feedback::PlayerHurt>()
            .add_message::<crate::feedback::DamageNumberEvent>()
            .add_message::<crate::feedback::AttackAlert>()
            .add_message::<ScriptToast>()
            .add_message::<crate::ambient::SfxEvent>()
            .init_resource::<ProjectileTemplates>()
            .init_resource::<ProjectileQueue>()
            .add_systems(
                Startup,
                install_templates.after(crate::recipes::spawn::startup),
            )
            .add_systems(Update, (spawn_projectiles, step_projectiles).chain());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config(attrs: &[(&str, &str)]) -> crate::worldsys::EngineConfigData {
        crate::worldsys::EngineConfigData {
            tag: "projectiletemplate".into(),
            attrs: attrs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        }
    }

    #[test]
    fn test_template_parses_the_simple_rpg_arrow() {
        let mut warnings = Vec::new();
        let t = ProjectileTemplate::from_config(
            &config(&[
                ("id", "enemy-arrow"),
                ("speed", "16"),
                ("damage", "8"),
                ("max-life", "2.5"),
                ("sensor-radius", "0.3"),
                ("faction", "enemy"),
            ]),
            &mut warnings,
        )
        .unwrap();
        assert!(warnings.is_empty(), "{warnings:?}");
        assert_eq!(t.id, "enemy-arrow");
        assert_eq!(
            (t.speed, t.damage, t.max_life, t.sensor_radius),
            (16.0, 8.0, 2.5, 0.3)
        );
        assert_eq!(t.faction, Faction::Enemy);
        assert_eq!(t.gravity, 0.0);
    }

    #[test]
    fn test_template_bad_values_fall_back_with_warnings() {
        let mut warnings = Vec::new();
        let t = ProjectileTemplate::from_config(
            &config(&[
                ("id", "x"),
                ("speed", "-3"),
                ("damage", "NaN"),
                ("faction", "martian"),
            ]),
            &mut warnings,
        )
        .unwrap();
        assert_eq!(t.speed, 16.0);
        assert_eq!(t.damage, 8.0);
        assert_eq!(t.faction, Faction::Enemy);
        assert_eq!(warnings.len(), 3, "{warnings:?}");
        assert!(
            ProjectileTemplate::from_config(&config(&[("speed", "3")]), &mut warnings).is_err()
        );
    }

    #[test]
    fn test_straight_launch_points_at_the_target() {
        let v = launch_velocity(Vec3::ZERO, Vec3::new(0.0, 0.0, 10.0), 16.0, 0.0);
        assert!((v - Vec3::new(0.0, 0.0, 16.0)).length() < 1e-4);
    }

    /// Integrar o arco devolvido passa no alvo (a meia-distância de um passo).
    #[test]
    fn test_ballistic_launch_hits_the_target() {
        let (origin, target, speed, g) = (Vec3::ZERO, Vec3::new(12.0, 1.5, 5.0), 20.0, 9.8);
        let v0 = launch_velocity(origin, target, speed, g);
        assert!((v0.length() - speed).abs() < 1e-3);
        let flat = Vec3::new(target.x, 0.0, target.z).length();
        let t = flat / Vec3::new(v0.x, 0.0, v0.z).length();
        let at = origin + v0 * t - Vec3::Y * 0.5 * g * t * t;
        assert!(at.distance(target) < 0.05, "{at} vs {target}");
    }

    #[test]
    fn test_ballistic_out_of_range_goes_45_degrees() {
        let v = launch_velocity(Vec3::ZERO, Vec3::new(500.0, 0.0, 0.0), 10.0, 9.8);
        assert!((v.x - v.y).abs() < 1e-3 && v.x > 0.0);
    }

    fn app_with(template: ProjectileTemplate) -> App {
        let mut app = App::new();
        // As faíscas de impacto (`particles::spawn_burst`) criam mesh/material.
        app.init_resource::<Time>()
            .init_resource::<Assets<Mesh>>()
            .init_resource::<Assets<StandardMaterial>>()
            .add_message::<crate::feedback::PlayerHurt>()
            .add_message::<crate::feedback::DamageNumberEvent>()
            .add_message::<crate::feedback::AttackAlert>()
            .add_message::<ScriptToast>()
            .add_message::<crate::ambient::SfxEvent>()
            .init_resource::<ProjectileQueue>()
            .insert_resource(ProjectileTemplates(HashMap::from([(
                template.id.clone(),
                template,
            )])))
            .add_systems(Update, (spawn_projectiles, step_projectiles).chain());
        app
    }

    fn template(faction: Faction) -> ProjectileTemplate {
        ProjectileTemplate {
            id: "bolt".into(),
            speed: 20.0,
            damage: 7.0,
            max_life: 3.0,
            sensor_radius: 0.3,
            faction,
            gravity: 0.0,
            color: [1.0, 1.0, 1.0],
            size: 0.1,
        }
    }

    /// Avança `frames` frames de 16 ms (sem `TimePlugin`: o relógio é manual).
    fn run_frames(app: &mut App, frames: usize) {
        for _ in 0..frames {
            app.world_mut()
                .resource_mut::<Time>()
                .advance_by(std::time::Duration::from_millis(16));
            app.update();
        }
    }

    fn fire(app: &mut App, origin: Vec3, target: Vec3) {
        app.world_mut()
            .resource_mut::<ProjectileQueue>()
            .requests
            .push(ProjectileRequest {
                template: "bolt".into(),
                origin,
                target,
                shooter: None,
            });
    }

    fn hurts(app: &App) -> usize {
        app.world()
            .resource::<Messages<crate::feedback::PlayerHurt>>()
            .iter_current_update_messages()
            .count()
    }

    #[test]
    fn test_enemy_projectile_hurts_the_hero_once_and_despawns() {
        let mut app = app_with(template(Faction::Enemy));
        app.world_mut().spawn((
            Player::default(),
            Transform::from_xyz(0.0, 0.0, 10.0),
            GlobalTransform::from_xyz(0.0, 0.0, 10.0),
        ));
        fire(
            &mut app,
            Vec3::new(0.0, 1.2, 0.0),
            Vec3::new(0.0, 1.1, 10.0),
        );
        let mut total = 0;
        for _ in 0..60 {
            run_frames(&mut app, 1);
            total += hurts(&app);
        }
        assert_eq!(total, 1, "um só hit");
        assert_eq!(
            app.world_mut()
                .query::<&Projectile>()
                .iter(app.world())
                .count(),
            0,
            "o projétil morre no impacto"
        );
    }

    #[test]
    fn test_player_projectile_damages_creatures_not_the_hero() {
        let mut app = app_with(template(Faction::Player));
        app.world_mut().spawn((
            Player::default(),
            Transform::from_xyz(0.0, 0.0, 5.0),
            GlobalTransform::from_xyz(0.0, 0.0, 5.0),
        ));
        let goblin = app
            .world_mut()
            .spawn((
                Health {
                    current: 30.0,
                    max: 30.0,
                },
                Transform::from_xyz(0.0, 0.0, 10.0),
                GlobalTransform::from_xyz(0.0, 0.0, 10.0),
            ))
            .id();
        fire(
            &mut app,
            Vec3::new(0.0, 1.2, 0.0),
            Vec3::new(0.0, 1.1, 10.0),
        );
        let mut total_hurts = 0;
        for _ in 0..60 {
            run_frames(&mut app, 1);
            total_hurts += hurts(&app);
        }
        assert_eq!(total_hurts, 0, "a facção do herói não o acerta");
        assert_eq!(app.world().get::<Health>(goblin).unwrap().current, 23.0);
    }

    /// Um passo de 4 m (soluço de 250 ms a 16 m/s) atravessava a cápsula de
    /// 1.5 m sem que a posição final lhe tocasse.
    #[test]
    fn test_long_step_does_not_tunnel_through_the_hero() {
        let mut app = app_with(template(Faction::Enemy));
        app.world_mut().spawn((
            Player::default(),
            Transform::from_xyz(0.0, 0.0, 2.0),
            GlobalTransform::from_xyz(0.0, 0.0, 2.0),
        ));
        fire(
            &mut app,
            Vec3::new(0.0, 1.2, 0.0),
            Vec3::new(0.0, 1.1, 10.0),
        );
        app.update();
        app.world_mut()
            .resource_mut::<Time>()
            .advance_by(std::time::Duration::from_millis(250));
        app.update();
        assert_eq!(hurts(&app), 1, "o passo 0→5 m cruza o herói em z=2");
    }

    #[test]
    fn test_swept_distance_hits_mid_step() {
        let feet = Vec3::new(0.0, 0.0, 2.0);
        let (gap, along) = swept_body_distance(Vec3::new(0.0, 1.0, 0.0), Vec3::new(0.0, 1.0, 5.0), feet);
        assert!(gap < 1e-4, "{gap}");
        assert!((along - 0.4).abs() < 1e-4, "{along}");
        let (gap, _) = swept_body_distance(Vec3::new(3.0, 1.0, 0.0), Vec3::new(3.0, 1.0, 5.0), feet);
        assert!((gap - 3.0).abs() < 1e-4, "{gap}");
        // Por cima da cabeça: a distância conta ao topo da cápsula.
        let (gap, _) = swept_body_distance(Vec3::new(0.0, 3.0, 0.0), Vec3::new(0.0, 3.0, 5.0), feet);
        assert!((gap - (3.0 - BODY_HIGH)).abs() < 1e-4, "{gap}");
    }

    /// Uma entidade com vida que dispara um projétil do herói (torre aliada)
    /// saía de dentro da própria cápsula e acertava-se no 1.º frame.
    #[test]
    fn test_shooter_is_never_hit_by_its_own_projectile() {
        let mut app = app_with(template(Faction::Player));
        let turret = app
            .world_mut()
            .spawn((
                Health {
                    current: 30.0,
                    max: 30.0,
                },
                Transform::from_xyz(0.0, 0.0, 0.0),
                GlobalTransform::from_xyz(0.0, 0.0, 0.0),
            ))
            .id();
        app.world_mut()
            .resource_mut::<ProjectileQueue>()
            .requests
            .push(ProjectileRequest {
                template: "bolt".into(),
                origin: Vec3::new(0.0, 1.2, 0.0),
                target: Vec3::new(0.0, 1.1, 10.0),
                shooter: Some(turret),
            });
        run_frames(&mut app, 60);
        assert_eq!(app.world().get::<Health>(turret).unwrap().current, 30.0);
    }

    /// Criaturas FSM sem script morriam de pé a 0 HP (sem cadáver nem XP).
    #[test]
    fn test_projectile_kills_scriptless_creature() {
        let mut app = app_with(ProjectileTemplate {
            damage: 50.0,
            ..template(Faction::Player)
        });
        let wolf = app
            .world_mut()
            .spawn((
                Health {
                    current: 10.0,
                    max: 10.0,
                },
                Transform::from_xyz(0.0, 0.0, 6.0),
                GlobalTransform::from_xyz(0.0, 0.0, 6.0),
            ))
            .id();
        fire(
            &mut app,
            Vec3::new(0.0, 1.2, 0.0),
            Vec3::new(0.0, 1.1, 6.0),
        );
        run_frames(&mut app, 60);
        assert!(app.world().get::<Corpse>(wolf).is_some(), "vira cadáver");
    }

    #[test]
    fn test_projectile_expires_after_max_life() {
        let mut app = app_with(ProjectileTemplate {
            max_life: 0.1,
            ..template(Faction::Neutral)
        });
        fire(&mut app, Vec3::ZERO, Vec3::Z * 100.0);
        run_frames(&mut app, 2);
        assert_eq!(
            app.world_mut()
                .query::<&Projectile>()
                .iter(app.world())
                .count(),
            1
        );
        run_frames(&mut app, 10);
        assert_eq!(
            app.world_mut()
                .query::<&Projectile>()
                .iter(app.world())
                .count(),
            0
        );
    }

    #[test]
    fn test_unknown_template_is_dropped() {
        let mut app = app_with(template(Faction::Enemy));
        app.world_mut()
            .resource_mut::<ProjectileQueue>()
            .requests
            .push(ProjectileRequest {
                template: "nope".into(),
                origin: Vec3::ZERO,
                target: Vec3::Z,
                shooter: None,
            });
        run_frames(&mut app, 1);
        assert_eq!(
            app.world_mut()
                .query::<&Projectile>()
                .iter(app.world())
                .count(),
            0
        );
        assert!(
            app.world()
                .resource::<ProjectileQueue>()
                .requests
                .is_empty()
        );
    }
}
