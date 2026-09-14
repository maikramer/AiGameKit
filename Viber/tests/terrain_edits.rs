//! Edições VIVAS do terreno (Fase 3) — o que a prova tem de mostrar:
//! depois de uma cratera, (a) a query de altura muda, (b) o MESH transvoxel
//! (os mesmos triângulos que assam o collider) acompanha, e (c) nada fora do
//! raio se mexe.

use bevy::math::{UVec2, Vec2, Vec3};

use viber::terrain::brush::BrushGrid;
use viber::terrain::delta::{DeltaGrid, EditedBase, TerrainEdit, apply_edit};
use viber::terrain::mesh::HeightField;
use viber::terrain::spec::TerrainSpec;
use viber::terrain::voxel::VoxelField;
use viber::terrain::voxel::spawn::{NO_NEIGHBOUR, build_box_mesh, column_boxes};

/// Grelha plana de 64 m a 10 m de altura, célula 1 m.
fn flat_grid() -> BrushGrid {
    let mut grid = BrushGrid::new(vec![0; 64 * 64], 64, 64, 64.0, 50.0, 0.0).expect("grid");
    grid.begin_stroke("flat");
    for i in 0..64 * 64 {
        grid.set_cell_height(i % 64, i / 64, 10.0);
    }
    grid.commit_stroke();
    grid
}

/// Mesha a coluna 0 (64 m, LOD 0) sobre a vista dada e devolve
/// `(nº de triângulos, y mínimo dos vértices dentro de |x|,|z| < 12)`.
fn mesh_probe(grid: &BrushGrid, deltas: &DeltaGrid, spec: &TerrainSpec) -> (usize, f32) {
    let field = VoxelField::default(); // sem mods: density = p.y − base
    let base = EditedBase { grid, deltas };
    let boxes = column_boxes(
        spec,
        &base,
        &field,
        64.0,
        1.0,
        0,
        UVec2::new(0, 0),
        [NO_NEIGHBOUR; 4],
        [false; 4],
    );
    let mut triangles = 0usize;
    let mut min_y = f32::INFINITY;
    for b in &boxes {
        let Some(data) = build_box_mesh(spec, &base, &field, b) else {
            continue;
        };
        triangles += data.indices.len() / 3;
        for p in &data.positions {
            let world = b.origin + Vec3::from(*p);
            if world.x.abs() < 12.0 && world.z.abs() < 12.0 {
                min_y = min_y.min(world.y);
            }
        }
    }
    (triangles, min_y)
}

/// Uma cratera muda a altura de gameplay E o mesh (que é o collider) —
/// dentro do raio escava, fora dele nada se mexe.
#[test]
fn test_crater_moves_the_mesh_and_the_queries() {
    let grid = flat_grid();
    let spec = TerrainSpec {
        world_size: 64.0,
        chunk_size: 64.0,
        ..TerrainSpec::default()
    };
    let mut deltas = DeltaGrid::default();
    let (tris_before, min_y_before) = mesh_probe(&grid, &deltas, &spec);
    assert!(
        min_y_before > 9.5,
        "plano antes da cratera: min_y {min_y_before}"
    );

    assert!(apply_edit(
        &grid,
        &mut deltas,
        &TerrainEdit::Crater {
            at: Vec2::ZERO,
            radius: 10.0,
            depth: 6.0,
        }
    ));

    // (a) A altura de gameplay segue o overlay.
    let base = EditedBase {
        grid: &grid,
        deltas: &deltas,
    };
    assert!(base.sample(0.0, 0.0) < 5.0, "fundo da tigela: {}", base.sample(0.0, 0.0));
    assert!(
        (base.sample(30.0, 30.0) - 10.0).abs() < 0.1,
        "fora do raio intacto"
    );
    let field = VoxelField::default();
    assert!(
        field.surface_top(&base, 0.0, 0.0) < 5.0,
        "surface_top do campo vê a edição"
    );

    // (b) O mesh transvoxel acompanha: mais triângulos (tigela + rebordo) e
    // o fundo desce. Estes são os MESMOS buffers que assam o trimesh da
    // coluna no swap atómico do LOD.
    let (tris_after, min_y_after) = mesh_probe(&grid, &deltas, &spec);
    assert!(
        min_y_after < 5.5,
        "o mesh desceu com a cratera: min_y {min_y_after} (antes {min_y_before})"
    );
    assert!(
        tris_after > tris_before,
        "a cratera acrescenta geometria: {tris_after} vs {tris_before}"
    );

    // (c) um raise posterior empilha na vista editada (não parte o overlay).
    assert!(apply_edit(
        &grid,
        &mut deltas,
        &TerrainEdit::Raise {
            at: Vec2::new(0.0, 0.0),
            radius: 4.0,
            height: 15.0,
        }
    ));
    let base = EditedBase {
        grid: &grid,
        deltas: &deltas,
    };
    assert!(
        base.sample(0.0, 0.0) > 12.0,
        "aterro sobre a cratera: {}",
        base.sample(0.0, 0.0)
    );
}
