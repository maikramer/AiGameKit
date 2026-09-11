//! Water FX — splash bursts and expanding surface ripples for anything that
//! walks into, through or out of a water body.
//!
//! The water surface itself is a static mesh with a specialized shader (see
//! [`super::water_material`]): the Bevy 0.19 slot-1 storage promotion never
//! re-uploads a custom material uniform, so **the shader cannot be told where
//! the hero is**. The wake therefore lives in the ECS instead: every contact
//! spawns short-lived entities that the normal transparent pass draws over
//! the mirror.
//!
//! Three contacts, three effects:
//!
//! * **Enter** (dry → submerged past [`WADE_ENTER_DEPTH`]; back out only
//!   below [`WADE_EXIT_DEPTH`], so the shoreline does not chatter) — a
//!   `splash` burst scaled by impact speed and submersion, plus one wide
//!   ripple.
//! * **Wade** (moving while submerged) — a `wade` burst and a ripple every
//!   [`RIPPLE_INTERVAL`] of *distance travelled*, so the cadence follows the
//!   stride instead of the frame rate, and stopping stops the wake.
//! * **Exit** — a small `splash` at the last wet position.
//!
//! Everything is driven off [`super::runtime::TerrainRuntime`]'s registry
//! ([`super::water::WaterBody::surface_y_at`]), so it works for lakes and
//! rivers alike. A caixa XZ de cada corpo ([`body_bounds`]) filtra os
//! nadadores em terra antes da query: sem ela, cada um dos ~170 nadadores
//! pagava o `nearest_on_path` do rio — O(estações) — todos os frames.

use bevy::asset::RenderAssetUsages;
use bevy::light::NotShadowCaster;
use bevy::prelude::*;
use bevy::render::mesh::{Indices, PrimitiveTopology};

use super::runtime::TerrainRuntime;
use crate::particles::spawn_burst;
use crate::recipes::ParticleSpec;

/// Submersion (meters of water over the entity's feet) that flips *dry →
/// wet*. Deliberately above [`WADE_EXIT_DEPTH`]: without the hysteresis a
/// hero walking the shoreline crosses a single threshold back and forth on
/// terrain-sampling noise and machine-guns entry splashes.
pub const WADE_ENTER_DEPTH: f32 = 0.22;
/// Submersion below which a wet entity counts as *out* again.
pub const WADE_EXIT_DEPTH: f32 = 0.10;
/// Distance walked (meters) between two wake ripples.
pub const RIPPLE_INTERVAL: f32 = 0.9;
/// Speed (m/s) below which a submerged entity stops making a wake.
pub const WAKE_MIN_SPEED: f32 = 0.35;
/// Ripple lifetime (s).
pub const RIPPLE_LIFE: f32 = 1.5;
/// Ripple radius at birth / at death (meters).
pub const RIPPLE_START_RADIUS: f32 = 0.35;
pub const RIPPLE_END_RADIUS: f32 = 2.6;
/// Ripple lift over the mirror (meters) — enough to sort in front of the
/// water surface without reading as a floating disc.
pub const RIPPLE_LIFT: f32 = 0.045;
/// Concurrent ripples allowed; beyond this the oldest are left to expire and
/// new ones are skipped (a crowd wading a ford must not flood the pass).
pub const MAX_RIPPLES: usize = 48;
/// Ring segments of the ripple mesh.
const RIPPLE_SEGMENTS: usize = 40;

// Invariantes das constantes acima, verificadas em COMPILE TIME (um `assert!`
// sobre consts em teste é sempre verdade e o clippy rejeita-o).
const _: () = {
    assert!(RIPPLE_START_RADIUS > 0.0);
    assert!(RIPPLE_START_RADIUS < RIPPLE_END_RADIUS);
    // O anel de entrada nasce com um multiplicador de 1.35 — mesmo assim tem
    // de ficar um efeito local, não um disco a cobrir a lagoa.
    assert!(RIPPLE_END_RADIUS * 1.35 < 4.0);
    // Histerese: sair exige menos submersão do que entrar.
    assert!(WADE_EXIT_DEPTH > 0.0);
    assert!(WADE_EXIT_DEPTH < WADE_ENTER_DEPTH);
    assert!(WADE_ENTER_DEPTH < 0.5);
};

// ── componentes ─────────────────────────────────────────────────────────

/// Anything that should splash. Inserted by [`tag_swimmers`] on the hero and
/// on every AI creature; also fine to add by hand.
#[derive(Debug, Clone, Component, Default)]
pub struct WaterContact {
    /// Submerged on the previous tick.
    pub wet: bool,
    /// Distance walked since the last ripple (meters).
    pub since_ripple: f32,
    /// Previous world position (for the speed estimate).
    pub last_pos: Option<Vec3>,
}

/// One expanding surface ring.
#[derive(Debug, Clone, Component)]
pub struct Ripple {
    pub age: f32,
    pub life: f32,
    pub start_radius: f32,
    pub end_radius: f32,
}

/// Shared ring mesh + a cheap way to make ripple materials.
#[derive(Resource)]
pub struct RippleAssets {
    pub mesh: Handle<Mesh>,
    /// Materiais dos anéis RECICLADOS. Cada anel anima o SEU alpha (o fade é
    /// por idade, ver `ripple_system`), portanto não podem partilhar um — mas
    /// podem voltar a usar-se quando o anel anterior morre. Antes disto cada
    /// anel nascia com um `materials.add` novo e o material ficava residente
    /// para sempre (o Bevy não faz GC de assets): com a chuva eram ~14
    /// anéis/s a acumular sem limite.
    pub materials: Vec<Handle<StandardMaterial>>,
}

// ── lógica pura (testada) ───────────────────────────────────────────────

/// Ripple radius at age `t` — fast at birth, easing out (a real ring loses
/// speed as it spreads), so `t=0` is the start radius and `t=life` the end.
pub fn ripple_radius(age: f32, life: f32, start: f32, end: f32) -> f32 {
    let t = (age / life.max(1e-4)).clamp(0.0, 1.0);
    let eased = 1.0 - (1.0 - t) * (1.0 - t);
    start + (end - start) * eased
}

/// Ripple alpha at age `t`: a short fade-in so the ring is not born at full
/// strength, then a linear fade to nothing.
pub fn ripple_alpha(age: f32, life: f32) -> f32 {
    let t = (age / life.max(1e-4)).clamp(0.0, 1.0);
    let rise = (t / 0.12).clamp(0.0, 1.0);
    rise * (1.0 - t) * (1.0 - t)
}

/// Particle count of an entry splash: the vertical impact speed sets the
/// crown, the submersion scales it down so stepping into ankle-deep water is
/// a plop and jumping into a bowl is a proper splash.
pub fn splash_count(impact_speed: f32, submersion: f32) -> usize {
    let depth_gain = (submersion / 0.8).clamp(0.25, 1.0);
    ((6.0 + impact_speed * 4.5 + submersion * 8.0) * depth_gain).clamp(4.0, 46.0) as usize
}

// ── malha do anel ───────────────────────────────────────────────────────

/// Unit ring (outer radius 1) whose vertex alpha peaks on the crest — the
/// entity's `Transform.scale` is what animates the radius, so one mesh serves
/// every ripple.
pub fn ripple_mesh() -> Mesh {
    // Três anéis: interior transparente → crista opaca → exterior transparente.
    let rings: [(f32, f32); 3] = [(0.70, 0.0), (0.88, 1.0), (1.0, 0.0)];
    let mut positions: Vec<[f32; 3]> = Vec::with_capacity(rings.len() * RIPPLE_SEGMENTS);
    let mut normals: Vec<[f32; 3]> = Vec::with_capacity(rings.len() * RIPPLE_SEGMENTS);
    let mut uvs: Vec<[f32; 2]> = Vec::with_capacity(rings.len() * RIPPLE_SEGMENTS);
    let mut colors: Vec<[f32; 4]> = Vec::with_capacity(rings.len() * RIPPLE_SEGMENTS);
    for (radius, alpha) in rings {
        for i in 0..RIPPLE_SEGMENTS {
            let theta = i as f32 / RIPPLE_SEGMENTS as f32 * std::f32::consts::TAU;
            positions.push([theta.cos() * radius, 0.0, theta.sin() * radius]);
            normals.push([0.0, 1.0, 0.0]);
            uvs.push([theta / std::f32::consts::TAU, radius]);
            colors.push([1.0, 1.0, 1.0, alpha]);
        }
    }
    let seg = RIPPLE_SEGMENTS as u32;
    let mut indices: Vec<u32> = Vec::with_capacity(RIPPLE_SEGMENTS * 12);
    for band in 0..2u32 {
        let a0 = band * seg;
        let b0 = (band + 1) * seg;
        for i in 0..seg {
            let j = (i + 1) % seg;
            indices.extend_from_slice(&[a0 + i, a0 + j, b0 + i, a0 + j, b0 + j, b0 + i]);
        }
    }
    let mut mesh = Mesh::new(
        PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
    mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, normals);
    mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, uvs);
    mesh.insert_attribute(Mesh::ATTRIBUTE_COLOR, colors);
    mesh.insert_indices(Indices::U32(indices));
    mesh
}

fn burst_spec(preset: &str) -> ParticleSpec {
    ParticleSpec {
        preset: preset.to_string(),
        emission_rate: None,
        life: None,
        speed: None,
        size: None,
        color: None,
        shape_radius: None,
        looping: false,
        world_space: false,
    }
}

// ── sistemas ────────────────────────────────────────────────────────────

fn setup_ripple_assets(mut commands: Commands, mut meshes: ResMut<Assets<Mesh>>) {
    commands.insert_resource(RippleAssets {
        mesh: meshes.add(ripple_mesh()),
        materials: Vec::new(),
    });
}

/// Caixa XZ CONSERVADORA de um corpo de água — a broad-phase das queries de
/// água por nadador.
///
/// O `WaterBody` de um RIO resolve TODAS as suas queries
/// (`contains`/`is_near`/`surface_y_at`/`distance_to_waterline`) com
/// `nearest_on_path` sobre o vetor inteiro de estações — O(estações) cada
/// uma. O `water_contact_system` perguntava a CADA nadador por TODOS os
/// corpos: com as ~170 criaturas do `simple-rpg` são dezenas de milhares de
/// testes de segmento por frame, para 99 % de bichos que estão em terra firme.
///
/// Medir a caixa UMA vez por corpo por frame custa `corpos × estações` (≈13 ×
/// estações) e corta as queries ao que interessa. A caixa é deliberadamente
/// GENEROSA — pico do contorno orgânico × reach do espelho nos lagos, maior
/// meia-largura de estação nos rios — para nunca excluir um ponto que o corpo
/// molhe de facto.
fn body_bounds(body: &super::water::WaterBody) -> (Vec2, Vec2) {
    match body.kind {
        super::WaterKind::Lake => {
            let reach = body
                .carve_radius
                .max(body.radius * super::water::CONTOUR_PEAK * body.mirror_reach);
            (body.at - Vec2::splat(reach), body.at + Vec2::splat(reach))
        }
        super::WaterKind::River => {
            if body.stations.is_empty() {
                return (body.at, body.at);
            }
            let half = body
                .half_width
                .iter()
                .copied()
                .fold(body.carve_radius, f32::max);
            let mut min = body.stations[0];
            let mut max = body.stations[0];
            for station in &body.stations {
                min = min.min(*station);
                max = max.max(*station);
            }
            (min - Vec2::splat(half), max + Vec2::splat(half))
        }
    }
}

/// Marca herói e criaturas como candidatos a salpicar (idempotente).
#[allow(clippy::type_complexity)]
fn tag_swimmers(
    mut commands: Commands,
    hero: Query<
        Entity,
        (
            With<crate::player::Player>,
            Without<WaterContact>,
            With<Transform>,
        ),
    >,
    creatures: Query<
        Entity,
        (
            With<crate::ai::EnemyCreature>,
            Without<WaterContact>,
            With<Transform>,
        ),
    >,
) {
    for entity in hero.iter().chain(creatures.iter()) {
        commands.entity(entity).insert(WaterContact::default());
    }
}

/// Deteta entrada/saída/vadeagem e emite os FX correspondentes.
#[allow(clippy::too_many_arguments)]
fn water_contact_system(
    time: Res<Time>,
    terrain: Option<Res<TerrainRuntime>>,
    assets: Option<ResMut<RippleAssets>>,
    mut commands: Commands,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut swimmers: Query<(&GlobalTransform, &mut WaterContact)>,
    ripples: Query<(), With<Ripple>>,
    mut bounds: Local<Vec<(Vec2, Vec2)>>,
) {
    let (Some(terrain), Some(mut assets)) = (terrain, assets) else {
        return;
    };
    let dt = time.delta_secs().max(1e-4);
    let mut budget = MAX_RIPPLES.saturating_sub(ripples.iter().count());
    // Broad-phase: uma caixa por corpo por frame (`body_bounds`), para a query
    // por nadador não pagar o `nearest_on_path` do rio em todos eles.
    bounds.clear();
    bounds.extend(terrain.water.iter().map(body_bounds));
    for (transform, mut contact) in &mut swimmers {
        let pos = transform.translation();
        let previous = contact.last_pos.unwrap_or(pos);
        contact.last_pos = Some(pos);
        let step = pos - previous;
        let planar_speed = Vec2::new(step.x, step.z).length() / dt;
        let fall_speed = (-step.y / dt).max(0.0);

        let p = Vec2::new(pos.x, pos.z);
        let surface = terrain
            .water
            .iter()
            .zip(bounds.iter())
            .filter(|(_, (min, max))| {
                p.x >= min.x && p.x <= max.x && p.y >= min.y && p.y <= max.y
            })
            .filter_map(|(body, _)| body.surface_y_at(p))
            .fold(None::<f32>, |acc, y| Some(acc.map_or(y, |a| a.max(y))));
        let Some(surface_y) = surface else {
            if contact.wet {
                contact.wet = false;
                contact.since_ripple = 0.0;
            }
            continue;
        };
        // `pos.y` é a base da entidade (o pivô dos GLB está nos pés).
        let submersion = surface_y - pos.y;
        let wet = if contact.wet {
            submersion > WADE_EXIT_DEPTH
        } else {
            submersion > WADE_ENTER_DEPTH
        };

        if wet && !contact.wet {
            let at = Vec3::new(pos.x, surface_y, pos.z);
            spawn_burst(
                &mut commands,
                &burst_spec("splash"),
                at,
                splash_count(fall_speed, submersion),
            );
            if budget > 0 {
                spawn_ripple(&mut commands, &mut assets, &mut materials, at, 1.35);
                budget -= 1;
            }
            contact.since_ripple = 0.0;
        } else if !wet && contact.wet {
            let at = Vec3::new(pos.x, surface_y, pos.z);
            spawn_burst(
                &mut commands,
                &burst_spec("splash"),
                at,
                12,
            );
        } else if wet && planar_speed > WAKE_MIN_SPEED {
            contact.since_ripple += planar_speed * dt;
            if contact.since_ripple >= RIPPLE_INTERVAL {
                contact.since_ripple = 0.0;
                let at = Vec3::new(pos.x, surface_y, pos.z);
                spawn_burst(
                    &mut commands,
                    &burst_spec("wade"),
                    at,
                    (4.0 + planar_speed * 2.0) as usize,
                );
                if budget > 0 {
                    spawn_ripple(&mut commands, &mut assets, &mut materials, at, 1.0);
                    budget -= 1;
                }
            }
        }
        contact.wet = wet;
    }
}

/// O material do anel: constante (o fade anima-se no alpha DESTE material,
/// por isso é um por anel — reciclado pelo pool de [`RippleAssets`]).
fn ripple_material() -> StandardMaterial {
    StandardMaterial {
        base_color: Color::srgba(0.92, 0.97, 1.0, 0.0),
        unlit: true,
        alpha_mode: AlphaMode::Blend,
        // A onda e a lâmina de água são ambas transparentes: sem o bias a
        // ordenação por distância troca-as consoante o ângulo da câmara e o
        // anel pisca ao passar por cima do espelho.
        depth_bias: 8.0,
        ..StandardMaterial::default()
    }
}

fn spawn_ripple(
    commands: &mut Commands,
    assets: &mut RippleAssets,
    materials: &mut Assets<StandardMaterial>,
    at: Vec3,
    scale: f32,
) {
    let material = assets
        .materials
        .pop()
        .unwrap_or_else(|| materials.add(ripple_material()));
    commands.spawn((
        Name::new("fx:ripple"),
        Transform::from_translation(Vec3::new(at.x, at.y + RIPPLE_LIFT, at.z))
            .with_scale(Vec3::splat(RIPPLE_START_RADIUS)),
        Visibility::Inherited,
        Mesh3d(assets.mesh.clone()),
        MeshMaterial3d(material),
        NotShadowCaster,
        Ripple {
            age: 0.0,
            life: RIPPLE_LIFE,
            start_radius: RIPPLE_START_RADIUS,
            end_radius: RIPPLE_END_RADIUS * scale,
        },
    ));
}

/// Expande, desvanece e despawna os anéis.
fn ripple_system(
    time: Res<Time>,
    mut commands: Commands,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut assets: ResMut<RippleAssets>,
    mut ripples: Query<(
        Entity,
        &mut Transform,
        &mut Ripple,
        &MeshMaterial3d<StandardMaterial>,
    )>,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut ripple, material) in &mut ripples {
        ripple.age += dt;
        if ripple.age >= ripple.life {
            // O material volta ao pool: o próximo anel herda-o (o alpha é
            // reescrito a partir da idade, o estado anterior não importa).
            assets.materials.push(material.0.clone());
            commands.entity(entity).despawn();
            continue;
        }
        let radius = ripple_radius(
            ripple.age,
            ripple.life,
            ripple.start_radius,
            ripple.end_radius,
        );
        transform.scale = Vec3::splat(radius);
        if let Some(mut material) = materials.get_mut(&material.0) {
            material
                .base_color
                .set_alpha(ripple_alpha(ripple.age, ripple.life) * 0.45);
        }
    }
}

/// Registo dos FX de água (chamado pelo arranque da engine).
pub struct WaterFxPlugin;

impl bevy::app::Plugin for WaterFxPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.add_systems(bevy::app::Startup, setup_ripple_assets)
            .add_systems(
                bevy::app::Update,
                (tag_swimmers, water_contact_system, ripple_system),
            );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ripple_radius_spans_start_to_end_and_eases() {
        let (s, e, life) = (0.35_f32, 2.6_f32, 1.5_f32);
        assert!((ripple_radius(0.0, life, s, e) - s).abs() < 1e-5);
        assert!((ripple_radius(life, life, s, e) - e).abs() < 1e-5);
        assert!(
            (ripple_radius(life * 2.0, life, s, e) - e).abs() < 1e-5,
            "clamped"
        );
        // Ease-out: metade da vida já cobriu mais de metade do raio.
        let half = ripple_radius(life * 0.5, life, s, e);
        assert!(half > s + (e - s) * 0.5, "eases out: {half}");
        // Monótono.
        let mut previous = f32::NEG_INFINITY;
        for i in 0..=20 {
            let r = ripple_radius(life * i as f32 / 20.0, life, s, e);
            assert!(r >= previous, "monotonic at {i}: {r} < {previous}");
            previous = r;
        }
    }

    #[test]
    fn test_ripple_alpha_rises_then_fades_to_zero() {
        let life = 1.5_f32;
        assert!(ripple_alpha(0.0, life) < 1e-6, "born invisible");
        assert!(ripple_alpha(life, life) < 1e-6, "dies invisible");
        assert!(ripple_alpha(life * 2.0, life) < 1e-6, "clamped");
        let peak = ripple_alpha(life * 0.12, life);
        assert!(peak > 0.5, "reaches full strength after the rise: {peak}");
        assert!(
            ripple_alpha(life * 0.8, life) < ripple_alpha(life * 0.4, life),
            "fades out"
        );
    }

    #[test]
    fn test_splash_count_scales_with_impact_depth_and_clamps() {
        assert!(splash_count(0.0, 1.0) >= 4, "walking in still splashes");
        assert!(
            splash_count(4.0, 1.0) > splash_count(1.0, 1.0),
            "impact scales"
        );
        assert!(
            splash_count(2.0, 0.25) < splash_count(2.0, 1.5),
            "ankle-deep is a plop, waist-deep a crown"
        );
        assert!(splash_count(1000.0, 10.0) <= 46, "clamped");
        assert!(splash_count(0.0, 0.0) >= 4, "floor");
    }

    /// O anel tem os três aros e a crista é o único opaco — é isso que dá a
    /// linha de onda em vez de um disco.
    #[test]
    fn test_ripple_mesh_is_a_faded_ring() {
        let mesh = ripple_mesh();
        assert_eq!(mesh.count_vertices(), RIPPLE_SEGMENTS * 3);
        let colors = match mesh
            .attribute(Mesh::ATTRIBUTE_COLOR)
            .expect("vertex colors")
        {
            bevy::render::mesh::VertexAttributeValues::Float32x4(v) => v,
            other => panic!("expected rgba colors, got {other:?}"),
        };
        assert!(colors[0][3] < 1e-6, "inner ring transparent");
        assert!(
            (colors[RIPPLE_SEGMENTS][3] - 1.0).abs() < 1e-6,
            "crest opaque"
        );
        assert!(
            colors[RIPPLE_SEGMENTS * 2][3] < 1e-6,
            "outer ring transparent"
        );
        let indices = mesh.indices().expect("indexed");
        assert_eq!(indices.len(), RIPPLE_SEGMENTS * 12, "two bands");
        assert!(
            indices.iter().all(|i| i < RIPPLE_SEGMENTS * 3),
            "indices in range"
        );
    }
}
