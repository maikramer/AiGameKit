//! Micro-benchmark: quanto custa meshar UMA caixa voxel por LOD?
//!
//! O ladder de colunas voxel reconstrói caixas inline no main thread com um
//! orçamento de `VOXEL_MAX_MESH_BUILDS_PER_FRAME` caixas/frame. Uma caixa 32³
//! ao passo de 1 m amostra ~39 k vezes o campo; este bench põe um número no
//! custo por LOD com a geometria exata que o simple-rpg usa, para o orçamento
//! ser argumentado com dados e não ao toque.
//!
//! O segundo bench mede o MESMO trabalho com o campo cheio de mods (pontes,
//! arcos, grutas): `density` passa a percorrer os candidatos do bucket com um
//! teste de AABB por mod, e é esse o custo que cresce quando um mundo ganha
//! travessias. Sem número, uma regressão de CSG passa em silêncio.
//!
//! Run with: `cargo test --release --test chunk_build_bench -- --nocapture`

use std::time::Instant;

use viber::terrain::brush::BrushGrid;
use viber::terrain::heightmap::HeightMapU16;
use viber::terrain::spec::TerrainSpec;
use viber::terrain::voxel::{VoxelField, build_box_mesh, column_boxes, lod_shape};

/// Grid de terreno com relevo suave (o custo no sampler é o mesmo do
/// runtime: Catmull-Rom monotone).
fn rolling_grid() -> (TerrainSpec, BrushGrid) {
    let spec = TerrainSpec {
        world_size: 256.0,
        max_height: 50.0,
        chunk_size: 64.0,
        resolution: 64,
        seed: 11,
        ..TerrainSpec::default()
    };
    let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
    let grid = BrushGrid::from_height_map(&map, spec.world_size, spec.max_height, 1.0)
        .expect("grid builds");
    (spec, grid)
}

#[test]
fn bench_voxel_box_build_per_lod() {
    let (spec, grid) = rolling_grid();
    let field = VoxelField::flat(spec.world_size, spec.chunk_size);
    let edge = spec.chunk_size;

    for lod in 0..3u8 {
        let shape = lod_shape(1.0, edge, lod);
        let coords = bevy::math::UVec2::new(2, 2);
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            edge,
            1.0,
            lod,
            coords,
            [viber::terrain::voxel::spawn::NO_NEIGHBOUR; 4],
        );
        assert!(!boxes.is_empty(), "lod {lod}: no boxes planned");

        // Warm-up (allocator, caches).
        for b in &boxes {
            let _ = build_box_mesh(&spec, &grid, &field, b);
        }
        let start = Instant::now();
        let mut built = 0usize;
        for b in &boxes {
            if build_box_mesh(&spec, &grid, &field, b).is_some() {
                built += 1;
            }
        }
        let elapsed = start.elapsed();
        let per_box = elapsed / built.max(1) as u32;
        let step = (edge / shape.per_edge as f32) / shape.cells as f32;
        println!(
            "LOD {lod} ({} células × {} por coluna, passo {step:.1} m): {built} caixas em {elapsed:?} → {per_box:?}/caixa",
            shape.cells, shape.per_edge,
        );
        assert!(built > 0, "lod {lod}: nothing built");
    }
}

/// Tecto por caixa em RELEASE. Medido: com o `ModIndex` a afinar a célula, a
/// coluna da ponte de pedra custa ~9 ms/caixa, contra ~7 ms do campo sem mods
/// nenhuns — o CSG sai praticamente de graça. Antes de afinar a célula custava
/// ~38 ms, e é essa a regressão que este tecto apanha: o campo a deixar de
/// podar candidatos, um `bounds()` largo de mais, uma primitiva SDF a fazer
/// trabalho a mais por amostra.
///
/// Em debug o mesmo trabalho é uma ordem de grandeza mais lento e o número não
/// diz nada, por isso aí o bench só imprime.
const MAX_MS_PER_BOX: f64 = 30.0;

/// Constrói o campo voxel do mundo QA das travessias (pontes de pedra e
/// natural, gruta com sala e chaminé, arco natural, viaduto, dispersão).
fn qa_pontes_field() -> (TerrainSpec, BrushGrid, VoxelField) {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("worlds")
        .join("qa-pontes.xml");
    let loaded = viber::xml::include::load_world(&path).expect("world loads");
    let world = viber::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("parses");
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
    let result = viber::terrain::features::apply_features(&mut grid, &pending.features);

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
    let field = VoxelField::new(mods, spec.world_size, spec.chunk_size);
    (spec, grid, field)
}

#[test]
fn bench_voxel_box_build_with_a_field_full_of_mods() {
    let (spec, grid, field) = qa_pontes_field();
    assert!(
        field.mods().len() >= 40,
        "o mundo QA tem de carregar mods a sério (tem {})",
        field.mods().len()
    );
    let edge = spec.chunk_size;
    println!("campo com {} mods", field.mods().len());

    // Colunas escolhidas por cima das travessias: é onde os candidatos por
    // bucket são muitos e o gate de AABB trabalha.
    for coords in [
        bevy::math::UVec2::new(2, 2), // a ponte de pedra sobre a ribeira
        bevy::math::UVec2::new(2, 3), // o arco natural
    ] {
        for lod in 0..3u8 {
            let boxes = column_boxes(
                &spec,
                &grid,
                &field,
                edge,
                1.0,
                lod,
                coords,
                [viber::terrain::voxel::spawn::NO_NEIGHBOUR; 4],
            );
            if boxes.is_empty() {
                continue;
            }
            for b in &boxes {
                let _ = build_box_mesh(&spec, &grid, &field, b);
            }
            let start = Instant::now();
            let mut built = 0usize;
            for b in &boxes {
                if build_box_mesh(&spec, &grid, &field, b).is_some() {
                    built += 1;
                }
            }
            let elapsed = start.elapsed();
            if built == 0 {
                continue;
            }
            let per_box_ms = elapsed.as_secs_f64() * 1000.0 / built as f64;
            println!(
                "coluna {coords:?} LOD {lod}: {built} caixas em {elapsed:?} → {per_box_ms:.2} ms/caixa",
            );
            assert!(
                cfg!(debug_assertions) || per_box_ms < MAX_MS_PER_BOX,
                "coluna {coords:?} LOD {lod}: {per_box_ms:.2} ms por caixa passa o tecto de \
                 {MAX_MS_PER_BOX} ms — o campo deixou de podar candidatos?"
            );
        }
    }
}
