//! O pipeline canónico de colocação — UM contrato de gates partilhado por
//! TODOS os consumidores de [`compute_placements`]: o runtime do jogo
//! (`spawner::instantiate_spawn_groups`), o `viber bake` (execução e
//! `verify_static`) e a ferramenta `viber tool scatter`. Já houve uma
//! divergência real entre cópias (a closure de amostragem existia inline no
//! spawner e replicada no bake; o naming de instâncias divergiu
//! `inst-grp-N` vs `inst-{id}` e produziu grupos com nomes trocados). Este
//! módulo existe para não haver segunda: qualquer gate novo (`avoid-*`)
//! muda AQUI e propaga aos quatro consumos.

use bevy::math::{Vec2, Vec3};

use crate::recipes::StaticSpawnerSpec;
use crate::spawner::{
    PlacedInstance, PlacementStats, SpawnOccupancy, TerrainSample, compute_placements,
};
use crate::terrain::cliffs::CliffMask;
use crate::terrain::runtime::TerrainRuntime;

/// Raio de "perto de água" e a folga da máscara de cliff para um spec.
///
/// A margem de cliff em metros reais: a máscara é amostrada com folga =
/// margem autoral + meia-largura da pegada na maior escala possível —
/// "a 2 m do cliff" conta a partir do SÍTIO onde o prop vai caber.
pub fn sample_params(spec: &StaticSpawnerSpec) -> (f32, f32) {
    (
        spec.near_water_radius,
        spec.cliff_margin.max(0.0)
            + spec.footprint_radius.max(0.0) * spec.scale_min.max(spec.scale_max).max(0.0),
    )
}

/// A amostragem ÚNICA — o contrato de gates (superfície renderizada,
/// gradiente voxel, linha de água EXATA, ribbon de estrada, máscara de cliff
/// dilatada, laje fina):
///
/// - Altura da SUPERFÍCIE renderizada (lattice LOD0): entre vértices o mesh
///   só desenha cordas lineares, e a amostra analítica flutuava acima delas
///   nas cristas.
/// - Gradiente do CAMPO voxel no ponto (fiel a cliffs/caves/arch — a matriz
///   3×3 da grelha crua era cega aos mods e inclinava props para paredes que
///   a grelha nem via).
/// - `near_water` é a banda EXATA em torno da linha de água
///   (`distance_to_waterline`, a mesma métrica do splatter) — as 4 sondas
///   antigas liam o carve radius (que passa da lâmina vários metros) e punham
///   "margem" em encosta seca.
/// - `roof`: laje fina por cima de vazio (banda de arco, brow de overhang
///   raso) — o gate rejeita; raízes não nascem em pedra suspensa. Em mundo
///   flat é sempre false, sem custo.
pub fn terrain_sample(
    runtime: &TerrainRuntime,
    cliffs: Option<&CliffMask>,
    x: f32,
    z: f32,
    near_radius: f32,
    cliff_clearance: f32,
) -> TerrainSample {
    let height = runtime.sample_mesh_surface(x, z);
    TerrainSample {
        height,
        normal: runtime
            .voxel
            .gradient(&runtime.base(), Vec3::new(x, height, z), 0.5),
        water: runtime.in_water(x, z),
        water_surface: runtime.water_surface_at(x, z),
        near_water: runtime
            .water
            .iter()
            .any(|body| body.distance_to_waterline(Vec2::new(x, z)).abs() <= near_radius),
        road: runtime.on_road(x, z),
        cliff: cliffs.is_some_and(|mask| mask.is_cliff_within(Vec2::new(x, z), cliff_clearance)),
        roof: runtime.has_thin_roof(x, z),
    }
}

/// O pipeline completo para um grupo: params + amostragem + colocação.
/// Exclusões/ocupação pré-registadas são honradas (registar ANTES de chamar).
pub fn place(
    spec: &StaticSpawnerSpec,
    occupancy: &mut SpawnOccupancy,
    runtime: &TerrainRuntime,
    cliffs: Option<&CliffMask>,
) -> (Vec<PlacedInstance>, PlacementStats) {
    let (near_radius, cliff_clearance) = sample_params(spec);
    let mut sample =
        |x: f32, z: f32| terrain_sample(runtime, cliffs, x, z, near_radius, cliff_clearance);
    compute_placements(spec, occupancy, &mut sample)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::recipes::StaticSpawnerSpec;
    use crate::terrain::brush::BrushGrid;
    use crate::terrain::delta::DeltaGrid;
    use crate::terrain::paint::PaintGrid;
    use crate::terrain::spec::TerrainSpec;
    use crate::terrain::voxel::VoxelField;
    use std::sync::Arc;

    /// Runtime flat 32×32 sem água/estradas — o mínimo para o pipeline correr.
    fn flat_runtime() -> TerrainRuntime {
        let n = 33usize;
        let raw: Vec<u16> = (0..n * n)
            .map(|i| ((10.0f32 / 50.0) * 65535.0).round() as u16)
            .collect();
        let grid = BrushGrid::new(raw, n, n, 16.0, 50.0, 1.0).expect("grid");
        TerrainRuntime {
            spec: TerrainSpec {
                world_size: 32.0,
                ..Default::default()
            },
            grid: Arc::new(grid),
            water: Vec::new(),
            roads: Vec::new(),
            pads: Vec::new(),
            voxel: Arc::new(VoxelField::default()),
            deltas: Arc::new(DeltaGrid::default()),
            paint: Arc::new(PaintGrid::default()),
            splat_tuning: Default::default(),
        }
    }

    fn spec(count: u32, seed: u64) -> StaticSpawnerSpec {
        let xml = format!(
            r#"<StaticSpawner count="{count}" seed="{seed}" region-min="-12 0 -12" region-max="12 0 12" random-yaw="1"><GameObject><GLTFLoader url="/assets/meshes/test/cube_lod0.glb" /></GameObject></StaticSpawner>"#
        );
        let nodes = crate::xml::parse_fragment(&xml).expect("fragment parseia");
        let parsed = crate::recipes::parse_world(&[], &nodes).expect("spec parseia");
        parsed
            .entities
            .iter()
            .find_map(|entity| match &entity.kind {
                crate::recipes::EntityKind::StaticSpawner { spec } => Some(spec.clone()),
                _ => None,
            })
            .expect("o fragmento tem um StaticSpawner")
    }

    /// Determinismo do pipeline: mesma seed + mesmo terreno ⇒ as MESMAS
    /// colocações, por quantas vezes `place` correr (o contrato do bake).
    #[test]
    fn place_is_deterministic_for_the_same_seed_and_terrain() {
        let runtime = flat_runtime();
        let spec = spec(12, 42);
        let mut occupancy = SpawnOccupancy::new();
        let (first, _) = place(&spec, &mut occupancy, &runtime, None);
        let mut fresh = SpawnOccupancy::new();
        let (second, _) = place(&spec, &mut fresh, &runtime, None);
        assert_eq!(first.len(), 12, "todas as instâncias cabem no mundo flat");
        assert_eq!(first.len(), second.len());
        for (a, b) in first.iter().zip(second.iter()) {
            assert_eq!(a.position, b.position, "mesma seed ⇒ mesma posição");
            assert_eq!(a.rotation, b.rotation);
            assert_eq!(a.scale, b.scale);
        }
    }

    /// Exclusões registadas ANTES do place produzem colocações disjuntas —
    /// o contrato "exclusões primeiro, sempre honradas" dos 4 consumidores.
    #[test]
    fn registered_exclusions_steer_placements_away() {
        let runtime = flat_runtime();
        // Um disco no centro que cobre quase toda a região: as instâncias
        // têm de nascer todas FORA dele (o spawner empurra para a borda).
        let mut occupancy = SpawnOccupancy::new();
        occupancy.register(0.0, 0.0, 10.0);
        let (instances, _) = place(&spec(16, 7), &mut occupancy, &runtime, None);
        assert!(!instances.is_empty(), "a borda da região ainda cabe props");
        for inst in &instances {
            let d2 = inst.position.x * inst.position.x + inst.position.z * inst.position.z;
            let r = 10.0 + inst.scale.max_element() * 0.5;
            assert!(
                d2 > r * r,
                "instância em ({}, {}) dentro do disco de exclusão r=10",
                inst.position.x,
                inst.position.z
            );
        }
    }

    /// `sample_params`: a folga de cliff soma a pegada na MAIOR escala.
    #[test]
    fn sample_params_scales_cliff_clearance_by_the_largest_scale() {
        let mut s = spec(1, 1);
        s.cliff_margin = 2.0;
        s.footprint_radius = 0.5;
        s.scale_min = 0.5;
        s.scale_max = 1.5;
        let (near, clearance) = sample_params(&s);
        // `near` vem do spec (o perfil do parser pode preencher defaults) —
        // o que este teste crava é a fórmula da folga de cliff.
        assert_eq!(near, s.near_water_radius);
        assert!((clearance - 2.75).abs() < 1e-5, "{clearance}");
    }
}
