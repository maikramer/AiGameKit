//! Sword trace — ribbon da lâmina durante o golpe (port melhorado de
//! `VibeGame/src/extras/weapon-trail.ts`).
//!
//! Cada frame com o swing ativo amostra dois pontos world-space da lâmina
//! (hilt e tip, derivados do AABB da arma) num ring buffer; a geometria é um
//! triangle strip de capacidade fixa com fade por idade, unlit aditivo —
//! preto = invisível, por isso o alpha multiplicado no RGB faz a cauda
//! dissolver sem blend alfa. Fora do swing as amostras envelhecem e a cauda
//! desaparece sozinha.
//!
//! Melhorias sobre o original:
//! - **Curva suavizada**: as colunas são sub-divididas [`TRAIL_SUBDIV`]* por
//!   segmento com Catmull-Rom — com amostras escassas (fps baixo, swing
//!   rápido) o ribbon lê como um arco contínuo, não como uma polilinha.
//! - **Afinamento em direção à cauda**: o span base→tip encolhe para a linha
//!   média ao envelhecer (o fio "fecha" em vez de cortar a seco).
//! - **Núcleo quente**: o lado do fio (tip) interpola para branco conforme o
//!   alpha — borda brilhante com halo da cor da arma.
//! - **Boost por swing**: o combate marca finishers/críticos com opacidade
//!   maior ([`TrailWindow::boost`]).

use std::collections::VecDeque;

use bevy::camera::primitives::Aabb;
use bevy::light::NotShadowCaster;
use bevy::mesh::Mesh;
use bevy::prelude::*;

use crate::combat::HeldWeapon;
#[cfg(test)]
use crate::combat::WEAPON_TABLE;

/// Amostras máximas (cada uma é um par base/tip em world space).
pub const TRAIL_SAMPLES: usize = 16;
/// Sub-divisões Catmull-Rom por segmento de amostra (curva suave).
pub const TRAIL_SUBDIV: usize = 4;
/// Colunas do strip = (amostras-1) × subdiv + 1.
pub const TRAIL_COLUMNS: usize = (TRAIL_SAMPLES - 1) * TRAIL_SUBDIV + 1;
/// Segundos até uma amostra expirar.
pub const TRAIL_LIFETIME: f32 = 0.2;
/// Só amostra quando a tip andou isto (m) — evita blob parado.
pub const TRAIL_MIN_DISTANCE: f32 = 0.02;
/// Overshoot da tip para lá da borda do AABB (lê como fio).
pub const BLADE_EXTEND: f32 = 0.12;
/// Base a esta fração do centro→tip (não ancorar no grip: o ribbon abria em
/// leque e escondia o personagem).
pub const BLADE_INSET: f32 = 0.45;
/// Alpha relativo do lado da hilt (o fio domina o look).
pub const HILT_ALPHA: f32 = 0.35;
/// O span encolhe até esta fração na cauda (afinamento).
pub const TAIL_WIDTH: f32 = 0.5;
/// Quanto o lado do fio desliza para branco no pico do alpha.
pub const EDGE_WHITE: f32 = 0.45;

/// Janela ativa do trail (s). Ligada no início de cada swing pela engine de
/// combate; o envelhecimento corre sempre, a amostragem só dentro da janela.
#[derive(Debug, Clone, Resource, Default)]
pub struct TrailWindow {
    pub left: f32,
    /// Opacidade extra deste swing (1 = normal; finisher/crítico > 1).
    pub boost: f32,
}

/// Cor + opacidade por arma (índice em [`WEAPON_TABLE`]) — TRAIL_BY_WEAPON.
pub fn trail_style(weapon_idx: usize) -> ([f32; 3], f32) {
    match weapon_idx {
        1 => ([1.0, 0.69, 0.415], 0.85), // machado
        2 => ([0.56, 0.94, 1.0], 0.85),  // lança
        _ => ([0.737, 0.847, 1.0], 0.9), // espada
    }
}

/// (base, tip) locais da lâmina a partir do AABB local da arma: o eixo mais
/// longo é a lâmina; tip = centro + eixo·metade·(1+extend); base a
/// [`BLADE_INSET`] do caminho centro→tip.
pub fn blade_from_aabb(min: Vec3, max: Vec3) -> (Vec3, Vec3) {
    let half = (max - min) / 2.0;
    let center = (max + min) / 2.0;
    let axis = if half.x >= half.y && half.x >= half.z {
        Vec3::X
    } else if half.y >= half.z {
        Vec3::Y
    } else {
        Vec3::Z
    };
    let reach = half.dot(axis.abs()) * (1.0 + BLADE_EXTEND);
    let tip = center + axis * reach;
    let base = center + axis * (reach * BLADE_INSET);
    (base, tip)
}

/// Interpolação Catmull-Rom (t em 0..1 entre `p1` e `p2`) — a curva do arco.
pub fn catmull_rom(p0: Vec3, p1: Vec3, p2: Vec3, p3: Vec3, t: f32) -> Vec3 {
    let t2 = t * t;
    let t3 = t2 * t;
    0.5 * ((2.0 * p1)
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)
}

/// Alpha com ease-out quadrático sobre a idade — a cauda vive mais brilhante
/// no início e morre suave no fim.
pub fn trail_alpha(age: f32, lifetime: f32, opacity: f32) -> f32 {
    let k = (1.0 - age / lifetime).clamp(0.0, 1.0);
    k * k * opacity
}

#[derive(Debug, Clone, Copy)]
struct TrailSample {
    base: Vec3,
    tip: Vec3,
    age: f32,
}

/// Estado do ribbon: entidade do mesh + amostras + cache dos endpoints por
/// arma (o AABB só é procurável depois do GLB chegar).
#[derive(Resource)]
pub struct WeaponTrailState {
    entity: Option<Entity>,
    mesh: Option<Handle<Mesh>>,
    samples: VecDeque<TrailSample>,
    /// Endpoints locais em cache para (entidade da arma, índice de arma).
    blade: Option<(Vec3, Vec3)>,
    blade_key: Option<(Entity, usize)>,
    style: ([f32; 3], f32),
}

impl Default for WeaponTrailState {
    fn default() -> Self {
        Self {
            entity: None,
            mesh: None,
            samples: VecDeque::with_capacity(TRAIL_SAMPLES + 1),
            blade: None,
            blade_key: None,
            style: trail_style(0),
        }
    }
}

impl WeaponTrailState {
    /// Escreve posições/cores do strip: as colunas são sub-amostradas com
    /// Catmull-Rom entre amostras; colunas em falta duplicam a última posição
    /// (quads degenerados, zero área). Cada coluna tem 2 verts (hilt, fio).
    fn build_strip(&self, opacity_boost: f32) -> (Vec<[f32; 3]>, Vec<[f32; 4]>) {
        let (color, opacity) = self.style;
        let opacity = opacity * opacity_boost.max(0.5);
        let n = self.samples.len();
        let mut positions = Vec::with_capacity(TRAIL_COLUMNS * 2);
        let mut colors = Vec::with_capacity(TRAIL_COLUMNS * 2);
        let sample = |i: usize| -> TrailSample {
            // Clampa às pontas (para a Catmull-Rom nos extremos).
            let clamped = i.min(n.saturating_sub(1));
            self.samples[clamped]
        };
        // Progresso 0 (fio novo) → 1 (cauda) para taper/alpha globais.
        let total_columns = (n.saturating_sub(1) * TRAIL_SUBDIV).max(1);
        {
            let mut emit = |base: Vec3, tip: Vec3, age: f32, progress: f32| {
                let a = trail_alpha(age, TRAIL_LIFETIME, opacity);
                let mid = (base + tip) / 2.0;
                let width = 1.0 - (1.0 - TAIL_WIDTH) * progress;
                let base = mid + (base - mid) * width;
                let tip = mid + (tip - mid) * width;
                positions.push(base.to_array());
                positions.push(tip.to_array());
                // Aditivo: fade = multiplicar o RGB pelo alpha (preto some).
                // O fio desliza para branco no pico — borda quente com halo.
                let edge = [
                    color[0] + (1.0 - color[0]) * (EDGE_WHITE * a),
                    color[1] + (1.0 - color[1]) * (EDGE_WHITE * a),
                    color[2] + (1.0 - color[2]) * (EDGE_WHITE * a),
                ];
                colors.push([
                    color[0] * a * HILT_ALPHA,
                    color[1] * a * HILT_ALPHA,
                    color[2] * a * HILT_ALPHA,
                    1.0,
                ]);
                colors.push([edge[0] * a, edge[1] * a, edge[2] * a, 1.0]);
            };
            for i in 0..n.saturating_sub(1) {
                let (p0, p1, p2, p3) = (
                    sample(i.saturating_sub(1)),
                    sample(i),
                    sample(i + 1),
                    sample(i + 2),
                );
                for k in 0..TRAIL_SUBDIV {
                    let t = k as f32 / TRAIL_SUBDIV as f32;
                    let progress = (i * TRAIL_SUBDIV + k) as f32 / total_columns as f32;
                    emit(
                        catmull_rom(p0.base, p1.base, p2.base, p3.base, t),
                        catmull_rom(p0.tip, p1.tip, p2.tip, p3.tip, t),
                        p1.age + (p2.age - p1.age) * t,
                        progress,
                    );
                }
            }
            if n > 0 {
                let last = sample(n - 1);
                emit(last.base, last.tip, last.age, 1.0);
            }
        }
        // Preenche o resto com a última coluna (degenerado/invisível).
        while positions.len() < TRAIL_COLUMNS * 2 {
            let base = positions
                .get(positions.len().saturating_sub(2))
                .copied()
                .unwrap_or([0.0; 3]);
            let tip = positions.last().copied().unwrap_or([0.0; 3]);
            positions.push(base);
            positions.push(tip);
            colors.push([0.0, 0.0, 0.0, 1.0]);
            colors.push([0.0, 0.0, 0.0, 1.0]);
        }
        (positions, colors)
    }
}

/// Índices estáticos do strip ([`TRAIL_COLUMNS`]-1 quads × 2 triângulos).
fn strip_indices() -> Vec<u32> {
    let mut indices = Vec::with_capacity((TRAIL_COLUMNS - 1) * 6);
    for j in 0..TRAIL_COLUMNS - 1 {
        let b0 = (j * 2) as u32;
        let t0 = b0 + 1;
        let b1 = b0 + 2;
        let t1 = b0 + 3;
        indices.extend_from_slice(&[b0, t0, t1, b0, t1, b1]);
    }
    indices
}

/// Ciclo do ribbon: amostra a lâmina da arma atual dentro da janela de swing,
/// envelhece as amostras e reescreve o mesh.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn weapon_trail_system(
    time: Res<Time>,
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    held: Option<Res<HeldWeapon>>,
    mut window: ResMut<TrailWindow>,
    mut state: ResMut<WeaponTrailState>,
    weapons: Query<&GlobalTransform>,
    bounds: Query<(&GlobalTransform, &Aabb)>,
    children: Query<&Children>,
) {
    let dt = time.delta_secs();
    window.left = (window.left - dt).max(0.0);
    for sample in &mut state.samples {
        sample.age += dt;
    }
    while state
        .samples
        .back()
        .is_some_and(|s| s.age >= TRAIL_LIFETIME)
    {
        state.samples.pop_back();
    }

    let Some(held) = held else { return };
    let Some(weapon_entity) = held.current else {
        state.samples.clear();
        return;
    };

    // Cache dos endpoints por (entidade, arma): mudou de arma ([V]) → refazer.
    let key = (weapon_entity, held.idx);
    if state.blade_key != Some(key) {
        state.blade_key = Some(key);
        state.blade = None;
        state.samples.clear();
        state.style = trail_style(held.idx);
    }
    if state.blade.is_none() {
        state.blade = compute_blade(weapon_entity, &weapons, &bounds, &children);
    }

    // Amostragem (janela ativa + arma com transform + blade conhecida).
    if window.left > 0.0
        && let (Ok(global), Some((base_local, tip_local))) =
            (weapons.get(weapon_entity), state.blade)
    {
        let affine = global.affine();
        let base = affine.transform_point(base_local);
        let tip = affine.transform_point(tip_local);
        let moved = state
            .samples
            .front()
            .is_none_or(|s| s.tip.distance(tip) >= TRAIL_MIN_DISTANCE);
        if moved {
            state.samples.push_front(TrailSample {
                base,
                tip,
                age: 0.0,
            });
            while state.samples.len() > TRAIL_SAMPLES {
                state.samples.pop_back();
            }
        }
    }

    // Entidade do mesh (lazy) + materiais.
    if state.entity.is_none() {
        let mesh_handle = meshes.add(empty_trail_mesh());
        let material = materials.add(StandardMaterial {
            base_color: Color::WHITE,
            unlit: true,
            alpha_mode: AlphaMode::Add,
            ..Default::default()
        });
        let entity = commands
            .spawn((
                Transform::IDENTITY,
                Visibility::Inherited,
                Mesh3d(mesh_handle.clone()),
                MeshMaterial3d(material),
                NotShadowCaster,
                Name::new("fx:sword-trail"),
            ))
            .id();
        state.entity = Some(entity);
        state.mesh = Some(mesh_handle);
    }
    let Some(handle) = state.mesh.as_ref() else {
        return;
    };
    if let Some(mut mesh) = meshes.get_mut(handle) {
        let (positions, colors) = state.build_strip(window.boost);
        mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
        mesh.insert_attribute(Mesh::ATTRIBUTE_COLOR, colors);
    }
}

/// AABB local da arma agregado dos descendentes com mesh → endpoints da
/// lâmina (mesma estratégia do `bladeEndpoints` do VibeGame). `None` enquanto
/// o GLB não chegou.
fn compute_blade(
    root: Entity,
    transforms: &Query<&GlobalTransform>,
    bounds: &Query<(&GlobalTransform, &Aabb)>,
    children: &Query<&Children>,
) -> Option<(Vec3, Vec3)> {
    // Reverse-transform dos AABBs dos filhos para o espaço local da raiz.
    let root_global = transforms.get(root).ok()?;
    let inverse = root_global.affine().inverse();
    let mut min = Vec3::splat(f32::INFINITY);
    let mut max = Vec3::splat(f32::NEG_INFINITY);
    let mut found = false;
    fn walk(
        entity: Entity,
        inverse: &bevy::math::Affine3A,
        min: &mut Vec3,
        max: &mut Vec3,
        found: &mut bool,
        bounds: &Query<(&GlobalTransform, &Aabb)>,
        children: &Query<&Children>,
    ) {
        if let Ok((global, aabb)) = bounds.get(entity) {
            let local = *inverse * global.affine();
            for corner_x in [aabb.min().x, aabb.max().x] {
                for corner_y in [aabb.min().y, aabb.max().y] {
                    for corner_z in [aabb.min().z, aabb.max().z] {
                        let point = local.transform_point(Vec3::new(corner_x, corner_y, corner_z));
                        *min = min.min(point);
                        *max = max.max(point);
                        *found = true;
                    }
                }
            }
        }
        for child in children.get(entity).into_iter().flatten() {
            walk(*child, inverse, min, max, found, bounds, children);
        }
    }
    walk(
        root, &inverse, &mut min, &mut max, &mut found, bounds, children,
    );
    found.then(|| blade_from_aabb(min, max))
}

/// Mesh do strip com capacidade fixa: atributos preenchidos a zero + índices
/// estáticos (nunca realoca — igual ao contrato dos billboards de partícula).
/// Sem emissive: o fade é o vertex color a ir a preto (invisível em aditivo) —
/// um emissive uniforme brilharia mesmo nas colunas degeneradas.
fn empty_trail_mesh() -> Mesh {
    use bevy::asset::RenderAssetUsages;
    let mut mesh = Mesh::new(
        bevy::render::mesh::PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(
        Mesh::ATTRIBUTE_POSITION,
        vec![[0.0f32; 3]; TRAIL_COLUMNS * 2],
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_COLOR, vec![[0.0f32; 4]; TRAIL_COLUMNS * 2]);
    mesh.insert_attribute(
        Mesh::ATTRIBUTE_NORMAL,
        vec![[0.0f32, 1.0, 0.0]; TRAIL_COLUMNS * 2],
    );
    mesh.insert_indices(bevy::mesh::Indices::U32(strip_indices()));
    mesh
}

pub struct TrailPlugin;

impl bevy::app::Plugin for TrailPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<TrailWindow>()
            .init_resource::<WeaponTrailState>()
            .add_systems(bevy::app::Update, weapon_trail_system);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_blade_from_aabb_picks_longest_axis() {
        // Espada "deitada" ao longo de Y (1×4×1): tip para cima com 12 %
        // de overshoot, base a 45 % do caminho.
        let (base, tip) = blade_from_aabb(Vec3::new(-0.5, -2.0, -0.5), Vec3::new(0.5, 2.0, 0.5));
        assert!(tip.y > base.y, "tip no sentido do eixo longo");
        assert!((tip.y - (2.0 * 1.12)).abs() < 1e-4, "tip {tip:?}");
        assert!(
            (base.y - (2.0 * 1.12 * BLADE_INSET)).abs() < 1e-4,
            "base {base:?}"
        );
        assert_eq!(tip.x, 0.0);
        // Espada ao longo de X: tip passa a borda com overshoot, no plano
        // do centro.
        let (_, tip) = blade_from_aabb(Vec3::new(-3.0, 0.0, 0.0), Vec3::new(3.0, 0.2, 0.2));
        assert!(tip.x > 3.0, "tip {tip:?}");
        assert!((tip.y - 0.1).abs() < 1e-4, "tip no centro do AABB: {tip:?}");
    }

    #[test]
    fn test_trail_style_matches_weapon_table() {
        assert_eq!(WEAPON_TABLE.len(), 3);
        let (sword, sword_a) = trail_style(0);
        let (axe, _) = trail_style(1);
        let (spear, _) = trail_style(2);
        assert!((sword[2] - 1.0).abs() < 0.05, "espada azulada");
        assert!(axe[0] > axe[2], "machado laranja");
        assert!(spear[1] > 0.9, "lança ciano");
        assert!(sword_a > 0.8);
    }

    #[test]
    fn test_trail_alpha_eases_out_quadratically() {
        assert!((trail_alpha(0.0, TRAIL_LIFETIME, 0.9) - 0.9).abs() < 1e-5);
        let mid = trail_alpha(TRAIL_LIFETIME / 2.0, TRAIL_LIFETIME, 1.0);
        assert!((mid - 0.25).abs() < 1e-4, "quadrático: metade = 0.25");
        assert_eq!(trail_alpha(TRAIL_LIFETIME, TRAIL_LIFETIME, 1.0), 0.0);
        // Além da vida: clamp (nunca negativo).
        assert_eq!(trail_alpha(TRAIL_LIFETIME * 3.0, TRAIL_LIFETIME, 1.0), 0.0);
    }

    #[test]
    fn test_catmull_rom_passes_through_control_points() {
        let (p0, p1, p2, p3) = (Vec3::ZERO, Vec3::X, Vec3::X + Vec3::Y, Vec3::Y * 2.0);
        assert!(catmull_rom(p0, p1, p2, p3, 0.0).distance(p1) < 1e-5);
        assert!(catmull_rom(p0, p1, p2, p3, 1.0).distance(p2) < 1e-5);
        // E o ponto médio fica entre os controles (curva, não linha).
        let mid = catmull_rom(p0, p1, p2, p3, 0.5);
        assert!(
            mid.x > 1.0 && mid.x < 1.5 && mid.y > 0.0 && mid.y < 0.5,
            "{mid:?}"
        );
    }

    #[test]
    fn test_build_strip_geometry_and_fade() {
        let mut state = WeaponTrailState::default();
        // Três amostras num arco: duas colunas de amostra + subdivs.
        for i in 0..3 {
            state.samples.push_back(TrailSample {
                base: Vec3::new(i as f32, 0.0, 0.0),
                tip: Vec3::new(i as f32, 1.0, 0.0),
                age: i as f32 * 0.05,
            });
        }
        let (positions, colors) = state.build_strip(1.0);
        assert_eq!(positions.len(), TRAIL_COLUMNS * 2, "capacidade fixa");
        assert_eq!(colors.len(), positions.len());
        // A coluna mais recente (primeiro par) está na pose da amostra 0,
        // com largura cheia.
        let first_base = positions[0];
        assert!(
            (first_base[0] - 0.0).abs() < 0.2,
            "base nova {first_base:?}"
        );
        // Alpha da cauda: o último par tem cor ~preta (invisível em aditivo).
        let tail = colors[colors.len() - 2];
        assert!(tail[0] < 0.05, "cauda esvanece: {tail:?}");
        // Com boost, a ponta nova fica mais brilhante que sem boost.
        let (pos2, col2) = state.build_strip(1.6);
        let _ = pos2;
        assert!(col2[1][0] >= colors[1][0], "boost aumenta o brilho do fio");
    }
}
