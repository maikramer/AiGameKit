//! The terrain collider IS the drawn surface.
//!
//! The claim under test (Fase 2 do plano voxel): o trimesh assado dos
//! triângulos transvoxel de uma coluna responde no MESMO sítio que as
//! queries de gameplay — `surface_top` em campo aberto, `surface_below`
//! dentro de uma gruta — e não tem cracks nas arestas internas entre as
//! caixas da coluna (pseudo-normais do `FIX_INTERNAL_EDGES`).

use bevy::math::Vec2;

use bevy_rapier3d::parry::math::Vector;
use bevy_rapier3d::parry::query::Ray;
use viber::physics::{ColumnColliderBake, collision_keep_within};
use viber::terrain::brush::BrushGrid;
use viber::terrain::heightmap::HeightMapU16;
use viber::terrain::spec::TerrainSpec;
use viber::terrain::voxel::spawn::NO_NEIGHBOUR;
use viber::terrain::voxel::{VoxelField, build_box_mesh, column_boxes};

/// Mundo de teste: 2×2 colunas de 64 m (LOD 0 = 2×2 caixas de 32³ @ 1 m).
const EDGE: f32 = 64.0;
const LOD0_CELL: f32 = 1.0;
const COORDS: bevy::math::UVec2 = bevy::math::UVec2::new(1, 1); // XZ [0, 64]²

fn spec(world_size: f32, max_height: f32) -> TerrainSpec {
    TerrainSpec {
        world_size,
        max_height,
        chunk_size: 64.0,
        resolution: 64,
        ..TerrainSpec::default()
    }
}

fn grid_from(world_size: f32, max_height: f32, height: impl Fn(f32, f32) -> f32) -> BrushGrid {
    let n = 33usize;
    let raw: Vec<u16> = (0..n * n)
        .map(|i| {
            let x = i % n;
            let z = i / n;
            let fx = x as f32 / (n - 1) as f32;
            let fz = z as f32 / (n - 1) as f32;
            ((height(fx, fz) / max_height) * 65535.0)
                .round()
                .clamp(0.0, 65535.0) as u16
        })
        .collect();
    BrushGrid::from_height_map(
        &HeightMapU16 {
            width: n,
            depth: n,
            data: raw,
        },
        world_size,
        max_height,
        0.0,
    )
    .expect("grid builds")
}

/// Assa o collider da coluna (1,1) exatamente como `spawn_column` faz.
fn bake_column(
    spec: &TerrainSpec,
    grid: &BrushGrid,
    field: &VoxelField,
) -> Option<bevy_rapier3d::prelude::Collider> {
    let boxes = column_boxes(
        spec,
        grid,
        field,
        EDGE,
        LOD0_CELL,
        0,
        COORDS,
        [NO_NEIGHBOUR; 4],
            [false; 4],
    );
    let mut bake = ColumnColliderBake::new();
    let mut meshed = 0;
    for b in &boxes {
        if let Some(data) = build_box_mesh(spec, grid, field, b) {
            bake.add_mesh_data(b.origin, &data);
            meshed += 1;
        }
    }
    assert!(meshed > 0, "the column must have geometry to collide with");
    bake.bake()
}

/// Ray para baixo a partir de `from`: o y do primeiro hit contra o collider.
fn drop_y(collider: &bevy_rapier3d::prelude::Collider, x: f32, z: f32, from: f32) -> f32 {
    let ray = Ray::new(Vector::new(x, from, z), Vector::new(0.0, -1.0, 0.0));
    let hit = collider
        .raw
        .cast_local_ray_and_get_normal(&ray, from + 500.0, true)
        .expect("the ray must hit the terrain collider");
    from - hit.time_of_impact
}

/// O chão sob `from` segundo as queries de gameplay (fallback ao topo).
fn gameplay_floor(field: &VoxelField, grid: &BrushGrid, x: f32, z: f32, from: f32) -> f32 {
    field
        .surface_below(grid, x, z, from)
        .unwrap_or_else(|| field.surface_top(grid, x, z))
}

#[test]
fn test_a_flat_column_collides_exactly_at_the_surface() {
    let (world_size, max_height, height) = (128.0_f32, 100.0_f32, 12.0_f32);
    let spec = spec(world_size, max_height);
    let grid = grid_from(world_size, max_height, |_, _| height);
    let field = VoxelField::default();
    let collider = bake_column(&spec, &grid, &field).expect("flat column bakes");

    // Inclui x=32: a costura ENTRE as duas caixas de 32 m — o ray tem de
    // acertar a superfície e não cair numa fenda fantasma da aresta interna.
    for x in [8.0_f32, 16.0, 31.99, 32.0, 32.01, 48.0, 56.0] {
        for z in [16.0_f32, 32.0, 48.0] {
            let hit = drop_y(&collider, x, z, 40.0);
            assert!(
                (hit - height).abs() < 0.05,
                "flat ground at ({x},{z}): ray hit {hit}, surface is {height}"
            );
        }
    }
}

#[test]
fn test_a_sloped_column_collides_where_the_queries_answer() {
    let (world_size, max_height) = (128.0_f32, 30.0_f32);
    let spec = spec(world_size, max_height);
    // Rampa ao longo de +X: 0 → 30 m.
    let grid = grid_from(world_size, max_height, |fx, _| fx * max_height);
    let field = VoxelField::default();
    let collider = bake_column(&spec, &grid, &field).expect("sloped column bakes");

    for x in [4.0_f32, 12.0, 20.0, 32.0, 44.0, 60.0] {
        for z in [8.0_f32, 32.0, 56.0] {
            let hit = drop_y(&collider, x, z, 60.0);
            let expected = gameplay_floor(&field, &grid, x, z, 60.0);
            // Meio voxel de tolerância: o mesher interpola linearmente a
            // célula, a query bissecta o SDF exato — o mesmo zero, com erro
            // sub-voxel nas duas pontas.
            assert!(
                (hit - expected).abs() < 0.5,
                "slope at ({x},{z}): collider {hit} vs query {expected}"
            );
        }
    }
}

#[test]
fn test_a_cave_collides_at_the_cave_floor_not_the_hill_top() {
    let (world_size, max_height, surface) = (128.0_f32, 100.0_f32, 60.0_f32);
    let spec = spec(world_size, max_height);
    let grid = grid_from(world_size, max_height, |_, _| surface);
    let cave = viber::terrain::voxel::cave::CaveSpec {
        name: Some("mina".into()),
        path: vec![Vec2::new(-60.0, 32.0), Vec2::new(60.0, 32.0)],
        radius: vec![3.5],
        depth: 12.0,
        open_ends: true,
        ..viber::terrain::voxel::cave::CaveSpec::default()
    };
    let field = VoxelField::new(cave.build(&grid), world_size, 64.0);
    let collider = bake_column(&spec, &grid, &field).expect("cave column bakes");

    for x in [24.0_f32, 32.0, 40.0] {
        // De DENTRO do túnel (centro 48, raio 3.5 → vazio 44.5..51.5): o ray
        // acerta o piso da gruta, não o topo do monte.
        let hit = drop_y(&collider, x, 32.0, 49.0);
        let expected = field
            .surface_below(&grid, x, 32.0, 49.0)
            .expect("cave floor below y=49");
        assert!(
            (hit - expected).abs() < 0.5,
            "cave floor at x={x}: collider {hit} vs query {expected}"
        );
        assert!(
            hit < surface - 5.0,
            "o ray a partir de DENTRO da gruta não pode acertar o topo do monte"
        );

        // De CIMA: acerta o topo do monte (a superfície que se vê).
        let top_hit = drop_y(&collider, x, 32.0, 90.0);
        let top = field.surface_top(&grid, x, 32.0);
        assert!(
            (top_hit - top).abs() < 0.5,
            "hill top at x={x}: collider {top_hit} vs query {top}"
        );
    }
}

#[test]
fn test_the_bake_is_deterministic() {
    let (world_size, max_height) = (128.0_f32, 30.0_f32);
    let spec = spec(world_size, max_height);
    let grid = grid_from(world_size, max_height, |fx, fz| {
        (fx + fz) * 0.5 * max_height
    });
    let field = VoxelField::default();

    let boxes = column_boxes(
        &spec,
        &grid,
        &field,
        EDGE,
        LOD0_CELL,
        0,
        COORDS,
        [NO_NEIGHBOUR; 4],
            [false; 4],
    );
    let bake = || {
        let mut b = ColumnColliderBake::new();
        for spec_box in &boxes {
            if let Some(data) = build_box_mesh(&spec, &grid, &field, spec_box) {
                b.add_mesh_data(spec_box.origin, &data);
            }
        }
        b
    };
    let a = bake();
    let b2 = bake();
    assert_eq!(a.triangle_count(), b2.triangle_count());
    assert!(a.triangle_count() > 0);
}

#[test]
fn test_the_collision_band_is_the_lod0_band() {
    // chunk 64 × radius 3 = 192 m, limitado à banda LOD 0 (lod_distance 128):
    // nunca há collider de geometria mais grosseira do que a desenhada.
    let spec = spec(4000.0, 200.0);
    assert!((collision_keep_within(&spec) - 128.0).abs() < 1e-4);

    // Um spec com banda LOD 0 mais larga que o raio de chunks: o raio vence.
    let mut wide = spec.clone();
    wide.lod_distance_ratio = 10.0; // lod_distance 640 > 192
    assert!((collision_keep_within(&wide) - 192.0).abs() < 1e-4);
}

// ---------------------------------------------------------------- travessias

/// Um vale com um desfiladeiro a meio da coluna (1,1): margens a 20 m,
/// leito a 2 m, paredes suavizadas para o mesher não ver um degrau vertical.
fn ravine_grid(world_size: f32, max_height: f32) -> BrushGrid {
    grid_from(world_size, max_height, |fx, _fz| {
        let world_x = -world_size * 0.5 + fx * world_size;
        // 0 no fundo do desfiladeiro, 1 nas margens.
        let t = ((world_x - 32.0).abs() / 10.0).clamp(0.0, 1.0);
        let s = t * t * (3.0 - 2.0 * t);
        2.0 + 18.0 * s
    })
}

fn bridge_field(
    grid: &BrushGrid,
    spec: &TerrainSpec,
    bridge: viber::terrain::voxel::BridgeSpec,
) -> VoxelField {
    VoxelField::new(bridge.build(grid), spec.world_size, spec.chunk_size)
}

#[test]
fn test_you_land_on_the_bridge_deck_and_not_in_the_river() {
    // A regressão que a ponte de estrada tem hoje: o ribbon de
    // `<Road profile="bridge">` é desenhado e mais nada, por isso o herói
    // atravessa-o e cai ao leito. Um `<Bridge>` vive no campo voxel, portanto
    // o trimesh da coluna traz o tabuleiro — e é o collider que o prova.
    let (world_size, max_height) = (128.0_f32, 100.0_f32);
    let spec = spec(world_size, max_height);
    let grid = ravine_grid(world_size, max_height);
    let bridge = viber::terrain::voxel::BridgeSpec {
        name: Some("ponte".to_string()),
        path: vec![Vec2::new(8.0, 32.0), Vec2::new(56.0, 32.0)],
        width: 8.0,
        rise: 1.0,
        thickness: 2.5,
        spans: Some(1),
        parapet: 0.0,
        ..viber::terrain::voxel::BridgeSpec::default()
    };
    let field = bridge_field(&grid, &spec, bridge);
    let collider = bake_column(&spec, &grid, &field).expect("a bridged column bakes");

    let bed = grid.sample(32.0, 32.0);
    assert!(bed < 6.0, "o leito tem de estar em baixo (got {bed:.2})");

    // Cair de muito alto sobre o meio do vão: o primeiro sólido é o
    // tabuleiro, ~18 m acima do leito.
    let landed = drop_y(&collider, 32.0, 32.0, 60.0);
    assert!(
        landed > bed + 10.0,
        "aterrou em {landed:.2} com o leito a {bed:.2} — caiu através do tabuleiro"
    );

    // E o collider concorda com o gameplay: o mesmo sítio, meio voxel.
    let floor = gameplay_floor(&field, &grid, 32.0, 32.0, 60.0);
    assert!(
        (landed - floor).abs() <= 0.5,
        "collider {landed:.2} vs gameplay {floor:.2}"
    );

    // Fora da largura do tabuleiro (8 m) não há ponte nenhuma: aterra no leito.
    let beside = drop_y(&collider, 32.0, 44.0, 60.0);
    assert!(
        beside < bed + 2.0,
        "ao lado da ponte devia cair ao leito, aterrou em {beside:.2}"
    );
}

#[test]
fn test_under_the_bridge_the_walker_gets_the_bed_not_the_deck() {
    // O outro lado do contrato: quem está DEBAIXO do arco tem chão próprio.
    // `surface_below` é a query certa (o topo do mundo ali é tecto).
    let (world_size, max_height) = (128.0_f32, 100.0_f32);
    let spec = spec(world_size, max_height);
    let grid = ravine_grid(world_size, max_height);
    let bridge = viber::terrain::voxel::BridgeSpec {
        name: Some("ponte".to_string()),
        path: vec![Vec2::new(8.0, 32.0), Vec2::new(56.0, 32.0)],
        width: 8.0,
        rise: 1.0,
        thickness: 2.5,
        spans: Some(1),
        parapet: 0.0,
        ..viber::terrain::voxel::BridgeSpec::default()
    };
    let field = bridge_field(&grid, &spec, bridge);
    let collider = bake_column(&spec, &grid, &field).expect("a bridged column bakes");

    let bed = grid.sample(32.0, 32.0);
    // Um metro acima do leito, já debaixo do arco.
    let under = gameplay_floor(&field, &grid, 32.0, 32.0, bed + 1.0);
    assert!(
        (under - bed).abs() <= 0.5,
        "debaixo do arco o chão é o leito ({bed:.2}), got {under:.2}"
    );
    let landed = drop_y(&collider, 32.0, 32.0, bed + 1.0);
    assert!(
        (landed - under).abs() <= 0.5,
        "collider {landed:.2} vs gameplay {under:.2} debaixo do arco"
    );
}
