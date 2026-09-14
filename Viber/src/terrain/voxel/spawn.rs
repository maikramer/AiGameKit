//! Spawning the volumetric columns — where the field becomes geometry.
//!
//! No caminho 100% volumétrico CADA célula da grelha de chunks é uma COLUNA
//! voxel: a entidade `TerrainChunk` (nome `chunk cz-cx`) é o pai de uma
//! pilha de caixas [`VoxelChunk`] meshadas com transvoxel. O ladder de
//! LOD vive no `plugin.rs` (select + histerese + budget + cull); o que este
//! módulo fornece é a geometria pura — [`lod_shape`] (tiled de caixas por
//! LOD), [`column_boxes`] (quais caixas existem) e [`build_box_mesh`] (uma
//! caixa → buffers).

use bevy::prelude::*;

use super::super::brush::BrushGrid;
use super::super::mesh::HeightField;
use super::super::runtime::{ChunkLayerMap, ChunkMaterialHandle, to_bevy_mesh};
use super::super::spec::TerrainSpec;
use super::field::VoxelField;
use super::mods::Bounds3;
use super::transvoxel_mesh::{VOXEL_CHUNK_CELLS, VoxelChunkParams, build_voxel_mesh};

/// A meshed box of the voxel field.
#[derive(Debug, Component)]
pub struct VoxelChunk {
    /// Box index inside the terrain grid (unique, stable, used for the name).
    pub coords: IVec3,
    /// World position of the box's minimum corner.
    pub origin: Vec3,
    /// Edge length in meters.
    pub extent: f32,
    /// Cell size in meters.
    pub voxel_size: f32,
}

/// Result of one bootstrap pass, for the log and for tests.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct VoxelSpawnStats {
    /// Boxes that produced geometry.
    pub meshed: usize,
    /// Boxes proven uniform (sky or bedrock) and skipped without sampling.
    pub skipped_uniform: usize,
    /// Boxes sampled that turned out to hold no surface after all.
    pub empty: usize,
    /// Terrain chunks handed to the voxel mesher.
    pub chunks: usize,
}

// ── Colunas voxel com ladder de LOD ─────────────────────────────────────────
//
// O caminho 100% volumétrico trata CADA célula da grelha de chunks como uma
// COLUNA voxel: o `TerrainChunk` deixa de ter mesh próprio e passa a ser o
// pai de uma pilha de caixas transvoxel. O ladder reutiliza a semântica do
// heightfield (select + histerese + budget + cull no `plugin.rs`); o que
// muda é o que um LOD constrói:

/// Geometria de UM LOD de uma coluna: `per_edge` caixas por borda de chunk,
/// cada uma com `cells` células por eixo.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VoxelLodShape {
    pub cells: usize,
    pub per_edge: i32,
}

/// Escolhe o tiling de caixas de uma coluna ao `lod`.
///
/// O ladder duplica a célula a cada nível — LOD 0 renderiza ao passo LOD-0
/// (`resolution`), comprando a mesma redução 4×/16× de triângulos do ladder
/// heightfield. `cells × célula × per_edge` tem de ladrilhar a borda do
/// chunk: procura-se um tiling exato a 32/16/8 células e, não havendo, UMA
/// caixa cobrindo a borda inteira (o mesher aceita qualquer tamanho de
/// célula; os LODs grossos ficam baratos pelo `cells`, não por caixas
/// parciais). Determinístico e global: dois vizinhos ao mesmo LOD derivam
/// SEMPRE o mesmo shape — é isso que mantém as costuras deles fechadas por
/// coincidência de vértices.
pub fn lod_shape(lod0_cell: f32, chunk_edge: f32, lod: u8) -> VoxelLodShape {
    if !(lod0_cell.is_finite() && lod0_cell > 0.0) || !(chunk_edge.is_finite() && chunk_edge > 0.0)
    {
        return VoxelLodShape {
            cells: VOXEL_CHUNK_CELLS,
            per_edge: 1,
        };
    }
    let target = lod0_cell * (1u32 << lod) as f32;
    for cells in [32usize, 16, 8] {
        let wanted = cells as f32 * target;
        let per_edge = (chunk_edge / wanted).ceil().max(1.0) as i32;
        let extent = chunk_edge / per_edge as f32;
        let actual = extent / cells as f32;
        if ((actual - target) / target).abs() <= 0.05 {
            return VoxelLodShape { cells, per_edge };
        }
    }
    let cells = ((chunk_edge / target).round() as usize).clamp(4, VOXEL_CHUNK_CELLS);
    VoxelLodShape { cells, per_edge: 1 }
}

/// Uma caixa transvoxel de uma coluna, pronta para meshar.
#[derive(Debug, Clone, PartialEq)]
pub struct VoxelBoxSpec {
    /// Coordenada da caixa na grelha lógica da coluna (nome/identidade).
    pub pos: IVec3,
    /// Posição mundial do canto mínimo.
    pub origin: Vec3,
    /// Aresta em metros (cúbica; as caixas ladrilham a coluna exatamente).
    pub extent: f32,
    /// Células por eixo.
    pub cells: usize,
    /// Aresta de uma célula em metros.
    pub voxel_size: f32,
    /// Caixa REFINADA (célula a meio do passo do LOD, perto de mods) —
    /// amostra 8× o volume; o orçamento de construção conta-a como uma
    /// frame inteira.
    pub refined: bool,
    /// Faces cujo vizinho está ao DOBRO da resolução desta caixa, em
    /// `[-X, +X, -Z, +Z]` — as células de transição do transvoxel.
    ///
    /// Só ligam nas caixas da fronteira da coluna, e só quando a coluna
    /// vizinha desse lado está num LOD mais FINO: quem gera as células de
    /// transição é o bloco grosseiro.
    pub transitions: [bool; 4],
}

/// LOD ausente na tabela de vizinhos (fora do mundo, ou coluna ainda por
/// nascer): trata-se como "igual a mim", que não pede transição nenhuma.
pub const NO_NEIGHBOUR: u8 = u8::MAX;

/// Gate do REFINAMENTO de voxel perto de mods — o "fix real" das folhas
/// finas sub-voxel (lips de carve a centímetros produzem ~0,4% de
/// triângulos degenerados ao passo de 1 m; a meio célula a superfície
/// segue a folha e os flaps virados caem). **OPT-IN**
/// (`VIBER_VOXEL_REFINE=1`): uma caixa refinada (64³) amostra 8× o volume
/// e mede 60-590 ms (pior caso: a coluna da ponte da qa-pontes, cache
/// superlinear) — default-ON seriam ~10 frames perdidos por caixa no
/// stream-in. Enquanto o mesher não amostro SÓ a superfície, fica para QA
/// visual e shots.
fn refine_enabled() -> bool {
    static ENABLED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *ENABLED.get_or_init(|| std::env::var("VIBER_VOXEL_REFINE").as_deref() == Ok("1"))
}

/// A coluna `coords` refina? Gate ON, ao LOD 0, TODOS os vizinhos também ao
/// LOD 0 (um refinado ao lado de um LOD 1 dava 4:1 — a ponte de transição
/// do transvoxel só sabe 2:1) e o retângulo do chunk toca a bounding XZ dos
/// mods (query aos buckets do [`ModIndex`]; mundo sem mods devolve vazio
/// logo). Precisa do [`LodField`] porque a condição dos VIZINHOS é avaliada
/// na vizinhança DELES — ver [`refined_neighbours`].
pub(crate) fn column_refines(
    lod_field: &super::super::plugin::LodField,
    field: &VoxelField,
    spec: &TerrainSpec,
    chunk_edge: f32,
    lod: u8,
    coords: UVec2,
) -> bool {
    if !refine_enabled() || lod != 0 {
        return false;
    }
    let n = lod_field.neighbours(coords);
    if !n.iter().all(|&l| l == 0 || l == NO_NEIGHBOUR) {
        return false;
    }
    let mods_y = field.index().bounds();
    if mods_y.min.x > mods_y.max.x {
        return false; // mundo sem mods — nada a refinar
    }
    let half = spec.world_size * 0.5;
    let x0 = -half + coords.x as f32 * chunk_edge;
    let z0 = -half + coords.y as f32 * chunk_edge;
    let rect = Bounds3::from_corners(
        Vec3::new(x0, mods_y.min.y, z0),
        Vec3::new(x0 + chunk_edge, mods_y.max.y, z0 + chunk_edge),
    );
    !field.index().candidates_in(&rect).is_empty()
}

/// Estado refinado dos 4 vizinhos de `coords`, avaliado na vizinhança DELES
/// (a pureza da função exige que os dois lados de uma costura vejam o mesmo
/// campo de LOD — é o mesmo contrato do clamp 2:1).
pub(crate) fn refined_neighbours(
    lod_field: &super::super::plugin::LodField,
    field: &VoxelField,
    spec: &TerrainSpec,
    chunk_edge: f32,
    coords: UVec2,
) -> [bool; 4] {
    std::array::from_fn(|i| {
        let (dx, dz) = side_delta(i);
        let nc = UVec2::new(
            (coords.x as i64 + dx).max(0) as u32,
            (coords.y as i64 + dz).max(0) as u32,
        );
        column_refines(lod_field, field, spec, chunk_edge, lod_field.get(nc).unwrap_or(NO_NEIGHBOUR), nc)
    })
}

fn side_delta(i: usize) -> (i64, i64) {
    match i {
        0 => (-1, 0),
        1 => (1, 0),
        2 => (0, -1),
        _ => (0, 1),
    }
}

/// Célula EFETIVA da coluna — o refinamento conta como meia célula (um
/// "meio-nível" do ladder): é esta grandeza que as transições comparam, e o
/// teto de 2:1 mantém-se por construção (refinar exige vizinhos a LOD 0).
fn effective_cell(lod0_cell: f32, lod: u8, refined: bool) -> f32 {
    lod0_cell * (1u32 << lod.min(8)) as f32 * if refined { 0.5 } else { 1.0 }
}

/// As caixas que renderizam UMA coluna ao `lod` dado.
///
/// A pilha vertical é o envelope do chunk: o intervalo de alturas da grelha
/// (`range_over`, O(1)) mais o alcance dos mods — o resto é céu ou bedrock
/// provados (`region_state`) e nunca chega a ser amostrado.
///
/// `neighbour_refined` é o estado de REFINAMENTO dos 4 vizinhos
/// ([`refined_neighbours`], avaliado no mesmo `LodField` que decidiu os
/// LODs) — as transições comparam a CÉLULA EFETIVA, e um LOD0 refinado
/// (meia célula) ao lado de um LOD0 normal ponteia como um nível do ladder.
#[allow(clippy::too_many_arguments)]
pub fn column_boxes(
    spec: &TerrainSpec,
    base: &dyn HeightField,
    field: &VoxelField,
    chunk_edge: f32,
    lod0_cell: f32,
    lod: u8,
    coords: UVec2,
    neighbour_lods: [u8; 4],
    neighbour_refined: [bool; 4],
) -> Vec<VoxelBoxSpec> {
    let shape = lod_shape(lod0_cell, chunk_edge, lod);
    let extent = chunk_edge / shape.per_edge as f32;
    let voxel_size = extent / shape.cells as f32;
    let half = spec.world_size * 0.5;
    let x0 = -half + coords.x as f32 * chunk_edge;
    let z0 = -half + coords.y as f32 * chunk_edge;
    let mods_y = field.index().bounds();
    let (gmin, gmax) = base
        .range_over(x0, z0, x0 + chunk_edge, z0 + chunk_edge)
        .unwrap_or((0.0, spec.max_height));
    let y_lo = gmin.min(mods_y.min.y) - 1.0;
    let y_hi = gmax.max(mods_y.max.y) + 1.0;
    let iy0 = (y_lo / extent).floor() as i32;
    let iy1 = (y_hi / extent).ceil() as i32;

    // Uma face pede transição quando o vizinho desse lado é mais FINO — em
    // CÉLULA EFETIVA: o refinamento conta como meia célula (um
    // "meio-nível"), e o teto de 2:1 mantém-se por construção (refinar
    // exige vizinhos a LOD 0, logo um refinado nunca encosta a um LOD 1).
    let self_refines = lod == 0
        && refine_enabled()
        && neighbour_lods.iter().all(|&l| l == 0 || l == NO_NEIGHBOUR)
        && {
            let mods_y = field.index().bounds();
            mods_y.min.x <= mods_y.max.x && {
                let rect = Bounds3::from_corners(
                    Vec3::new(x0, mods_y.min.y, z0),
                    Vec3::new(x0 + chunk_edge, mods_y.max.y, z0 + chunk_edge),
                );
                !field.index().candidates_in(&rect).is_empty()
            }
        };
    let my_cell = effective_cell(lod0_cell, lod, self_refines);
    let finer = [
        neighbour_lods[0] != NO_NEIGHBOUR
            && effective_cell(lod0_cell, neighbour_lods[0], neighbour_refined[0]) < my_cell,
        neighbour_lods[1] != NO_NEIGHBOUR
            && effective_cell(lod0_cell, neighbour_lods[1], neighbour_refined[1]) < my_cell,
        neighbour_lods[2] != NO_NEIGHBOUR
            && effective_cell(lod0_cell, neighbour_lods[2], neighbour_refined[2]) < my_cell,
        neighbour_lods[3] != NO_NEIGHBOUR
            && effective_cell(lod0_cell, neighbour_lods[3], neighbour_refined[3]) < my_cell,
    ];

    let cells = if self_refines {
        shape.cells * 2
    } else {
        shape.cells
    };
    let cell_size = if self_refines {
        voxel_size * 0.5
    } else {
        voxel_size
    };

    let mut boxes = Vec::new();
    for iz in 0..shape.per_edge {
        for ix in 0..shape.per_edge {
            for iy in iy0..iy1 {
                let origin = Vec3::new(
                    x0 + ix as f32 * extent,
                    iy as f32 * extent,
                    z0 + iz as f32 * extent,
                );
                let bounds = Bounds3::from_corners(origin, origin + Vec3::splat(extent));
                if field.region_state(base, &bounds).is_some() {
                    continue;
                }
                boxes.push(VoxelBoxSpec {
                    pos: IVec3::new(
                        coords.x as i32 * shape.per_edge + ix,
                        iy,
                        coords.y as i32 * shape.per_edge + iz,
                    ),
                    origin,
                    extent,
                    cells,
                    voxel_size: cell_size,
                    refined: self_refines,
                    transitions: [
                        ix == 0 && finer[0],
                        ix == shape.per_edge - 1 && finer[1],
                        iz == 0 && finer[2],
                        iz == shape.per_edge - 1 && finer[3],
                    ],
                });
            }
        }
    }
    boxes
}

/// Mesa UMA caixa da coluna (SDF → transvoxel → buffers).
pub fn build_box_mesh(
    spec: &TerrainSpec,
    base: &dyn HeightField,
    field: &VoxelField,
    b: &VoxelBoxSpec,
) -> Option<super::super::mesh::ChunkMeshData> {
    let params = VoxelChunkParams {
        origin: b.origin,
        cells: b.cells,
        voxel_size: b.voxel_size,
        texture_tile_size: spec.texture_tile_size,
        tint: spec.chunk_tint(),
        max_height: spec.max_height,
        uses_layer_material: !spec.layers.is_empty(),
        transitions: b.transitions,
    };
    let density = |p: Vec3| field.density(base, p);
    build_voxel_mesh(&density, &params)
}

/// Spawna a entidade de UMA caixa voxel (filha da coluna). `visible = false`
/// para caixas em construção (staged) — entram visíveis só no swap da
/// coluna completa, para nunca desenhar metade de um LOD ao lado do outro.
#[allow(clippy::too_many_arguments)]
pub(crate) fn spawn_box_entity(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    parent: Entity,
    b: &VoxelBoxSpec,
    data: super::super::mesh::ChunkMeshData,
    material: &ChunkMaterialHandle,
    visible: bool,
) -> Entity {
    let handle = meshes.add(to_bevy_mesh(&data));
    let mut entity = commands.spawn((
        Name::new(format!("voxel chunk {}-{}-{}", b.pos.x, b.pos.y, b.pos.z)),
        VoxelChunk {
            coords: b.pos,
            origin: b.origin,
            extent: b.extent,
            voxel_size: b.voxel_size,
        },
        // Surface-nets positions are origin-relative on all three axes.
        Transform::from_translation(b.origin),
        if visible {
            Visibility::Inherited
        } else {
            Visibility::Hidden
        },
        Mesh3d(handle),
        ChildOf(parent),
    ));
    match material {
        ChunkMaterialHandle::Layer(material) => {
            entity.insert(MeshMaterial3d(material.clone()));
        }
        ChunkMaterialHandle::Standard(material) => {
            entity.insert(MeshMaterial3d(material.clone()));
        }
    }
    entity.id()
}

/// Spawna uma COLUNA inteira: a entidade `TerrainChunk` (o nome `chunk
/// cz-cx` é o contrato de adoção/profiler) mais as caixas de `boxes` já
/// visíveis. Devolve `(entidade, caixas meshadas)`.
///
/// O collider da coluna nasce AQUI: um trimesh com os MESMOS triângulos que
/// o render acabou de assar, na entidade da coluna — mesh e collider são
/// inseparáveis desde o primeiro frame. `collision_anchor` (XZ do player ou
/// câmara) limita o bake à banda de colisão; `None` (testes headless) assa
/// tudo.
#[allow(clippy::too_many_arguments)]
pub(crate) fn spawn_column(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    parent: Entity,
    coords: UVec2,
    lod: u8,
    material: &ChunkMaterialHandle,
    boxes: &[VoxelBoxSpec],
    base: &dyn HeightField,
    field: &VoxelField,
    spec: &TerrainSpec,
    neighbour_lods: [u8; 4],
    edit_rev: u64,
    collision_anchor: Option<Vec2>,
) -> (Entity, usize) {
    let column = commands
        .spawn((
            Name::new(super::super::plugin::chunk_name(coords)),
            Transform::default(),
            Visibility::Inherited,
            ChildOf(parent),
            super::super::plugin::TerrainChunk {
                coords,
                lod,
                built_lod: lod,
                built_neighbours: neighbour_lods,
                built_edit_rev: edit_rev,
            },
        ))
        .id();
    let mut meshed = 0;
    let mut bake = crate::physics::ColumnColliderBake::new();
    for b in boxes {
        if let Some(data) = build_box_mesh(spec, base, field, b) {
            bake.add_mesh_data(b.origin, &data);
            spawn_box_entity(commands, meshes, column, b, data, material, true);
            meshed += 1;
        }
    }
    if meshed > 0
        && spec.collision_resolution > 0
        && collision_anchor.is_none_or(|anchor| column_in_collision_band(anchor, coords, spec))
        && let Some(collider) = bake.bake()
    {
        commands.entity(column).insert((
            collider,
            bevy_rapier3d::prelude::RigidBody::Fixed,
            crate::physics::VoxelCollider,
        ));
    }
    (column, meshed)
}

/// A coluna está na banda de colisão de um ponto XZ do mundo?
pub(crate) fn column_in_collision_band(anchor: Vec2, coords: UVec2, spec: &TerrainSpec) -> bool {
    crate::physics::column_xz_distance(
        Vec3::new(anchor.x, 0.0, anchor.y),
        coords,
        spec.world_size * 0.5,
        super::super::plugin::chunk_edge(spec),
    ) <= crate::physics::collision_keep_within(spec)
}

/// Bootstrap do caminho 100% voxel: spawna as colunas dentro do raio de
/// render ao LOD que a distância da câmara pede (o plugin cuida do resto —
/// respawn à aproximação e refinamento com histerese). Sem câmara (testes
/// headless) tudo nasce em LOD 0, como no heightfield.
#[allow(clippy::too_many_arguments)]
pub fn spawn_voxel_columns(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    parent: Entity,
    spec: &TerrainSpec,
    grid: &BrushGrid,
    field: &VoxelField,
    camera_xz: Option<Vec2>,
    standard: Handle<StandardMaterial>,
    layer_map: Option<&ChunkLayerMap>,
) -> VoxelSpawnStats {
    let mut stats = VoxelSpawnStats::default();
    let edge = super::super::plugin::chunk_edge(spec);
    if !(edge.is_finite() && edge > 0.0) {
        return stats;
    }
    let lod0_cell = super::super::plugin::lod0_step(spec) as f32;
    let max_lod = super::super::plugin::max_lod_for(spec, edge);
    let rows = (spec.world_size / edge).ceil().max(1.0) as u32;
    let half = spec.world_size * 0.5;

    let center_of = |cx: u32, cz: u32| {
        Vec2::new(
            -half + cx as f32 * edge + edge * 0.5,
            -half + cz as f32 * edge + edge * 0.5,
        )
    };

    // Campo de LOD de toda a grelha antes de meshar a primeira caixa: as faces
    // de transição de uma coluna dependem do LOD das quatro vizinhas, e o
    // clamp 2:1 é o que garante que a diferença nunca passa do salto único que
    // o transvoxel sabe fazer a ponte. Sem câmara (testes headless) tudo nasce
    // em LOD 0 e o campo é uniforme.
    let mut lod_field = super::super::plugin::LodField::new(rows);
    let collision_keep = crate::physics::collision_keep_within(spec);
    for cz in 0..rows {
        for cx in 0..rows {
            let distance = camera_xz.map(|cam| cam.distance(center_of(cx, cz)));
            if let Some(distance) = distance
                && distance > spec.effective_render_distance()
            {
                continue;
            }
            let lod = distance
                .map(|d| {
                    let lod = super::super::plugin::raw_lod(d, spec.lod_distance(), max_lod);
                    // Piso de LOD 0 na banda de colisão (só a câmara aqui —
                    // no bootstrap o player ainda não se moveu): o chão que
                    // se pode tocar nasce fino, collider incluído.
                    if super::super::plugin::chunk_aabb_distance(d, edge) < collision_keep {
                        0
                    } else {
                        lod
                    }
                })
                .unwrap_or(0);
            lod_field.set(UVec2::new(cx, cz), lod);
        }
    }
    lod_field.clamp_two_to_one(max_lod);

    let mut commands = world.commands();
    for cz in 0..rows {
        for cx in 0..rows {
            let coords = UVec2::new(cx, cz);
            let Some(lod) = lod_field.get(coords) else {
                continue;
            };
            let neighbours = lod_field.neighbours(coords);
            let refined = refined_neighbours(&lod_field, field, spec, edge, coords);
            let boxes =
                column_boxes(spec, grid, field, edge, lod0_cell, lod, coords, neighbours, refined);
            // Mesma codificação do plugin (`lod | 0x80·refinado`) — o passe
            // de LOD compara contra ISTO; guardar o array cru fazia toda
            // coluna refinada reconstruir uma vez à toa no primeiro frame.
            let nkey: [u8; 4] =
                std::array::from_fn(|i| neighbours[i] | ((refined[i] as u8) << 7));
            stats.chunks += 1;
            let material = layer_map
                .and_then(|m| m.get(cx, cz).cloned())
                .map(ChunkMaterialHandle::Layer)
                .unwrap_or_else(|| ChunkMaterialHandle::Standard(standard.clone()));
            let (_, built) = spawn_column(
                &mut commands,
                meshes,
                parent,
                coords,
                lod,
                &material,
                &boxes,
                grid,
                field,
                spec,
                nkey,
                0, // bootstrap: a revisão de edições ainda é 0
                camera_xz,
            );
            stats.meshed += built;
        }
    }
    stats
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::heightmap::HeightMapU16;
    use crate::terrain::voxel::cave::CaveSpec;
    use crate::terrain::voxel::mods::{BoxMod, ModOp, VoxelMod};
    use bevy::math::Vec2;

    fn flat_grid(world_size: f32, height: f32, max_height: f32) -> BrushGrid {
        let n = 33usize;
        let raw = vec![((height / max_height) * 65535.0).round() as u16; n * n];
        let map = HeightMapU16 {
            width: n,
            depth: n,
            data: raw,
        };
        BrushGrid::from_height_map(&map, world_size, max_height, 0.0).expect("grid")
    }

    /// Ground climbing along +X, so a region spans real relief.
    fn sloped_grid(world_size: f32, max_height: f32) -> BrushGrid {
        let n = 33usize;
        let mut raw = vec![0u16; n * n];
        for z in 0..n {
            for x in 0..n {
                let f = x as f32 / (n - 1) as f32;
                raw[z * n + x] = (f * 65535.0).round() as u16;
            }
        }
        let map = HeightMapU16 {
            width: n,
            depth: n,
            data: raw,
        };
        BrushGrid::from_height_map(&map, world_size, max_height, 0.0).expect("grid")
    }

    fn shelf_field(world_size: f32) -> VoxelField {
        let shelf: Box<dyn VoxelMod> = Box::new(BoxMod::new(
            "shelf",
            Bounds3::from_corners(Vec3::new(-16.0, 24.0, -16.0), Vec3::new(16.0, 32.0, 16.0)),
            ModOp::Union,
        ));
        VoxelField::new(vec![shelf], world_size, 64.0)
    }

    /// Gate de refinamento: colunas sobre mods, ao LOD 0 com vizinhos a
    /// LOD 0, dobram as células; longe dos mods ou ao LOD 1, não.
    #[test]
    fn test_refinement_halves_the_cell_only_near_mods() {
        if std::env::var("VIBER_VOXEL_REFINE").as_deref() != Ok("1") {
            return; // gate é opt-in: sem a env, nada a provar aqui
        }
        let world_size = 256.0_f32;
        let grid = flat_grid(world_size, 10.0, 100.0);
        let spec = TerrainSpec {
            world_size,
            ..TerrainSpec::default()
        };
        let field = shelf_field(world_size); // mod em [-16..16]² → chunk (2,2)
        let edge = 64.0_f32;
        let coords = UVec2::new(2, 2);
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            edge,
            1.0,
            0,
            coords,
            [NO_NEIGHBOUR; 4],
            [false; 4],
        );
        assert!(
            boxes.iter().any(|b| b.refined && b.cells == 64 && b.voxel_size == 0.5),
            "coluna sobre o mod refina a meio célula: {:?}",
            boxes.first().map(|b| (b.cells, b.voxel_size, b.refined))
        );
        // Chunk SEM mods: o passo normal mantém-se.
        let far = column_boxes(
            &spec,
            &grid,
            &field,
            edge,
            1.0,
            0,
            UVec2::new(0, 0),
            [NO_NEIGHBOUR; 4],
            [false; 4],
        );
        assert!(
            far.iter().all(|b| !b.refined && b.cells == 32),
            "coluna longa dos mods não refina"
        );
        // Ao LOD 1: nunca refina (o teto 2:1 proíbe).
        let coarse = column_boxes(
            &spec,
            &grid,
            &field,
            edge,
            1.0,
            1,
            coords,
            [NO_NEIGHBOUR; 4],
            [false; 4],
        );
        assert!(coarse.iter().all(|b| !b.refined), "LOD 1 não refina");
        let _ = coords; // (o comentário acima explica a escolha)
    }

    /// Um vizinho REFINADO é mais fino em célula efetiva: a coluna
    /// normal (grosseira) constrói a ponte — e só na caixa da face.
    #[test]
    fn test_unrefined_column_bridges_to_a_refined_neighbour() {
        // Campo SEM mods na coluna sob teste (a coluna refinada é a
        // vizinha — o caso real: a borda do blob de mods).
        let grid = flat_grid(256.0, 10.0, 100.0);
        let spec = TerrainSpec::default();
        let field: VoxelField = VoxelField::new(Vec::new(), 256.0, 64.0);
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            64.0,
            1.0,
            0,
            UVec2::new(1, 1),
            [0, 0, 0, 0],
            [true, false, false, false],
        );
        assert!(!boxes.is_empty());
        for b in &boxes {
            let lx = ((b.origin.x - (-128.0 + 64.0)) / b.extent).round() as i32;
            if lx == 0 {
                assert!(b.transitions[0], "face −X ponteia o vizinho refinado: {b:?}");
            } else {
                assert!(!b.transitions[0], "fiada interior não ponteia: {b:?}");
            }
        }
        // Sem refinamento do vizinho: igual LOD, nenhuma ponte.
        let plain = column_boxes(
            &spec,
            &grid,
            &field,
            64.0,
            1.0,
            0,
            UVec2::new(1, 1),
            [0, 0, 0, 0],
            [false; 4],
        );
        for b in &plain {
            assert!(!b.transitions[0]);
        }
    }

    #[test]
    fn test_lod_shape_ladder_doubles_the_cell() {
        // Chunk 64 m, célula LOD-0 de 1 m: LOD0 = 2×2 caixas de 32³ @1 m,
        // LOD1 = 1 caixa @2 m, LOD2 = 16³ @4 m (32 células a 4 m não
        // ladrilham 64 m — a 16 sim).
        assert_eq!(
            lod_shape(1.0, 64.0, 0),
            VoxelLodShape {
                cells: 32,
                per_edge: 2
            }
        );
        assert_eq!(
            lod_shape(1.0, 64.0, 1),
            VoxelLodShape {
                cells: 32,
                per_edge: 1
            }
        );
        assert_eq!(
            lod_shape(1.0, 64.0, 2),
            VoxelLodShape {
                cells: 16,
                per_edge: 1
            }
        );
        // Edge 16 (spec pequena dos testes): tudo numa caixa por coluna.
        assert_eq!(
            lod_shape(1.0, 16.0, 0),
            VoxelLodShape {
                cells: 16,
                per_edge: 1
            }
        );
        assert_eq!(
            lod_shape(1.0, 16.0, 2),
            VoxelLodShape {
                cells: 4,
                per_edge: 1
            }
        );
    }

    /// O invariante que evitava o moiré: as caixas ladrilham a COLUNA
    /// exatamente — dentro da célula da grelha e sobre o lattice derivado da
    /// borda. Com tudo voxel, o mesmo contrato garante que colunas vizinhas
    /// ao mesmo LOD partilham vértices por coincidência.
    #[test]
    fn test_boxes_tile_the_column_exactly() {
        for world_size in [256.0_f32, 300.0, 4000.0] {
            let grid = flat_grid(world_size, 10.0, 100.0);
            let spec = TerrainSpec {
                world_size,
                ..TerrainSpec::default()
            };
            let field = shelf_field(world_size);
            let edge = 64.0_f32;
            let coords = UVec2::new(2, 1);
            let boxes = column_boxes(
                &spec,
                &grid,
                &field,
                edge,
                1.0,
                0,
                coords,
                [NO_NEIGHBOUR; 4],
                [false; 4],
            );
            assert!(!boxes.is_empty(), "world {world_size}: nothing planned");

            let half = world_size * 0.5;
            let x0 = -half + coords.x as f32 * edge;
            let z0 = -half + coords.y as f32 * edge;
            for b in &boxes {
                assert!(
                    b.origin.x >= x0 - 0.01
                        && b.origin.x + b.extent <= x0 + edge + 0.01
                        && b.origin.z >= z0 - 0.01
                        && b.origin.z + b.extent <= z0 + edge + 0.01,
                    "box {b:?} overruns its column"
                );
                assert!(
                    ((b.origin.x - x0) / b.extent).fract().abs() < 1e-4,
                    "box not on the column lattice: {b:?}"
                );
            }
        }
    }

    /// Mundo sem mods: as caixas de coluna continuam a cobrir a superfície
    /// (o heightfield é só o termo-base agora) e o mesh sai ao nível certo.
    #[test]
    fn test_a_flat_world_boxes_cover_the_surface() {
        let grid = flat_grid(256.0, 10.0, 100.0);
        let spec = TerrainSpec::default();
        let field = VoxelField::flat(256.0, 64.0);
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            64.0,
            1.0,
            0,
            UVec2::new(1, 1),
            [NO_NEIGHBOUR; 4],
            [false; 4],
        );
        assert!(!boxes.is_empty(), "flat ground must still be meshed");
        let mut meshed = 0;
        for b in &boxes {
            if let Some(data) = build_box_mesh(&spec, &grid, &field, b) {
                meshed += 1;
                for (i, pos) in data.positions.iter().enumerate() {
                    // Os vértices de selo pendem seal_depth abaixo da
                    // superfície — só a pele para cima tem de acertar o nível.
                    if data.normals[i][1] < 0.5 {
                        continue;
                    }
                    assert!(
                        (10.0 - (b.origin.y + pos[1])).abs() < 1.01,
                        "vertex off the flat surface: {pos:?}"
                    );
                }
            }
        }
        assert!(meshed > 0, "at least one box meshes the surface");
    }

    /// A prova `region_state` continua a pagar o aluguer: relevo profundo
    /// deixa a maioria das caixas do envelope por amostrar.
    #[test]
    fn test_real_relief_lets_most_boxes_be_proven_uniform() {
        let grid = sloped_grid(256.0, 200.0);
        let spec = TerrainSpec {
            world_size: 256.0,
            max_height: 200.0,
            ..TerrainSpec::default()
        };
        let wall: Box<dyn VoxelMod> = Box::new(BoxMod::new(
            "wall",
            Bounds3::from_corners(Vec3::new(-64.0, 100.0, -8.0), Vec3::new(64.0, 130.0, 8.0)),
            ModOp::Union,
        ));
        let field = VoxelField::new(vec![wall], 256.0, 64.0);
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            64.0,
            1.0,
            0,
            UVec2::new(1, 1),
            [NO_NEIGHBOUR; 4],
            [false; 4],
        );
        assert!(!boxes.is_empty(), "the wall column must plan boxes");
        // Sem a prova seriam 2×2×(200/32) ≈ 26 caixas por coluna.
        assert!(
            boxes.len() < 12,
            "{} boxes planned — region_state is not pruning",
            boxes.len()
        );
    }

    #[test]
    fn test_a_cave_produces_a_ceiling() {
        let grid = flat_grid(256.0, 60.0, 100.0);
        let spec = TerrainSpec {
            world_size: 256.0,
            max_height: 100.0,
            ..TerrainSpec::default()
        };
        let cave = CaveSpec {
            name: Some("mina".into()),
            path: vec![Vec2::new(-60.0, 0.0), Vec2::new(60.0, 0.0)],
            radius: vec![3.5],
            depth: 12.0,
            open_ends: true,
            ..CaveSpec::default()
        };
        let field = VoxelField::new(cave.build(&grid), 256.0, 64.0);
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            64.0,
            1.0,
            0,
            UVec2::new(1, 1),
            [NO_NEIGHBOUR; 4],
            [false; 4],
        );
        let mut downward = 0usize;
        for b in &boxes {
            if let Some(data) = build_box_mesh(&spec, &grid, &field, b) {
                downward += data.normals.iter().filter(|n| n[1] < -0.5).count();
            }
        }
        assert!(
            downward > 0,
            "the cave meshed no downward-facing surface — there is no roof"
        );
    }

    /// Vizinhos ao MESMO LOD (ou ausentes) não pedem transição nenhuma: as
    /// caixas alinham célula a célula e a costura fecha por coincidência.
    #[test]
    fn test_equal_neighbours_ask_for_no_transition() {
        let grid = flat_grid(256.0, 10.0, 100.0);
        let spec = TerrainSpec::default();
        let field = shelf_field(256.0);
        for neighbours in [[NO_NEIGHBOUR; 4], [1u8; 4]] {
            let boxes = column_boxes(
                &spec,
                &grid,
                &field,
                64.0,
                1.0,
                1,
                UVec2::new(1, 1),
                neighbours,
                [false; 4],
            );
            assert!(!boxes.is_empty());
            for b in &boxes {
                assert_eq!(b.transitions, [false; 4], "sem degrau de LOD: {b:?}");
            }
        }
    }

    /// Um vizinho mais FINO põe células de transição — e só na caixa que toca
    /// essa face da coluna. Quem gera a ponte é o bloco grosseiro.
    #[test]
    fn test_only_the_boundary_box_facing_a_finer_neighbour_transitions() {
        let grid = flat_grid(256.0, 10.0, 100.0);
        let spec = TerrainSpec::default();
        let field = shelf_field(256.0);
        // Coluna a LOD 1 (1 caixa de 64 m por nível) com o vizinho −X a LOD 0.
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            64.0,
            1.0,
            1,
            UVec2::new(1, 1),
            [0, 1, 1, 1],
            [false; 4],
        );
        assert!(!boxes.is_empty());
        for b in &boxes {
            assert_eq!(b.transitions, [true, false, false, false], "só −X: {b:?}");
        }

        // A LOD 0 a coluna tem 2×2 caixas: a fiada interior não toca a
        // fronteira e nunca transiciona, mesmo com o vizinho declarado.
        let boxes = column_boxes(
            &spec,
            &grid,
            &field,
            64.0,
            1.0,
            0,
            UVec2::new(1, 1),
            [0, 0, 0, 0],
            [false; 4],
        );
        let half = 128.0_f32;
        let x0 = -half + 64.0;
        for b in &boxes {
            let lx = ((b.origin.x - x0) / b.extent).round() as i32;
            // Vizinho ao MESMO LOD 0: nunca é mais fino do que nós.
            assert!(!b.transitions[0], "vizinho igual não pede ponte: {b:?}");
            let _ = lx;
        }
    }
}
