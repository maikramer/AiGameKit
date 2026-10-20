//! FX de impacto de combate: o "peso" físico que falta ao acertar num
//! inimigo. Dois efeitos curtos e legíveis:
//!
//! - **[`HitRecoil`]** — squash-and-stretch na escala da raiz do inimigo: no
//!   frame do golpe ele comprime em Y e estica em XZ, recuperando com uma
//!   curva quadrática. Na raiz (não nos filhos do GLB) porque as animações
//!   esqueléticas e o swap de LOD vivem na subárvore; escala não briga com
//!   rotação/facing nem com o knockback (que move a translation).
//! - **[`ImpactRing`]** — anel fino no chão (unlit) que abre pouco e
//!   desvanece. Marca zona de perigo e o slam/bomba; não é um aro gordo.

use crate::profiler::{Group, timed};
use bevy::light::{NotShadowCaster, NotShadowReceiver};
use bevy::prelude::*;
use bevy::shape::Torus;

/// Duração do recoil (s) — um flinch curto, não um squash de desenho animado.
pub const RECOIL_DURATION: f32 = 0.14;
/// Compressão em Y no pico do recoil (fração da escala base).
pub const RECOIL_SQUASH_Y: f32 = 0.08;
/// Esticamento em XZ no pico.
pub const RECOIL_STRETCH_XZ: f32 = 0.04;
/// Duração do anel (s).
pub const RING_DURATION: f32 = 0.34;
/// O anel nasce já perto do raio final — abre um pouco, não varre o chão.
pub const RING_START_FRAC: f32 = 0.78;
/// Alpha de nascimento do anel.
pub const RING_START_ALPHA: f32 = 0.42;
/// Tubo do torus unitário (major = 1). A escala da entidade é o raio em metros,
/// por isso o tubo também escala: 1,6 cm a 1 m, ~8 cm num slam de 5 m.
pub const RING_TUBE: f32 = 0.016;

// ── recoil (squash-and-stretch por golpe) ───────────────────────────────

/// Stagger visual do inimigo atingido. `base_scale` tem de ser a escala SEM
/// recoil — quem insere deve reutilizar a de um recoil em curso (query
/// `Option<&HitRecoil>`), senão um re-hit a meio compounding a deformação.
#[derive(Debug, Clone, Component)]
pub struct HitRecoil {
    pub elapsed: f32,
    pub duration: f32,
    pub base_scale: Vec3,
}

impl HitRecoil {
    pub fn new(base_scale: Vec3) -> Self {
        Self {
            elapsed: 0.0,
            duration: RECOIL_DURATION,
            base_scale,
        }
    }
}

/// Fração de squash no instante `k` (0..1 da duração): 1 no impacto, 0 no
/// fim, com saída quadrática (recupera rápido e assenta suave).
pub fn recoil_pulse(k: f32) -> f32 {
    let k = k.clamp(0.0, 1.0);
    (1.0 - k) * (1.0 - k)
}

/// Escala deformada para uma base e pulso `p` (0..1).
pub fn recoil_scale(base: Vec3, p: f32) -> Vec3 {
    Vec3::new(
        base.x * (1.0 + RECOIL_STRETCH_XZ * p),
        base.y * (1.0 - RECOIL_SQUASH_Y * p),
        base.z * (1.0 + RECOIL_STRETCH_XZ * p),
    )
}

#[allow(clippy::type_complexity)]
pub fn hit_recoil_system(
    time: Res<Time>,
    mut recoils: Query<(Entity, &mut HitRecoil, &mut Transform)>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut recoil, mut transform) in &mut recoils {
        recoil.elapsed += dt;
        if recoil.elapsed >= recoil.duration {
            transform.scale = recoil.base_scale;
            commands.entity(entity).remove::<HitRecoil>();
            continue;
        }
        let p = recoil_pulse(recoil.elapsed / recoil.duration);
        transform.scale = recoil_scale(recoil.base_scale, p);
    }
}

// ── anel de choque (onda no chão) ───────────────────────────────────────

/// Anel de choque em expansão. O material é clonado por anel (o alpha anima
/// por instância).
#[derive(Debug, Clone, Component)]
pub struct ImpactRing {
    pub elapsed: f32,
    pub duration: f32,
    pub max_radius: f32,
    pub start_alpha: f32,
    pub material: Handle<StandardMaterial>,
}

/// Progresso do raio no instante `k` (0..1): ease-out cúbico — arranca
/// rápido (ler a onda no frame do golpe) e desacelera a parar.
pub fn ring_radius_progress(k: f32) -> f32 {
    let k = k.clamp(0.0, 1.0);
    1.0 - (1.0 - k).powi(3)
}

/// Torus unitário partilhado por todos os anéis de choque.
const IMPACT_RING_MESH: Handle<Mesh> =
    bevy::asset::uuid_handle!("6f0d3c52-9a41-4b7e-8d3a-2c1e5f7b9a10");

/// Torus unitário do anel: major 1, tubo [`RING_TUBE`].
///
/// `Torus::new(inner, outer)` mede o BURACO e o diâmetro exterior. O antigo
/// `Torus::new(0.045, 1.0)` dava minor ≈ major — um donut quase sólido, que
/// à escala do golpe lia como um aro gordo no chão.
pub fn impact_ring_torus() -> Torus {
    Torus {
        minor_radius: RING_TUBE,
        major_radius: 1.0,
    }
}

/// Spawna um anel no chão em `position` (pés + ~8 cm — o torus é plano no
/// XZ; em declive moderado pode tangenciar o chão). Desvanece em
/// [`RING_DURATION`].
pub fn spawn_impact_ring(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    position: Vec3,
    max_radius: f32,
    color: Color,
) {
    // Mesh PARTILHADA: o torus é unitário (a escala é o raio), portanto
    // todos os anéis servem-se do mesmo asset. O material continua por anel
    // (o alpha de cada um desvanece à sua cadência).
    if meshes.get(&IMPACT_RING_MESH).is_none() {
        let _ = meshes.insert(&IMPACT_RING_MESH, impact_ring_torus().into());
    }
    let mesh = IMPACT_RING_MESH;
    let material = materials.add(StandardMaterial {
        base_color: color.with_alpha(RING_START_ALPHA),
        unlit: true,
        alpha_mode: AlphaMode::Blend,
        ..StandardMaterial::default()
    });
    commands.spawn((
        Transform::from_translation(position),
        Visibility::Inherited,
        Mesh3d(mesh),
        MeshMaterial3d(material.clone()),
        NotShadowCaster,
        NotShadowReceiver,
        ImpactRing {
            elapsed: 0.0,
            duration: RING_DURATION,
            max_radius,
            start_alpha: RING_START_ALPHA,
            material,
        },
        Name::new("fx:impact-ring"),
    ));
}

#[allow(clippy::type_complexity)]
pub fn impact_ring_system(
    time: Res<Time>,
    mut rings: Query<(Entity, &mut ImpactRing, &mut Transform)>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut ring, mut transform) in &mut rings {
        ring.elapsed += dt;
        let k = ring.elapsed / ring.duration;
        if k >= 1.0 {
            commands.entity(entity).despawn();
            continue;
        }
        let start = ring.max_radius * RING_START_FRAC;
        let radius = start + (ring.max_radius - start) * ring_radius_progress(k);
        transform.scale = Vec3::splat(radius);
        if let Some(mut material) = materials.get_mut(&ring.material) {
            let alpha = ring.start_alpha * (1.0 - k);
            if material.base_color.alpha() != alpha {
                material.base_color = material.base_color.with_alpha(alpha);
            }
        }
    }
}

/// Liga os FX de impacto (sistemas de update; quem insere os componentes é
/// o combate/skills).
pub struct ImpactFxPlugin;

impl bevy::app::Plugin for ImpactFxPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.add_systems(
            bevy::app::Update,
            (
                timed(Group::Fx, hit_recoil_system),
                timed(Group::Fx, impact_ring_system),
            ),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_recoil_pulse_starts_full_ends_zero() {
        assert!((recoil_pulse(0.0) - 1.0).abs() < 1e-6, "pico no impacto");
        assert!(recoil_pulse(1.0).abs() < 1e-6, "zero no fim");
        assert!(recoil_pulse(1.5).abs() < 1e-6, "clamp além do fim");
        assert!(recoil_pulse(-0.5) - 1.0 < 1e-6, "clamp antes do início");
        // Monótona decrescente (recuperação suave, sem oscilar).
        let mut prev = recoil_pulse(0.0);
        for i in 1..=10 {
            let k = i as f32 / 10.0;
            let v = recoil_pulse(k);
            assert!(v <= prev + 1e-6, "pulse desceu? k={k} v={v} prev={prev}");
            prev = v;
        }
    }

    #[test]
    fn test_recoil_scale_squashes_y_stretches_xz() {
        let base = Vec3::splat(1.3); // spawner aplica escalas aleatórias
        let p = recoil_pulse(0.0);
        let s = recoil_scale(base, p);
        assert!(s.y < base.y, "comprime em Y no pico");
        assert!(s.x > base.x && s.z > base.z, "estica em XZ no pico");
        // No fim volta EXATAMENTE à base (sem erro acumulado entre hits).
        let done = recoil_scale(base, recoil_pulse(1.0));
        assert!(done.distance(base) < 1e-6);
    }

    #[test]
    fn test_impact_ring_torus_is_a_thin_band() {
        let torus = impact_ring_torus();
        assert!(
            (torus.major_radius - 1.0).abs() < 1e-4,
            "major unitário: a escala da entidade é o raio"
        );
        assert!(
            torus.minor_radius / torus.major_radius < 0.04,
            "tubo fino — `Torus::new(0.045, 1.0)` era um donut sólido"
        );
    }

    #[test]
    fn test_ring_progress_eases_out() {
        assert!(ring_radius_progress(0.0).abs() < 1e-6);
        assert!((ring_radius_progress(1.0) - 1.0).abs() < 1e-6);
        // Arranca rápido: no k=0.25 já andou mais de metade do caminho até
        // k=0.5 do que de k=0.5 a k=0.75.
        let a = ring_radius_progress(0.25);
        let b = ring_radius_progress(0.5) - ring_radius_progress(0.25);
        let c = ring_radius_progress(0.75) - ring_radius_progress(0.5);
        assert!(a > b && b > c, "ease-out: {a} {b} {c}");
    }
}
