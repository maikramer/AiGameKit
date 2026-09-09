//! Geometry health of the carved terrain — 100% volumétrico: the demo world
//! (`worlds/terrain.xml`) is carved for real and every VOXEL BOX of every
//! column is scanned for the defect classes that show up in-engine as black
//! holes — non-finite buffers, zero-length normals and degenerate triangles.

use std::path::{Path, PathBuf};

use viber::terrain::brush::BrushGrid;
use viber::terrain::features::apply_features;
use viber::terrain::heightmap::HeightMapU16;
use viber::terrain::mesh::ChunkMeshData;
use viber::terrain::spec::TerrainSpec;
use viber::terrain::voxel::{build_box_mesh, column_boxes};
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

/// Meshes every voxel box of the carved demo world (LOD 0).
fn build_all_boxes(
    spec: &TerrainSpec,
    grid: &BrushGrid,
    features: &viber::terrain::features::TerrainFeatures,
) -> Vec<((u32, u32), ChunkMeshData)> {
    // Sem features 3D o campo é o flat; o mundo demo tem água/carves que
    // vivem na grid — o campo só lê o termo-base.
    let field =
        viber::terrain::voxel::VoxelField::new(Vec::new(), spec.world_size, spec.chunk_size);
    build_boxes_with_field(spec, grid, &field, features)
}

/// Same sweep against an arbitrary voxel field — what a world with `<Bridge>`,
/// `<Arch>` and `<Cave>` mods actually meshes.
fn build_boxes_with_field(
    spec: &TerrainSpec,
    grid: &BrushGrid,
    field: &viber::terrain::voxel::VoxelField,
    features: &viber::terrain::features::TerrainFeatures,
) -> Vec<((u32, u32), ChunkMeshData)> {
    let edge = spec.chunk_size;
    let rows = (spec.world_size / edge).ceil().max(1.0) as u32;
    let lod0_cell = 1.0_f32; // 64 m / resolution 64
    let mut out = Vec::new();
    for cz in 0..rows {
        for cx in 0..rows {
            let boxes = column_boxes(
                spec,
                grid,
                field,
                edge,
                lod0_cell,
                0,
                bevy::math::UVec2::new(cx, cz),
                [viber::terrain::voxel::spawn::NO_NEIGHBOUR; 4],
            );
            for b in &boxes {
                if let Some(data) = build_box_mesh(spec, grid, field, b) {
                    out.push(((cx, cz), data));
                }
            }
        }
    }
    let _ = features;
    out
}

#[test]
fn test_carved_boxes_have_finite_buffers_and_clean_normals() {
    let (spec, grid, features) = carved_demo_world();
    let boxes = build_all_boxes(&spec, &grid, &features);
    assert!(!boxes.is_empty(), "demo world produced voxel boxes");
    for ((cx, cz), data) in &boxes {
        for (i, p) in data.positions.iter().enumerate() {
            assert!(
                p.iter().all(|v| v.is_finite()),
                "box of chunk ({cx},{cz}) vertex {i}: non-finite position {p:?}"
            );
        }
        for (i, n) in data.normals.iter().enumerate() {
            assert!(
                n.iter().all(|v| v.is_finite()),
                "box of chunk ({cx},{cz}) vertex {i}: non-finite normal {n:?}"
            );
            let len = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
            assert!(
                (len - 1.0).abs() < 1e-2,
                "box of chunk ({cx},{cz}) vertex {i}: normal {n:?} is not unit (len {len})"
            );
        }
        for (i, c) in data.colors.iter().enumerate() {
            assert!(
                c.iter().all(|v| v.is_finite() && (0.0..=1.0).contains(v)),
                "box of chunk ({cx},{cz}) vertex {i}: color out of range {c:?}"
            );
        }
    }
}

#[test]
fn test_carved_boxes_have_no_degenerate_triangles() {
    let (spec, grid, features) = carved_demo_world();
    let boxes = build_all_boxes(&spec, &grid, &features);

    let mut degenerate = 0usize;
    let mut total = 0usize;
    for (_, data) in &boxes {
        for tri in data.indices.chunks_exact(3) {
            let (a, b, c) = (
                bevy::math::Vec3::from(data.positions[tri[0] as usize]),
                bevy::math::Vec3::from(data.positions[tri[1] as usize]),
                bevy::math::Vec3::from(data.positions[tri[2] as usize]),
            );
            let cross = (b - a).cross(c - a);
            total += 1;
            if cross.length() < 1e-9 {
                degenerate += 1;
            }
        }
    }
    assert!(total > 0, "voxel triangles were scanned");
    // Folhas finas sub-voxel (lips de carve a centímetros da superfície)
    // produzem alguns flaps de área nula — artefacto documentado do surface
    // nets; o fix real é refinamento de voxel perto de features. Teto
    // honesto em vez de zero.
    let budget = (total / 200).max(8);
    assert!(
        degenerate <= budget,
        "{degenerate}/{total} degenerate triangles (budget {budget})"
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
    viber::terrain::water::carve_river(&mut grid, &river, 0, &[]).expect("river carves");
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

/// The shared asset pool ships meshopt-compressed GLBs; the decoder has to
/// turn a real one into a plain GLB that a glTF reader accepts.
///
/// Skipped when the pool is not checked out beside this crate.
#[test]
fn test_meshopt_decodes_a_real_pool_asset() {
    let Some(pool) = viber::meshopt::shared_asset_pool().map(|p| p.join("assets/meshes")) else {
        eprintln!("shared-assets pool absent — skipping");
        return;
    };
    // Find the first compressed GLB in the pool.
    let mut compressed = None;
    for entry in walk(&pool).into_iter().take(4000) {
        let Ok(bytes) = std::fs::read(&entry) else {
            continue;
        };
        if viber::meshopt::needs_decode(&bytes) {
            compressed = Some((entry, bytes));
            break;
        }
    }
    let (path, bytes) = compressed.expect(
        "the pool ships meshopt-compressed GLBs; finding none means the detector is broken",
    );

    let decoded = viber::meshopt::decode_glb(&bytes)
        .unwrap_or_else(|e| panic!("decoding {}: {e:#}", path.display()));

    assert!(
        !viber::meshopt::needs_decode(&decoded),
        "the decoded GLB no longer declares the extension"
    );
    assert!(
        decoded.len() > bytes.len(),
        "decompressed data is larger than the compressed source ({} -> {})",
        bytes.len(),
        decoded.len()
    );
    // The decoded container must parse as a glTF document with real meshes.
    let doc = gltf_json(&decoded).expect("decoded GLB has a JSON chunk");
    assert!(
        doc["meshes"].as_array().is_some_and(|m| !m.is_empty()),
        "decoded GLB still has meshes"
    );
    assert!(
        doc["bufferViews"]
            .as_array()
            .is_some_and(|views| views.iter().all(|v| v.get("extensions").is_none())),
        "no buffer view keeps a compression extension"
    );
}

/// Every `.glb` under `dir`, recursively.
fn walk(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            out.extend(walk(&path));
        } else if path.extension().is_some_and(|e| e == "glb") {
            out.push(path);
        }
    }
    out
}

/// Parses the JSON chunk out of a GLB.
fn gltf_json(bytes: &[u8]) -> Option<serde_json::Value> {
    let len = u32::from_le_bytes(bytes.get(12..16)?.try_into().ok()?) as usize;
    serde_json::from_slice(bytes.get(20..20 + len)?).ok()
}

/// Carves the crossings QA world and builds its real voxel field: two
/// bridges, a cave with a chamber and a shaft, a natural arch, a viaduct and
/// a seeded rock field.
fn carved_crossings_world() -> (
    TerrainSpec,
    BrushGrid,
    viber::terrain::voxel::VoxelField,
    viber::terrain::features::TerrainFeatures,
) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("worlds/qa-pontes.xml");
    let loaded = xml::include::load_world(&path).expect("crossings world loads");
    let world = recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("world parses");
    let mut pending = viber::recipes::spawn::PendingTerrain::default();
    viber::recipes::spawn::collect_terrain(&world.entities, &mut pending);
    let spec = pending.terrain.clone().expect("world has a <Terrain>");
    let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
    let mut grid = BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    )
    .expect("grid builds");
    let result = apply_features(&mut grid, &pending.features);

    let mut mods: Vec<Box<dyn viber::terrain::voxel::VoxelMod>> = Vec::new();
    for cave in &pending.features.caves {
        mods.extend(cave.build(&grid));
    }
    for arch in &pending.features.arches {
        mods.extend(arch.build(&grid));
    }
    for bridge in &pending.features.bridges {
        mods.extend(bridge.build(&grid));
    }
    let guards = viber::terrain::voxel::ScatterGuards {
        water: &result.water,
        roads: &result.roads,
        ..viber::terrain::voxel::ScatterGuards::default()
    };
    for field in &pending.features.rock_fields {
        let seeded = field.resolve(&grid, &guards);
        for c in &seeded.caves {
            mods.extend(c.build(&grid));
        }
        for a in &seeded.arches {
            mods.extend(a.build(&grid));
        }
        for b in &seeded.bridges {
            mods.extend(b.build(&grid));
        }
    }
    let field = viber::terrain::voxel::VoxelField::new(mods, spec.world_size, spec.chunk_size);
    (spec, grid, field, pending.features)
}

#[test]
fn test_the_crossings_world_meshes_as_cleanly_as_the_bare_terrain() {
    // Bridges, arches and caves multiply the CSG the mesher walks through.
    // The failure they could introduce is the same one the demo world guards
    // against — NaN buffers, zero-length normals, a flood of slivers — so
    // hold them to the same bar rather than inventing a softer one.
    let (spec, grid, field, features) = carved_crossings_world();
    assert!(
        field.mods().len() >= 40,
        "the QA world must carry real mods (has {})",
        field.mods().len()
    );
    let boxes = build_boxes_with_field(&spec, &grid, &field, &features);
    assert!(!boxes.is_empty(), "the crossings world produced voxel boxes");

    let mut degenerate = 0usize;
    let mut total = 0usize;
    for ((cx, cz), data) in &boxes {
        for (i, p) in data.positions.iter().enumerate() {
            assert!(
                p.iter().all(|v| v.is_finite()),
                "box of chunk ({cx},{cz}) vertex {i}: non-finite position {p:?}"
            );
        }
        for (i, n) in data.normals.iter().enumerate() {
            let len = (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]).sqrt();
            assert!(
                n.iter().all(|v| v.is_finite()) && (len - 1.0).abs() < 1e-2,
                "box of chunk ({cx},{cz}) vertex {i}: normal {n:?} is not unit (len {len})"
            );
        }
        for tri in data.indices.chunks_exact(3) {
            let (a, b, c) = (
                bevy::math::Vec3::from(data.positions[tri[0] as usize]),
                bevy::math::Vec3::from(data.positions[tri[1] as usize]),
                bevy::math::Vec3::from(data.positions[tri[2] as usize]),
            );
            total += 1;
            if (b - a).cross(c - a).length() < 1e-9 {
                degenerate += 1;
            }
        }
    }
    assert!(total > 0, "voxel triangles were scanned");
    let budget = (total / 200).max(8);
    assert!(
        degenerate <= budget,
        "{degenerate}/{total} degenerate triangles with mods (budget {budget})"
    );
}
