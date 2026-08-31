//! Geometry health of the carved terrain: the demo world (`worlds/terrain.xml`)
//! is carved for real and every chunk mesh is scanned for the defect classes
//! that show up in-engine as black holes — non-finite buffers, zero-length
//! normals and downward-facing top-surface normals.

use std::path::Path;

use viber::terrain::brush::BrushGrid;
use viber::terrain::features::apply_features;
use viber::terrain::heightmap::HeightMapU16;
use viber::terrain::mesh::{ChunkMeshData, ChunkMeshParams, TintParams, build_chunk_mesh};
use viber::terrain::spec::TerrainSpec;
use viber::{recipes, xml};

/// Carves the demo world and returns its grid + spec.
fn carved_demo_world() -> (
    TerrainSpec,
    BrushGrid,
    viber::terrain::features::TerrainFeatures,
) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("worlds/terrain.xml");
    let loaded = xml::include::load_world(&path).expect("demo world loads");
    let world = recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("demo world parses");
    let mut pending = viber::recipes::spawn::PendingTerrain::default();
    viber::recipes::spawn::collect_terrain(&world.entities, &mut pending);
    let spec = pending.terrain.clone().expect("demo world has a <Terrain>");
    let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
    let mut grid = BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    )
    .expect("grid builds");
    let features = pending.features.clone();
    apply_features(&mut grid, &features);
    (spec, grid, features)
}

/// LOD-0 step, mirroring `runtime::lod0_step`.
fn lod0_step(spec: &TerrainSpec) -> usize {
    let ideal = spec.chunk_size / spec.resolution.max(1) as f32;
    let step = ideal.round().max(1.0) as usize;
    if (spec.chunk_size / step as f32).abs().fract() > 1e-3 {
        1
    } else {
        step
    }
}

fn build_all_chunks(spec: &TerrainSpec, grid: &BrushGrid) -> Vec<((u32, u32), ChunkMeshData)> {
    let step = lod0_step(spec);
    let segments = (spec.chunk_size / step as f32).round() as usize;
    let edge = segments as f32 * step as f32;
    let rows = (spec.world_size / edge).ceil().max(1.0) as u32;
    let half = spec.world_size * 0.5;
    let tint = TintParams::from(&spec.tint);
    let mut out = Vec::new();
    for cz in 0..rows {
        for cx in 0..rows {
            let params = ChunkMeshParams {
                origin: bevy::math::Vec3::new(
                    -half + cx as f32 * edge,
                    0.0,
                    -half + cz as f32 * edge,
                ),
                size: edge,
                lod_step: step,
                skirt_depth: spec.skirt_depth_meters(),
                normal_epsilon: grid.texel(),
                texture_tile_size: spec.texture_tile_size,
                levels: spec.levels,
                tint: tint.clone(),
                world_size: spec.world_size,
            };
            if let Ok(Some(data)) = build_chunk_mesh(grid, &params) {
                out.push(((cx, cz), data));
            }
        }
    }
    out
}

#[test]
fn test_carved_chunks_have_finite_buffers() {
    let (spec, grid, _) = carved_demo_world();
    let chunks = build_all_chunks(&spec, &grid);
    assert!(!chunks.is_empty(), "demo world produced chunks");
    for ((cx, cz), data) in &chunks {
        for (i, p) in data.positions.iter().enumerate() {
            assert!(
                p.iter().all(|v| v.is_finite()),
                "chunk ({cx},{cz}) vertex {i}: non-finite position {p:?}"
            );
        }
        for (i, n) in data.normals.iter().enumerate() {
            assert!(
                n.iter().all(|v| v.is_finite()),
                "chunk ({cx},{cz}) vertex {i}: non-finite normal {n:?}"
            );
            let len = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
            assert!(
                (len - 1.0).abs() < 1e-2,
                "chunk ({cx},{cz}) vertex {i}: normal {n:?} is not unit (len {len})"
            );
        }
        for (i, c) in data.colors.iter().enumerate() {
            assert!(
                c.iter().all(|v| v.is_finite() && (0.0..=1.0).contains(v)),
                "chunk ({cx},{cz}) vertex {i}: color out of range {c:?}"
            );
        }
    }
}

#[test]
fn test_carved_chunks_have_no_degenerate_or_inverted_top_faces() {
    let (spec, grid, _) = carved_demo_world();
    let chunks = build_all_chunks(&spec, &grid);
    let step = lod0_step(&spec);
    let segments = (spec.chunk_size / step as f32).round() as usize;
    // Top-surface triangles come first; skirt triangles follow.
    let top_indices = segments * segments * 6;

    let mut degenerate = 0usize;
    let mut inverted = 0usize;
    let mut total = 0usize;
    for (_, data) in &chunks {
        for tri in data.indices[..top_indices.min(data.indices.len())].chunks_exact(3) {
            let (a, b, c) = (
                bevy::math::Vec3::from(data.positions[tri[0] as usize]),
                bevy::math::Vec3::from(data.positions[tri[1] as usize]),
                bevy::math::Vec3::from(data.positions[tri[2] as usize]),
            );
            let cross = (b - a).cross(c - a);
            total += 1;
            if cross.length() < 1e-9 {
                degenerate += 1;
            } else if cross.normalize().y <= 0.0 {
                inverted += 1;
            }
        }
    }
    assert!(total > 0, "top-surface triangles were scanned");
    assert_eq!(
        degenerate, 0,
        "{degenerate}/{total} degenerate top triangles"
    );
    assert_eq!(
        inverted, 0,
        "{inverted}/{total} top triangles wind downward"
    );
}

/// The carve must not tear the heightfield. Measures the largest height jump
/// between neighbouring grid texels before and after the river is carved: a
/// carve that steps by many meters over one texel renders as the vertical
/// "fins" that used to line the banks.
#[test]
fn test_river_carve_does_not_tear_the_heightfield() {
    use viber::terrain::water::RiverSpec;

    let spec = TerrainSpec {
        world_size: 512.0,
        max_height: 60.0,
        chunk_size: 64.0,
        levels: 4,
        resolution: 64,
        height_smoothing: 1.0,
        seed: 7,
        ..TerrainSpec::default()
    };
    let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
    let mut grid = BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    )
    .expect("grid builds");

    let before = max_texel_step(&grid, &spec);

    let river = RiverSpec {
        path: vec![
            bevy::math::Vec2::new(150.0, 220.0),
            bevy::math::Vec2::new(170.0, 120.0),
            bevy::math::Vec2::new(150.0, 20.0),
            bevy::math::Vec2::new(175.0, -80.0),
            bevy::math::Vec2::new(150.0, -200.0),
        ],
        width: 14.0,
        depth: 3.4,
        water_offset: 1.0,
        bank_width: 5.5,
        bank_height: 0.9,
        ..RiverSpec::default()
    };
    viber::terrain::water::carve_river(&mut grid, &river, 0).expect("river carves");
    let after = max_texel_step(&grid, &spec);

    // The channel is 3.4 m deep with a ~5.5 m bank and a feathered outer
    // band, so a carved texel step should stay in the same order as the
    // natural terrain's, not explode into a cliff.
    assert!(
        after <= before.max(1.0) * 4.0,
        "river carve tore the field: max texel step {before:.2} m -> {after:.2} m"
    );
}

/// Largest absolute height difference between 4-neighbour grid texels.
fn max_texel_step(grid: &BrushGrid, spec: &TerrainSpec) -> f32 {
    let texel = grid.texel();
    let n = (spec.world_size / texel).round() as i32;
    let half = spec.world_size * 0.5;
    let mut worst = 0.0_f32;
    for iz in 0..n {
        for ix in 0..n {
            let x = -half + ix as f32 * texel;
            let z = -half + iz as f32 * texel;
            let h = grid.sample(x, z);
            let dx = (grid.sample(x + texel, z) - h).abs();
            let dz = (grid.sample(x, z + texel) - h).abs();
            worst = worst.max(dx).max(dz);
        }
    }
    worst
}

/// End-to-end: the whole demo world (pads → lake → river → roads) must carve
/// without tearing. Guards the class of bug where a carver stamps its design
/// surface onto texels far outside its own footprint.
#[test]
fn test_full_demo_carve_does_not_tear_the_heightfield() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("worlds/terrain.xml");
    let loaded = xml::include::load_world(&path).expect("demo world loads");
    let world = recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("demo world parses");
    let mut pending = viber::recipes::spawn::PendingTerrain::default();
    viber::recipes::spawn::collect_terrain(&world.entities, &mut pending);
    let spec = pending.terrain.clone().expect("demo world has a <Terrain>");

    let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
    let mut grid = BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    )
    .expect("grid builds");
    let before = max_texel_step(&grid, &spec);
    apply_features(&mut grid, &pending.features);
    let after = max_texel_step(&grid, &spec);

    assert!(
        after <= before.max(1.0) * 6.0,
        "demo carve tore the field: max texel step {before:.2} m -> {after:.2} m"
    );
}
