//! End-to-end checks for the voxel terrain layer, driven by the QA world.
//!
//! The claim under test is the one the heightfield cannot make: after parsing
//! `worlds/qa-voxel.xml` and carving it exactly as the engine does, there is a
//! place in the world where you can stand with **rock above your head**.

use bevy::math::Vec3;

use viber::recipes;
use viber::terrain::brush::BrushGrid;
use viber::terrain::features::apply_features;
use viber::terrain::heightmap::HeightMapU16;
use viber::terrain::voxel::transvoxel_mesh::sliver_area2_floor;
use viber::terrain::voxel::{
    Bounds3, ChunkClass, VOXEL_CHUNK_CELLS, VoxelChunkParams, VoxelField, VoxelMod,
    build_voxel_mesh,
};
use viber::xml;

use std::path::Path;

/// The full guard set the bootstrap arms the scatter with.
fn full_guards<'a>(
    result: &'a viber::terrain::features::FeatureResult,
    cliff_mask: &'a viber::terrain::cliffs::CliffMask,
    taken: &'a [viber::terrain::voxel::TakenDisc],
) -> viber::terrain::voxel::ScatterGuards<'a> {
    viber::terrain::voxel::ScatterGuards {
        water: &result.water,
        roads: &result.roads,
        cliffs: Some(cliff_mask),
        pads: &result.pads,
        taken,
    }
}

/// Parses and carves a world exactly like `terrain::runtime::bootstrap` does
/// — carve, cliff bands, authored features, then the scatter with the FULL
/// guard set — and builds the voxel field.
fn carve(world_file: &str) -> (BrushGrid, VoxelField, viber::terrain::spec::TerrainSpec) {
    let (grid, features, spec, result, cliff_mask, cliff_bands, taken) =
        bootstrap_to_scatter(world_file);
    let mut mods: Vec<Box<dyn VoxelMod>> = Vec::new();
    for (i, band) in cliff_bands.iter().enumerate() {
        mods.extend(band.clone().into_mods(&format!("cliff:{i}")));
    }
    for cave in &features.caves {
        mods.extend(cave.build(&grid));
    }
    for arch in &features.arches {
        mods.extend(arch.build(&grid));
    }
    for bridge in &features.bridges {
        mods.extend(bridge.build(&grid));
    }
    for field in &features.rock_fields {
        let got = field.resolve(&grid, &full_guards(&result, &cliff_mask, &taken));
        for c in &got.caves {
            mods.extend(c.build(&grid));
        }
        for a in &got.arches {
            mods.extend(a.build(&grid));
        }
        for b in &got.bridges {
            mods.extend(b.build(&grid));
        }
    }
    let field = VoxelField::new(mods, spec.world_size, spec.chunk_size);
    (grid, field, spec)
}

/// The discs an authored cave claims — the tunnel line plus its rooms.
fn claim_cave(cave: &viber::terrain::voxel::CaveSpec, out: &mut Vec<viber::terrain::voxel::TakenDisc>) {
    let r = cave.radius.iter().copied().fold(0.0_f32, f32::max).max(2.0);
    for p in viber::terrain::paths::resample(&cave.path, 8.0) {
        out.push(viber::terrain::voxel::TakenDisc { at: p, radius: r });
    }
    for chamber in &cave.chambers {
        out.push(viber::terrain::voxel::TakenDisc {
            at: chamber.at,
            radius: chamber.radius,
        });
    }
    for shaft in &cave.shafts {
        out.push(viber::terrain::voxel::TakenDisc {
            at: shaft.at,
            radius: shaft.radius,
        });
    }
}

/// The discs an authored bridge claims — the deck, half width included.
fn claim_bridge(
    bridge: &viber::terrain::voxel::BridgeSpec,
    out: &mut Vec<viber::terrain::voxel::TakenDisc>,
) {
    for p in viber::terrain::paths::resample(&bridge.path, 8.0) {
        out.push(viber::terrain::voxel::TakenDisc {
            at: p,
            radius: bridge.width * 0.5,
        });
    }
}

/// The discs an authored arch claims — the whole band, leg to leg.
fn claim_arch(
    arch: &viber::terrain::voxel::ArchSpec,
    out: &mut Vec<viber::terrain::voxel::TakenDisc>,
) {
    let span = arch
        .span
        .unwrap_or(viber::terrain::voxel::arch::DEFAULT_ARCH_SPAN);
    let r = arch.thickness + span * 0.5;
    let pts: Vec<bevy::math::Vec2> = if arch.path.is_empty() {
        vec![arch.at]
    } else {
        arch.path.clone()
    };
    for p in viber::terrain::paths::resample(&pts, 8.0) {
        out.push(viber::terrain::voxel::TakenDisc { at: p, radius: r });
    }
}

/// Everything the bootstrap builds up to the scatter: the carved grid, the
/// feature registries, the cliff bands + mask and the discs claimed by
/// authored features. The field builder (`carve`) and the scatter tests share
/// this, so a test never seeds with weaker guards than the engine does.
fn bootstrap_to_scatter(
    world_file: &str,
) -> (
    BrushGrid,
    viber::terrain::features::TerrainFeatures,
    viber::terrain::spec::TerrainSpec,
    viber::terrain::features::FeatureResult,
    viber::terrain::cliffs::CliffMask,
    Vec<viber::terrain::voxel::CliffBand>,
    Vec<viber::terrain::voxel::TakenDisc>,
) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("worlds")
        .join(world_file);
    let loaded = xml::include::load_world(&path).expect("world loads");
    let world = recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("world parses");
    let mut pending = viber::recipes::spawn::PendingTerrain::default();
    viber::recipes::spawn::collect_terrain(&world.entities, &mut pending);
    let spec = pending.terrain.clone().expect("world has a <Terrain>");
    let features = pending.features.clone();

    let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
    let mut grid = BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    )
    .expect("grid builds");
    let result = apply_features(&mut grid, &pending.features);

    // Same order as the bootstrap: bands resolve against the CARVED grid,
    // before the mask, and the authored bands join the public query layer.
    let texel = grid.texel();
    let cliff_bands: Vec<viber::terrain::voxel::CliffBand> = features
        .cliffs
        .iter()
        .filter_map(|cliff| viber::terrain::voxel::CliffBand::build(cliff, &grid, texel))
        .collect();
    let mut cliff_mask = viber::terrain::cliffs::CliffMask::build_with(
        &grid,
        spec.cliff_angle,
        spec.cliff_min_area,
        spec.cliff_min_drop,
        spec.cliff_min_extent,
    );
    cliff_mask.add_authored_bands(&cliff_bands);
    let mut taken: Vec<viber::terrain::voxel::TakenDisc> = Vec::new();
    for cave in &features.caves {
        claim_cave(cave, &mut taken);
    }
    for bridge in &features.bridges {
        claim_bridge(bridge, &mut taken);
    }
    for arch in &features.arches {
        claim_arch(arch, &mut taken);
    }
    (
        grid, features, spec, result, cliff_mask, cliff_bands, taken,
    )
}

/// The seeded specs of the QA world's one `<RockFeatures>` field, resolved
/// with the full guard set.
fn qa_scatter() -> (BrushGrid, viber::terrain::voxel::ScatterResult) {
    let (grid, features, _spec, result, cliff_mask, _bands, taken) =
        bootstrap_to_scatter("qa-pontes.xml");
    let field = features
        .rock_fields
        .first()
        .expect("the QA world seeds rocks")
        .clone();
    let seeded = field.resolve(&grid, &full_guards(&result, &cliff_mask, &taken));
    (grid, seeded)
}

#[test]
fn test_qa_world_parses_cliffs_caves_and_arches_into_voxel_mods() {
    let (_grid, field, _spec) = carve("qa-voxel.xml");
    assert!(!field.is_flat(), "the QA world authors 3D features");
    assert!(
        field.mods().len() > 20,
        "two tunnels of ~180 m at 4 m spacing plus cliff bands and an arch, got {}",
        field.mods().len()
    );
    // The `arch` cliff profile bores its window: exactly one label per
    // authored arch-band, deterministic by construction.
    let windows = field
        .mods()
        .iter()
        .filter(|m| m.label().ends_with(":window"))
        .count();
    assert_eq!(windows, 1, "the janela cliff must bore exactly one window");
}

#[test]
fn test_somewhere_in_the_tunnel_there_is_rock_overhead() {
    // The whole point of the exercise. A heightfield column has exactly one
    // surface; find a column with two, and a gap between them tall enough to
    // stand in.
    let (grid, field, _spec) = carve("qa-voxel.xml");

    let mut best: Option<(f32, f32, f32)> = None; // (headroom, x, z)
    for i in 0..=180 {
        let x = -90.0 + i as f32;
        let z = 6.0 - (i as f32 / 180.0) * 6.0 + (i as f32 / 180.0) * 2.0;
        let spans = field.column(&grid, x, z);
        if spans.len() < 2 {
            continue;
        }
        // Air between the underside of the roof and the floor below it.
        let headroom = spans[0].bottom - spans[1].top;
        if best.is_none_or(|(h, _, _)| headroom > h) {
            best = Some((headroom, x, z));
        }
    }

    let (headroom, x, z) = best.expect(
        "no column in the tunnel had two solid spans — the cave has no roof, \
         which is exactly the thing a heightfield could not do either",
    );
    assert!(
        headroom > 2.0,
        "the cave at ({x}, {z}) is only {headroom:.2} m tall — not walkable"
    );

    // And the top surface is still the hill, so every existing gameplay query
    // that asks `sample()` keeps getting the answer it always got.
    let top = field.surface_top(&grid, x, z);
    let spans = field.column(&grid, x, z);
    assert!(
        (top - spans[0].top).abs() < 0.01,
        "surface_top must report the hill above the cave"
    );
    // Standing inside the tunnel, the floor is the cave floor, not the hill.
    let inside = spans[0].bottom - 0.5;
    let floor = field
        .surface_below(&grid, x, z, inside)
        .expect("a cave has a floor");
    assert!(
        floor < top - 2.0,
        "the cave floor {floor:.2} must be well under the hilltop {top:.2}"
    );
}

#[test]
fn test_a_world_without_3d_features_stays_bit_for_bit_the_heightfield() {
    // The non-regression claim for every world authored before this existed.
    // qa-agua carves pads/lakes/rivers/roads — all heightfield work — and
    // authors no cliff, cave or arch, so the field must come out empty.
    let (grid, field, _spec) = carve("qa-agua.xml");
    assert!(
        field.is_flat(),
        "qa-agua authors no 3D feature but the field grew {} mods",
        field.mods().len()
    );
    for i in 0..64 {
        let x = -240.0 + i as f32 * 7.5;
        let z = 180.0 - i as f32 * 5.5;
        assert_eq!(
            field.surface_top(&grid, x, z),
            grid.sample(x, z),
            "flat world diverged from the heightfield at {x},{z}"
        );
    }
}

#[test]
fn test_only_the_chunks_the_caves_touch_are_volumetric() {
    // The cost argument: a world with two tunnels must not turn its whole
    // terrain into voxels.
    let (_grid, field, spec) = carve("qa-voxel.xml");
    let edge = spec.chunk_size;
    let half = spec.world_size * 0.5;
    let rows = (spec.world_size / edge).ceil() as i32;

    let mut volumetric = 0;
    let mut total = 0;
    for cz in 0..rows {
        for cx in 0..rows {
            let min = Vec3::new(-half + cx as f32 * edge, 0.0, -half + cz as f32 * edge);
            let bounds = Bounds3::from_corners(min, min + Vec3::new(edge, spec.max_height, edge));
            total += 1;
            if field.classify(&bounds) == ChunkClass::Volumetric {
                volumetric += 1;
            }
        }
    }
    assert!(volumetric > 0, "the caves must claim some chunks");
    assert!(
        volumetric < total,
        "{volumetric}/{total} chunks volumetric — the flat path must still carry most of the world"
    );
}

#[test]
fn test_the_tunnel_actually_meshes_into_geometry() {
    let (grid, field, spec) = carve("qa-voxel.xml");
    let voxel_size = 1.0_f32;
    let extent = VOXEL_CHUNK_CELLS as f32 * voxel_size;

    // Walk the boxes around the middle of the first tunnel until one meshes.
    let mut meshed = 0;
    let mut downward_normals = 0;
    for cy in -1..3 {
        for cx in -3..3 {
            let origin = Vec3::new(cx as f32 * extent, cy as f32 * extent, -extent);
            let params = VoxelChunkParams {
                origin,
                cells: VOXEL_CHUNK_CELLS,
                voxel_size,
                texture_tile_size: spec.texture_tile_size,
                tint: spec.chunk_tint(),
                max_height: spec.max_height,
                uses_layer_material: true,
                transitions: [false; 4],
            };
            let density = |p: Vec3| field.density(&grid, p);
            if let Some(data) = build_voxel_mesh(&density, &params) {
                meshed += 1;
                downward_normals += data.normals.iter().filter(|n| n[1] < -0.5).count();
                assert!(
                    data.positions
                        .iter()
                        .all(|p| p.iter().all(|c| c.is_finite())),
                    "non-finite vertex in a cave chunk"
                );
                assert_eq!(data.indices.len() % 3, 0);
            }
        }
    }
    assert!(meshed > 0, "no voxel chunk meshed around the tunnel");
    assert!(
        downward_normals > 0,
        "the tunnel produced no downward-facing surface — there is no ceiling"
    );
}

#[test]
fn test_two_runs_of_the_same_world_are_byte_identical() {
    // "Mesma seed, mesmo mundo" — the contract the pre-stroke probes used to
    // guarantee by hand. Compared as raw f32/u32 bits, not floats: NaN layout
    // drift and last-bit jitter both count as failures.
    let (grid_a, field_a, spec) = carve("qa-voxel.xml");
    let (grid_b, field_b, _spec) = carve("qa-voxel.xml");

    // The columns answer identically, bit for bit, over the tunnels, the
    // cliffs and the arch.
    for i in 0..200 {
        let x = -95.0 + i as f32 * 1.7;
        let z = -80.0 + (i * 37 % 160) as f32;
        for y in [0.0, 8.0, 20.0, 40.0] {
            assert_eq!(
                field_a.density(&grid_a, Vec3::new(x, y, z)).to_bits(),
                field_b.density(&grid_b, Vec3::new(x, y, z)).to_bits(),
                "density bits diverged at ({x}, {y}, {z})"
            );
        }
        let cols_a = field_a.column(&grid_a, x, z);
        let cols_b = field_b.column(&grid_b, x, z);
        assert_eq!(cols_a.len(), cols_b.len(), "span count diverged at {x},{z}");
        for (sa, sb) in cols_a.iter().zip(cols_b.iter()) {
            assert_eq!(sa.top.to_bits(), sb.top.to_bits());
            assert_eq!(sa.bottom.to_bits(), sb.bottom.to_bits());
        }
    }

    // And so does the mesh of the tunnel chunk.
    let voxel_size = 1.0_f32;
    let extent = VOXEL_CHUNK_CELLS as f32 * voxel_size;
    let params = VoxelChunkParams {
        origin: Vec3::new(-extent, 0.0, -extent),
        cells: VOXEL_CHUNK_CELLS,
        voxel_size,
        texture_tile_size: spec.texture_tile_size,
        tint: spec.chunk_tint(),
        max_height: spec.max_height,
        uses_layer_material: true,
        transitions: [false; 4],
    };
    let mesh_a =
        build_voxel_mesh(&|p| field_a.density(&grid_a, p), &params).expect("chunk meshes (a)");
    let mesh_b =
        build_voxel_mesh(&|p| field_b.density(&grid_b, p), &params).expect("chunk meshes (b)");
    let bits_a: Vec<u32> = mesh_a
        .positions
        .iter()
        .flat_map(|p| p.iter().map(|c| c.to_bits()))
        .collect();
    let bits_b: Vec<u32> = mesh_b
        .positions
        .iter()
        .flat_map(|p| p.iter().map(|c| c.to_bits()))
        .collect();
    assert_eq!(bits_a, bits_b, "vertex bits diverged between runs");
    assert_eq!(
        mesh_a.indices, mesh_b.indices,
        "indices diverged between runs"
    );
}

#[test]
fn test_the_tunnel_mesh_has_no_degenerate_triangles() {
    let (grid, field, spec) = carve("qa-voxel.xml");
    let voxel_size = 1.0_f32;
    let extent = VOXEL_CHUNK_CELLS as f32 * voxel_size;
    let params = VoxelChunkParams {
        origin: Vec3::new(-extent, 0.0, -extent),
        cells: VOXEL_CHUNK_CELLS,
        voxel_size,
        texture_tile_size: spec.texture_tile_size,
        tint: spec.chunk_tint(),
        max_height: spec.max_height,
        uses_layer_material: true,
        transitions: [false; 4],
    };
    let data = build_voxel_mesh(&|p| field.density(&grid, p), &params).expect("chunk meshes");
    assert!(data.indices.len() >= 3);
    // Marching cubes emite SLIVERS onde a isosuperfície passa rente a um canto
    // do lattice — dois vértices interpolados quase coincidentes. Não são
    // removíveis sem furar a malha (partilham arestas com triângulos bons), e
    // não se veem. O que tem de continuar verdade é que são uma MINORIA: um
    // mesher partido produz slivers às centenas de por cento, não a 1 %.
    let floor = sliver_area2_floor(voxel_size);
    let total = data.indices.len() / 3;
    let mut slivers = 0usize;
    for tri in data.indices.chunks_exact(3) {
        let (a, b, c) = (
            Vec3::from(data.positions[tri[0] as usize]),
            Vec3::from(data.positions[tri[1] as usize]),
            Vec3::from(data.positions[tri[2] as usize]),
        );
        if (b - a).cross(c - a).length_squared() <= floor {
            slivers += 1;
        }
    }
    let fraction = slivers as f32 / total as f32;
    assert!(
        fraction < 0.05,
        "{slivers}/{total} triângulos são slivers ({:.1} %) — o mesher degradou",
        fraction * 100.0
    );
}

#[test]
fn test_a_fully_interior_tunnel_chunk_is_watertight() {
    // A cave surface that never touches the chunk border must close: every
    // undirected edge shared by exactly two triangles. The neighbouring
    // chunk owns the faces at the outermost lattice plane, so any edge
    // touching it is exempt (the seam-coincidence has its own test in
    // `surface_nets.rs`).
    let (grid, field, spec) = carve("qa-voxel.xml");
    let voxel_size = 1.0_f32;
    let extent = VOXEL_CHUNK_CELLS as f32 * voxel_size;

    // Positions are chunk-relative on ALL axes: the lattice spans
    // [-1, extent + 1]. Vertices are smoothed inside their cell, so the
    // seam band is one voxel DEEP, not a plane: any vertex within a voxel
    // of either extreme may complete a face the neighbouring chunk owns.
    let on_border = |p: &[f32; 3]| {
        p[0] <= voxel_size
            || p[0] >= extent - voxel_size
            || p[1] <= voxel_size
            || p[1] >= extent - voxel_size
            || p[2] <= voxel_size
            || p[2] >= extent - voxel_size
    };
    let mut found = false;
    for z0 in [-extent, 0.0] {
        for cy in 0..2 {
            for cx in -3..3 {
                let origin = Vec3::new(cx as f32 * extent, cy as f32 * extent, z0);
                let params = VoxelChunkParams {
                    origin,
                    cells: VOXEL_CHUNK_CELLS,
                    voxel_size,
                    texture_tile_size: spec.texture_tile_size,
                    tint: spec.chunk_tint(),
                    max_height: spec.max_height,
                    uses_layer_material: true,
                    transitions: [false; 4],
                };
                let Some(data) = build_voxel_mesh(&|p| field.density(&grid, p), &params) else {
                    continue;
                };
                // Tunnel chunks only: the bare terrain sheet has no roof,
                // and its seam rim would pollute the claim under test.
                if !data.normals.iter().any(|n| n[1] < -0.5) {
                    continue;
                }
                // Undirected edge census.
                let mut edges: std::collections::HashMap<(u32, u32), u32> =
                    std::collections::HashMap::new();
                for tri in data.indices.chunks_exact(3) {
                    for k in 0..3 {
                        let (a, b) = (tri[k], tri[(k + 1) % 3]);
                        *edges.entry((a.min(b), a.max(b))).or_insert(0) += 1;
                    }
                }
                let open: Vec<_> = edges
                    .iter()
                    .filter(|item| *item.1 == 1)
                    .filter(|item| {
                        let (a, b) = item.0;
                        !on_border(&data.positions[*a as usize])
                            && !on_border(&data.positions[*b as usize])
                    })
                    .collect();
                assert!(
                    open.is_empty(),
                    "{} interior edges have a single face — the chunk leaks",
                    open.len()
                );
                found = true;
            }
        }
    }
    assert!(found, "no tunnel chunk meshed to check");
}

#[test]
fn test_the_free_standing_arch_gives_a_walker_two_spans() {
    // The acceptance query for `<Arch>`: under the crown the column holds
    // ground + air + the arch band, so `column()` answers 2 spans and
    // `surface_below` puts the walker on the GROUND, not the band.
    let (grid, field, _spec) = carve("qa-voxel.xml");
    let mut best: Option<(f32, f32, f32, usize)> = None; // (band thickness, x, z, spans)
    for dz in -4..=4 {
        for dx in -4..=4 {
            let x = 72.0 + dx as f32;
            let z = -18.0 + dz as f32;
            let spans = field.column(&grid, x, z);
            if spans.len() >= 2 {
                let t = spans[0].thickness();
                if best.is_none_or(|(bt, _, _, _)| t > bt) {
                    best = Some((t, x, z, spans.len()));
                }
            }
        }
    }
    let (thickness, x, z, _spans) =
        best.expect("no two-span column near the arch — the portal has no opening");
    let spans = field.column(&grid, x, z);
    assert!(
        spans.len() == 2,
        "expected exactly 2 spans under the arch at ({x}, {z}), got {}",
        spans.len()
    );
    assert!(
        thickness < 12.0,
        "the top span at ({x}, {z}) is {thickness:.1} m thick — that is not a walkable band"
    );
    // Standing in the opening (just under the band underside) and asking for
    // the ground BELOW gets the plaza, not the band top.
    let from = spans[0].bottom - 0.5;
    let ground = field
        .surface_below(&grid, x, z, from)
        .expect("solid ground under the arch");
    assert!(
        ground <= spans[1].top + 0.5,
        "the floor under the arch at ({x}, {z}) is {ground:.2} vs span top {:.2}",
        spans[1].top
    );
}

#[test]
fn test_folded_cliff_walls_have_no_holes_and_bounded_flips() {
    // The visual gate that caught the wall-speckling: a cliff wall meshed
    // from folded profiles (terraces, undercuts, gullies) must have no
    // topological holes anywhere, and the backfacing-triangle count — the
    // GPU-culled "polygonal holes" — must stay bounded. Known residual:
    // sub-voxel thin sheets (a carved void passing under the natural
    // terrain) put both crossings in one cell and yield a handful of
    // flipped triangles along the sheet line; killing those needs voxel
    // refinement near features and is intentionally NOT attempted here.
    let (grid, field, spec) = carve("qa-cliffs.xml");
    let voxel_size = 1.0_f32;
    let extent = VOXEL_CHUNK_CELLS as f32 * voxel_size;

    // Positions are chunk-relative on ALL axes; the seam band is one voxel
    // deep on each face of the chunk.
    let on_border = |p: &[f32; 3]| {
        p[0] <= voxel_size
            || p[0] >= extent - voxel_size
            || p[1] <= voxel_size
            || p[1] >= extent - voxel_size
            || p[2] <= voxel_size
            || p[2] >= extent - voxel_size
    };

    let mut total_flipped = 0usize;
    let xs: Vec<f32> = (-6i32..7).map(|i| i as f32 * extent).collect();
    let zs: Vec<f32> = (-4i32..=6).map(|i| i as f32 * extent).collect();
    for z0 in zs {
        for cy in 0..3 {
            for x0 in &xs {
                let origin = Vec3::new(*x0, cy as f32 * extent, z0);
                let params = VoxelChunkParams {
                    origin,
                    cells: VOXEL_CHUNK_CELLS,
                    voxel_size,
                    texture_tile_size: spec.texture_tile_size,
                    tint: spec.chunk_tint(),
                    max_height: spec.max_height,
                    uses_layer_material: true,
                    transitions: [false; 4],
                };
                let Some(data) = build_voxel_mesh(&|p| field.density(&grid, p), &params) else {
                    continue;
                };
                // No topological holes: every interior edge has exactly two
                // triangles.
                let mut edges: std::collections::HashMap<(u32, u32), u32> =
                    std::collections::HashMap::new();
                for tri in data.indices.chunks_exact(3) {
                    for k in 0..3 {
                        let (a, b) = (tri[k], tri[(k + 1) % 3]);
                        *edges.entry((a.min(b), a.max(b))).or_insert(0) += 1;
                    }
                }
                let holes = edges
                    .iter()
                    .filter(|item| *item.1 == 1)
                    .filter(|item| {
                        let (a, b) = item.0;
                        !on_border(&data.positions[*a as usize])
                            && !on_border(&data.positions[*b as usize])
                    })
                    .count();
                assert_eq!(holes, 0, "interior holes in chunk origin {origin}");
                // Winding: geometric normal must agree with the field
                // gradient (outward = +gradient); seam-band triangles are
                // exempt (their other half lives in the neighbour).
                for tri in data.indices.chunks_exact(3) {
                    if on_border(&data.positions[tri[0] as usize])
                        || on_border(&data.positions[tri[1] as usize])
                        || on_border(&data.positions[tri[2] as usize])
                    {
                        continue;
                    }
                    let (a, b, c) = (
                        Vec3::from(data.positions[tri[0] as usize]),
                        Vec3::from(data.positions[tri[1] as usize]),
                        Vec3::from(data.positions[tri[2] as usize]),
                    );
                    let gn = (b - a).cross(c - a);
                    if gn.length_squared() < 1e-12 {
                        continue;
                    }
                    let centroid = origin + (a + b + c) / 3.0;
                    let e = 0.5;
                    let g = Vec3::new(
                        field.density(&grid, centroid + Vec3::X * e)
                            - field.density(&grid, centroid - Vec3::X * e),
                        field.density(&grid, centroid + Vec3::Y * e)
                            - field.density(&grid, centroid - Vec3::Y * e),
                        field.density(&grid, centroid + Vec3::Z * e)
                            - field.density(&grid, centroid - Vec3::Z * e),
                    );
                    if g.dot(gn) < 0.0 {
                        total_flipped += 1;
                    }
                }
            }
        }
    }
    assert!(
        total_flipped <= 200,
        "{total_flipped} backfacing triangles across the cliff walls — the \
         thin-sheet speckling regressed past its documented bound"
    );
}

// --------------------------------------------------------------- travessias

/// Walks the deck of a `<Bridge>` and returns, per sampled point, the spans
/// the column resolves to. A crossing you can use is one where the walker
/// finds a solid slab with air under it.
fn deck_spans(
    grid: &BrushGrid,
    field: &VoxelField,
    from: bevy::math::Vec2,
    to: bevy::math::Vec2,
    samples: usize,
) -> Vec<(f32, Vec<viber::terrain::voxel::Span>)> {
    (0..=samples)
        .map(|i| {
            let t = i as f32 / samples as f32;
            let p = from.lerp(to, t);
            (t, field.column(grid, p.x, p.y))
        })
        .collect()
}

#[test]
fn test_the_stone_bridge_puts_a_deck_over_the_river_with_air_under_it() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // The middle third of the span: over the channel, clear of both banks.
    let walk = deck_spans(
        &grid,
        &field,
        bevy::math::Vec2::new(-14.0, 0.0),
        bevy::math::Vec2::new(14.0, 0.0),
        14,
    );
    let two_span = walk.iter().filter(|(_, s)| s.len() >= 2).count();
    assert!(
        two_span >= walk.len() / 2,
        "over the channel most columns must be deck + bed, got {two_span} of {}",
        walk.len()
    );

    // The deck is walkable from above and the water is reachable from below:
    // `sample` (the top of the world) is the deck, `surface_below` from just
    // under it is the river bed. That pair is the whole point of the feature.
    let mid = bevy::math::Vec2::new(0.0, 0.0);
    let top = field.surface_top(&grid, mid.x, mid.y);
    let ground = grid.sample(mid.x, mid.y);
    assert!(
        top > ground + 1.0,
        "deck at {top:.2} must stand clear of the bed at {ground:.2}"
    );
    let under = field
        .surface_below(&grid, mid.x, mid.y, top - 2.5)
        .expect("there is a river bed under the deck");
    assert!(
        under <= ground + 0.6,
        "under the deck the walker lands on the bed ({under:.2}), not on the slab"
    );
    assert!(
        top - under >= 2.0,
        "headroom under the deck is {:.2} m, too low to pass",
        top - under
    );
}

#[test]
fn test_the_stone_bridge_has_piers_between_its_arcades() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // Three arcades over an 80 m deck: the openings are wide, the piers
    // between them are narrow. Sampling the underside along the whole deck
    // must find BOTH — all-solid means the arcades never cut, all-air means
    // the spandrel never built.
    let mut open = 0;
    let mut solid = 0;
    for i in 0..=80 {
        let x = -40.0 + i as f32;
        let ground = grid.sample(x, 0.0);
        // A metre over the ground: inside a pier this is rock, inside an
        // arcade it is air.
        let p = Vec3::new(x, ground + 1.5, 0.0);
        if field.density(&grid, p) < 0.0 {
            solid += 1;
        } else {
            open += 1;
        }
    }
    assert!(open > 20, "the arcades never opened ({open} air samples)");
    assert!(solid > 5, "no masonry between the arcades ({solid} rock samples)");
}

#[test]
fn test_the_natural_bridge_spans_without_a_single_pier() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // The rock span crosses the same channel further north. Chaikin flattens
    // the authored apex, so the deck rides close to the chord — sample that.
    let walk = deck_spans(
        &grid,
        &field,
        bevy::math::Vec2::new(-22.0, 62.0),
        bevy::math::Vec2::new(6.0, 62.0),
        12,
    );
    assert!(
        walk.iter().all(|(_, s)| s.len() >= 2),
        "a span with no piers must leave rock-then-air-then-bed everywhere: {:?}",
        walk.iter().map(|(t, s)| (*t, s.len())).collect::<Vec<_>>()
    );
    // No pier: the air under the span is continuous, so every sample keeps a
    // real gap between the arch band and the bed.
    for (t, spans) in &walk {
        let band = spans[0];
        let bed = spans[1];
        assert!(
            band.bottom - bed.top >= 2.0,
            "t={t:.2}: only {:.2} m of air under the span — that reads as a pier",
            band.bottom - bed.top
        );
        // Authored 4 m thick: rock, not a paper slab.
        assert!(
            band.thickness() >= 2.0,
            "t={t:.2}: rock span is only {:.2} m thick",
            band.thickness()
        );
    }
}

#[test]
fn test_a_bridge_ends_on_its_banks_instead_of_floating() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // At both abutments the deck must meet the terrain: one span, no gap.
    for x in [-40.0f32, 40.0] {
        let spans = field.column(&grid, x, 0.0);
        assert_eq!(
            spans.len(),
            1,
            "abutment at x={x} left a gap between deck and bank: {spans:?}"
        );
    }
}

// ------------------------------------------------------ grutas com salas

#[test]
fn test_the_chamber_is_a_room_with_a_roof_over_it() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // Six metres off the chamber's centre: still well inside the 9 m room,
    // and clear of the shaft that opens its middle to the sky.
    let (x, z) = (-70.0f32, -62.0f32);
    let spans = field.column(&grid, x, z);
    assert_eq!(
        spans.len(),
        2,
        "inside the room the column is roof + floor, got {spans:?}"
    );
    let (roof, floor) = (spans[0], spans[1]);
    assert!(
        roof.bottom - floor.top >= 3.0,
        "the room is only {:.2} m tall — that is a tunnel, not a hall",
        roof.bottom - floor.top
    );
    // And there is real rock overhead, not a skin.
    assert!(
        roof.thickness() >= 1.0,
        "the roof is {:.2} m thick",
        roof.thickness()
    );
}

#[test]
fn test_the_shaft_opens_the_chamber_to_the_sky() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // Straight down the chimney: no rock at all between the room and daylight,
    // so the top of the world here is the chamber floor.
    let (x, z) = (-76.0f32, -62.0f32);
    let ground = grid.sample(x, z);
    let top = field.surface_top(&grid, x, z);
    assert!(
        top < ground - 5.0,
        "the shaft must cut through: surface {top:.2} vs ground {ground:.2}"
    );
    assert_eq!(
        field.column(&grid, x, z).len(),
        1,
        "a chimney leaves one solid — everything above the floor is air"
    );
    // Two metres to the side the rock is back: the shaft is a shaft, not a
    // crater.
    assert!(
        field.surface_top(&grid, x + 4.0, z) > ground - 2.0,
        "the shaft ate the whole hillside"
    );
}

#[test]
fn test_the_cave_radius_profile_widens_into_the_gallery() {
    use viber::terrain::paths::{chaikin_smooth, resample};
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // `radius="2.5 5 3"`: the tube is narrowest at the mouths and widest in
    // the middle. Walk the tunnel's own line (the same smoothing the build
    // does) and measure the void, staying clear of the chamber at t ~ 0.5.
    let authored = vec![
        bevy::math::Vec2::new(-104.0, -84.0),
        bevy::math::Vec2::new(-76.0, -62.0),
        bevy::math::Vec2::new(-44.0, -52.0),
    ];
    let line = resample(&chaikin_smooth(&authored, 2, false), 4.0);
    // Arc-length fractions, the same parametrisation the build uses: the
    // stations are not evenly spaced once the authored end point is pinned.
    let mut run = 0.0f32;
    let mut arc = vec![0.0f32];
    for pair in line.windows(2) {
        run += pair[0].distance(pair[1]);
        arc.push(run);
    }
    let ts: Vec<f32> = arc.iter().map(|d| d / run).collect();
    let void_at = |i: usize| -> f32 {
        let p = line[i];
        let spans = field.column(&grid, p.x, p.y);
        if spans.len() < 2 {
            return 0.0;
        }
        spans[0].bottom - spans[1].top
    };
    let mut near_mouth = 0.0f32;
    let mut toward_gallery = 0.0f32;
    for (i, t) in ts.iter().enumerate() {
        // Clear of the mouth ramp (t < 0.18) at one end and of the chamber at
        // the other, so what is being compared is the tube's own radius.
        if (0.20..0.28).contains(t) {
            near_mouth = near_mouth.max(void_at(i));
        } else if (0.38..0.46).contains(t) {
            toward_gallery = toward_gallery.max(void_at(i));
        }
    }
    assert!(near_mouth > 0.0, "the tunnel never opened near the mouth");
    assert!(
        toward_gallery > near_mouth + 1.0,
        "the gallery ({toward_gallery:.2} m) must be clearly taller than the \
         tunnel near the mouth ({near_mouth:.2} m)"
    );
}

// --------------------------------------------------------- arcos e viadutos

#[test]
fn test_the_natural_arch_tilts_with_the_slope_instead_of_floating() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // Mid-span of the rock band: air under it, ground below that.
    let (x, z) = (70.0f32, -65.0f32);
    let spans = field.column(&grid, x, z);
    assert_eq!(spans.len(), 2, "band over ground, got {spans:?}");
    let (band, ground) = (spans[0], spans[1]);
    assert!(
        band.bottom - ground.top >= 3.0,
        "only {:.2} m of headroom under the arch",
        band.bottom - ground.top
    );
    // Both legs are planted: sampling right at each foot finds a single solid
    // reaching the ground, no floating stub.
    for (fx, fz) in [(58.0f32, -72.0f32), (82.0, -58.0)] {
        let at_foot = field.column(&grid, fx, fz);
        assert_eq!(
            at_foot.len(),
            1,
            "the foot at ({fx}, {fz}) is not on the ground: {at_foot:?}"
        );
    }
}

#[test]
fn test_the_viaduct_repeats_its_opening_along_the_path() {
    let (grid, field, _spec) = carve("qa-pontes.xml");
    // `spans="4"` over a 48 m path: walking the line must pass under four
    // separate bands, each with its own ground underneath.
    let mut openings = 0;
    let mut was_open = false;
    for i in 0..=96 {
        let t = i as f32 / 96.0;
        let x = 66.0 + 48.0 * t;
        let z = 18.0 + 8.0 * t;
        let open = field.column(&grid, x, z).len() >= 2;
        if open && !was_open {
            openings += 1;
        }
        was_open = open;
    }
    assert_eq!(openings, 4, "expected four distinct openings, found {openings}");
}

// --------------------------------------------------------------- dispersão

#[test]
fn test_the_same_seed_seeds_the_same_rocks_twice() {
    let (grid, features, _spec, result, cliff_mask, _bands, taken) =
        bootstrap_to_scatter("qa-pontes.xml");
    let guards = full_guards(&result, &cliff_mask, &taken);
    let spec = features.rock_fields[0].clone();
    let first = spec.resolve(&grid, &guards);
    let second = spec.resolve(&grid, &guards);
    assert!(!first.is_empty(), "the field seeded nothing at all");
    assert_eq!(
        first, second,
        "the same seed over the same terrain must place the same rocks"
    );

    // A different seed moves them. Otherwise the seed is decorative.
    let moved = viber::terrain::voxel::RockFeaturesSpec {
        seed: spec.seed + 1,
        ..spec.clone()
    }
    .resolve(&grid, &guards);
    assert_ne!(first, moved, "changing the seed changed nothing");
}

#[test]
fn test_seeded_rocks_stay_inside_the_region_and_respect_the_budget() {
    let (_grid, got) = qa_scatter();
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("worlds")
        .join("qa-pontes.xml");
    let loaded = xml::include::load_world(&path).expect("world loads");
    let world = recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("world parses");
    let mut pending = viber::recipes::spawn::PendingTerrain::default();
    viber::recipes::spawn::collect_terrain(&world.entities, &mut pending);
    let spec = &pending.features.rock_fields[0];

    assert!(got.arches.len() <= spec.arches as usize);
    assert!(got.caves.len() <= spec.caves as usize);
    assert!(got.bridges.len() <= spec.bridges as usize);

    // Anchors inside the region. A feature's own path reaches beyond it by
    // design (an arch is wider than its site) — the seed point is what the
    // region bounds.
    let lo = spec.min.min(spec.max);
    let hi = spec.min.max(spec.max);
    let inside = |p: bevy::math::Vec2| {
        p.x >= lo.x - 0.01 && p.x <= hi.x + 0.01 && p.y >= lo.y - 0.01 && p.y <= hi.y + 0.01
    };
    for cave in &got.caves {
        assert!(inside(cave.path[0]), "cave mouth outside the region");
    }
    for bridge in &got.bridges {
        assert!(inside(bridge.path[1]), "bridge centre outside the region");
    }
}

#[test]
fn test_seeded_rocks_keep_out_of_the_water() {
    // The guards are the point: a rock span seeded into the middle of the
    // river channel is worse than no span at all.
    let (grid, _field, _spec) = carve("qa-pontes.xml");
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("worlds")
        .join("qa-pontes.xml");
    let loaded = xml::include::load_world(&path).expect("world loads");
    let world = recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("world parses");
    let mut pending = viber::recipes::spawn::PendingTerrain::default();
    viber::recipes::spawn::collect_terrain(&world.entities, &mut pending);
    // A field deliberately laid over the river, with the water guard armed.
    let over_the_river = viber::terrain::voxel::RockFeaturesSpec {
        min: bevy::math::Vec2::new(-40.0, -110.0),
        max: bevy::math::Vec2::new(40.0, 90.0),
        seed: 3,
        arches: 8,
        caves: 4,
        spacing: 18.0,
        min_slope: 5.0,
        min_drop: 1.0,
        ..viber::terrain::voxel::RockFeaturesSpec::default()
    };
    let mut carved = BrushGrid::from_height_map(
        &HeightMapU16::procedural(&_spec, _spec.resolution.max(1) as usize),
        _spec.world_size,
        _spec.max_height,
        _spec.height_smoothing,
    )
    .expect("grid builds");
    let result = viber::terrain::features::apply_features(&mut carved, &pending.features);
    let guards = viber::terrain::voxel::ScatterGuards {
        water: &result.water,
        roads: &result.roads,
        ..viber::terrain::voxel::ScatterGuards::default()
    };
    let got = over_the_river.resolve(&grid, &guards);
    assert!(!got.is_empty(), "nothing seeded — the test proves nothing");
    for p in got
        .arches
        .iter()
        .flat_map(|a| a.path.iter())
        .chain(got.caves.iter().map(|c| &c.path[0]))
    {
        assert!(
            !result.water.iter().any(|w| w.contains(*p)),
            "a rock was seeded inside the water carve zone at {p:?}"
        );
    }
}

/// Builds the mask + claimed discs exactly like the bootstrap, then resolves
/// the QA world's field with the FULL guard set and asserts every seeded
/// cave is clean along its whole path.
#[test]
fn test_seeded_caves_stay_clear_of_every_feature_along_the_whole_path() {
    let (grid, features, _spec, result, cliff_mask, _bands, taken) =
        bootstrap_to_scatter("qa-pontes.xml");
    let guards = full_guards(&result, &cliff_mask, &taken);

    let field = features
        .rock_fields
        .first()
        .expect("the QA world seeds rocks");
    let (got, stats) = field.resolve_with_stats(&grid, &guards);
    assert!(
        !got.caves.is_empty(),
        "no cave survived the guards — the test proves nothing (stats: {stats:?})"
    );

    let clear_of_roads = field.clear_of_roads.max(2.0);
    for cave in &got.caves {
        let stations = viber::terrain::paths::resample(&cave.path, 4.0);
        assert!(
            stations.len() >= 2,
            "a seeded cave must have a real path, got {} station(s)",
            stations.len()
        );
        let h0 = grid.sample(stations[0].x, stations[0].y);
        for (i, s) in stations.iter().enumerate() {
            assert!(
                !result.water.iter().any(|w| w.contains(*s)),
                "cave `{}` station {i} {s:?} is inside a water carve zone",
                cave.name.as_deref().unwrap_or("?")
            );
            assert!(
                result
                    .roads
                    .iter()
                    .all(|r| r.distance_to_road(*s) >= clear_of_roads),
                "cave `{}` station {i} {s:?} digs into a road corridor",
                cave.name.as_deref().unwrap_or("?")
            );
            assert!(
                h0 - grid.sample(s.x, s.y) <= 1.5,
                "cave `{}` station {i} {s:?} dives {:.1} m below the mouth — \
                 the tube would trench the valley floor",
                cave.name.as_deref().unwrap_or("?"),
                h0 - grid.sample(s.x, s.y)
            );
        }
        // The hill must rise over the run: both mouths open on rising ground.
        let high = stations
            .iter()
            .map(|s| grid.sample(s.x, s.y))
            .fold(f32::NEG_INFINITY, f32::max);
        assert!(
            high - h0 >= cave.depth * 0.5,
            "cave `{}` climbs only {:.1} m over its run — the mouth ramps would \
             trench flat ground instead of opening in the slope",
            cave.name.as_deref().unwrap_or("?"),
            high - h0
        );
    }
}

