//! Geometry backend: the world as rerecast sees it.
//!
//! `bevy_rerecast` does not know what a world is made of — it asks a *backend*
//! system for one [`TriMesh`] and voxelises that. The builtin `Mesh3dBackend`
//! is no use here for two reasons: the render meshes are LOD ladders (millions
//! of triangles of foliage that are not obstacles), and the terrain colliders
//! only exist within `physics::PHYSICS_CHUNK_RADIUS` of the player, so half the
//! tile would simply have no ground.
//!
//! So this backend builds the input itself, from the two authorities that are
//! always complete:
//!
//! * **the ground** — sampled straight from [`TerrainRuntime`] on a regular XZ
//!   lattice over the tile. Cells over water or on a cliff face are left out
//!   entirely: a hole in the input is a hole in the navmesh, which is exactly
//!   what "you cannot walk here" means.
//! * **the obstacles** — the static Rapier colliders inside the tile. A
//!   `trimesh` collider (the city walls, the houses, the bridges — the pipeline
//!   bakes a `*_collision.glb` for each) goes in as its own triangles, so a
//!   gateway stays a gateway; anything else goes in as the box of its local
//!   AABB, which is the right approximation for a barrel or a crate.
//!
//! Roads are NOT marked here. `generate_navmesh` calls
//! `TriMesh::mark_walkable_triangles`, which overwrites every area type with
//! `DEFAULT_WALKABLE` before the volumes are applied — the road areas are
//! carried by [`NavmeshSettings::area_volumes`] instead (see
//! [`super::tile::road_area_volumes`]).

use bevy::prelude::*;
use bevy_rapier3d::prelude::Collider;
use bevy_rerecast::prelude::NavmeshSettings;
use bevy_rerecast::rerecast::{AreaType, TriMesh};

use crate::terrain::cliffs::CliffMask;
use crate::terrain::runtime::TerrainRuntime;

/// Static colliders only: a kinematic body is a creature, and creatures are
/// agents that avoid each other at runtime, not holes in the navmesh.
pub type ObstacleFilter = (
    Without<crate::physics::VoxelCollider>,
    Without<crate::ai::AiLocomotion>,
    Without<crate::player::Player>,
);

/// A collider whose local AABB is taller than this is a wall, a cliff body or a
/// building; below it, it is clutter a character steps over rather than around.
///
/// Rerecast would work this out on its own from `walkable_climb`, but feeding
/// it every pebble in the valley costs voxelisation time for nothing.
pub const MIN_OBSTACLE_HEIGHT: f32 = 0.35;

/// Builds the navmesh input for the tile described by `settings.aabb`.
pub fn nav_backend(
    settings: In<NavmeshSettings>,
    config: Res<super::NavConfig>,
    terrain: Option<Res<TerrainRuntime>>,
    cliffs: Option<Res<CliffMask>>,
    obstacles: Query<(&GlobalTransform, &Collider), ObstacleFilter>,
) -> TriMesh {
    let Some(aabb) = settings.aabb else {
        // No tile: an empty input yields an empty navmesh, and the agents fall
        // back to the beeline. Never a panic — navigation is an upgrade over
        // the old behaviour, not a precondition for the world to run.
        return TriMesh::default();
    };
    let min = Vec3::new(aabb.min.x, aabb.min.y, aabb.min.z);
    let max = Vec3::new(aabb.max.x, aabb.max.y, aabb.max.z);

    let mut trimesh = TriMesh::default();
    if let Some(terrain) = terrain.as_deref() {
        trimesh.extend(ground_lattice(
            terrain,
            cliffs.as_deref(),
            min.xz(),
            max.xz(),
            config.ground_cell,
        ));
    }
    let ground_tris = trimesh.indices.len();
    let mut taken = 0usize;
    for (global, collider) in &obstacles {
        if let Some(mesh) = obstacle_trimesh(global, collider, min, max) {
            trimesh.extend(mesh);
            taken += 1;
        }
    }
    if config.debug {
        info!(
            "nav: backend — chão {} triângulos, obstáculos {}/{} ({} triângulos)",
            ground_tris,
            taken,
            obstacles.iter().count(),
            trimesh.indices.len() - ground_tris
        );
    }
    trimesh
}

/// The walkable ground of the tile as a regular lattice of quads.
///
/// Holes (water, cliff faces, outside the heightfield) are simply not emitted.
/// A quad is only emitted when all four of its corners are walkable, so the
/// navmesh edge lands on the last fully solid cell rather than halfway into the
/// lake.
pub fn ground_lattice(
    terrain: &TerrainRuntime,
    cliffs: Option<&CliffMask>,
    min: Vec2,
    max: Vec2,
    cell: f32,
) -> TriMesh {
    let cell = cell.max(0.1);
    let cols = (((max.x - min.x) / cell).ceil() as usize).max(1) + 1;
    let rows = (((max.y - min.y) / cell).ceil() as usize).max(1) + 1;
    // A tile this big is a bug in the caller, not a request: refuse it rather
    // than allocating gigabytes.
    if cols.saturating_mul(rows) > 4_000_000 {
        warn!("nav: lattice {cols}×{rows} pedida — tile grande demais, ignorada");
        return TriMesh::default();
    }

    let mut heights = vec![f32::NAN; cols * rows];
    for row in 0..rows {
        for col in 0..cols {
            let x = min.x + col as f32 * cell;
            let z = min.y + row as f32 * cell;
            if !terrain.in_field(x, z) || terrain.in_water(x, z) {
                continue;
            }
            if cliffs.is_some_and(|mask| mask.is_cliff_at(Vec2::new(x, z))) {
                continue;
            }
            heights[row * cols + col] = terrain.sample_mesh_surface(x, z);
        }
    }

    let mut mesh = TriMesh::default();
    // One vertex per lattice node, emitted lazily so the vertex buffer only
    // holds nodes an emitted quad actually uses.
    let mut vertex_of = vec![u32::MAX; cols * rows];
    let vertex = |mesh: &mut TriMesh, vertex_of: &mut Vec<u32>, index: usize| -> u32 {
        if vertex_of[index] != u32::MAX {
            return vertex_of[index];
        }
        let col = index % cols;
        let row = index / cols;
        let id = mesh.vertices.len() as u32;
        mesh.vertices.push(
            Vec3::new(
                min.x + col as f32 * cell,
                heights[index],
                min.y + row as f32 * cell,
            )
            .into(),
        );
        vertex_of[index] = id;
        id
    };

    for row in 0..rows.saturating_sub(1) {
        for col in 0..cols.saturating_sub(1) {
            let corners = [
                row * cols + col,
                row * cols + col + 1,
                (row + 1) * cols + col + 1,
                (row + 1) * cols + col,
            ];
            if corners.iter().any(|i| heights[*i].is_nan()) {
                continue;
            }
            let ids = corners.map(|i| vertex(&mut mesh, &mut vertex_of, i));
            // Counter-clockwise seen from above (+Y normal) — rerecast keeps a
            // triangle when its normal points up.
            mesh.indices.push(UVec3::new(ids[0], ids[3], ids[1]));
            mesh.indices.push(UVec3::new(ids[1], ids[3], ids[2]));
            mesh.area_types
                .extend([AreaType::DEFAULT_WALKABLE, AreaType::DEFAULT_WALKABLE]);
        }
    }
    mesh
}

/// One static collider as navmesh input, or `None` when it is outside the tile
/// or too low to matter.
fn obstacle_trimesh(
    global: &GlobalTransform,
    collider: &Collider,
    min: Vec3,
    max: Vec3,
) -> Option<TriMesh> {
    // `Collider::raw` already carries the scale (`Collider::scale` folded it
    // in), so only translation and rotation come from the transform —
    // `compute_transform().scale` would apply it a second time.
    let placement = global.compute_transform();
    let iso = Transform {
        translation: placement.translation,
        rotation: placement.rotation,
        scale: Vec3::ONE,
    };
    let local = collider.raw.compute_local_aabb();
    let local_min = Vec3::new(local.mins.x, local.mins.y, local.mins.z);
    let local_max = Vec3::new(local.maxs.x, local.maxs.y, local.maxs.z);
    if local_max.y - local_min.y < MIN_OBSTACLE_HEIGHT {
        return None;
    }
    // Reject on the world AABB of the (rotated) local AABB: cheap, and the
    // false positives it lets through are clipped by the voxelisation anyway.
    let (world_min, world_max) = rotated_aabb(&iso, local_min, local_max);
    if world_max.x < min.x || world_min.x > max.x || world_max.z < min.z || world_min.z > max.z {
        return None;
    }

    let mut mesh = TriMesh::default();
    if let Some(trimesh) = collider.as_trimesh() {
        // The baked collision mesh: gateways, arches and bridge decks stay
        // open because their real geometry is what gets voxelised.
        for (a, b, c) in trimesh.triangles() {
            let base = mesh.vertices.len() as u32;
            for point in [a, b, c] {
                mesh.vertices.push(iso.transform_point(point).into());
            }
            mesh.indices.push(UVec3::new(base, base + 1, base + 2));
            mesh.area_types.push(AreaType::NOT_WALKABLE);
        }
        return Some(mesh);
    }
    Some(box_trimesh(&iso, local_min, local_max))
}

/// World-space AABB of a local AABB placed by `iso`.
fn rotated_aabb(iso: &Transform, local_min: Vec3, local_max: Vec3) -> (Vec3, Vec3) {
    let mut min = Vec3::splat(f32::INFINITY);
    let mut max = Vec3::splat(f32::NEG_INFINITY);
    for corner in aabb_corners(local_min, local_max) {
        let p = iso.transform_point(corner);
        min = min.min(p);
        max = max.max(p);
    }
    (min, max)
}

fn aabb_corners(min: Vec3, max: Vec3) -> [Vec3; 8] {
    [
        Vec3::new(min.x, min.y, min.z),
        Vec3::new(max.x, min.y, min.z),
        Vec3::new(max.x, min.y, max.z),
        Vec3::new(min.x, min.y, max.z),
        Vec3::new(min.x, max.y, min.z),
        Vec3::new(max.x, max.y, min.z),
        Vec3::new(max.x, max.y, max.z),
        Vec3::new(min.x, max.y, max.z),
    ]
}

/// The 12 triangles of a box, as an obstacle.
fn box_trimesh(iso: &Transform, local_min: Vec3, local_max: Vec3) -> TriMesh {
    let mut mesh = TriMesh::default();
    for corner in aabb_corners(local_min, local_max) {
        mesh.vertices.push(iso.transform_point(corner).into());
    }
    // bottom, top, and the four sides — winding does not matter: every face is
    // NOT_WALKABLE, and rerecast only needs them to voxelise as solid.
    const FACES: [[u32; 6]; 6] = [
        [0, 1, 2, 0, 2, 3], // -Y
        [4, 6, 5, 4, 7, 6], // +Y
        [0, 4, 5, 0, 5, 1], // -Z
        [3, 2, 6, 3, 6, 7], // +Z
        [0, 3, 7, 0, 7, 4], // -X
        [1, 5, 6, 1, 6, 2], // +X
    ];
    for face in FACES {
        mesh.indices.push(UVec3::new(face[0], face[1], face[2]));
        mesh.indices.push(UVec3::new(face[3], face[4], face[5]));
        mesh.area_types
            .extend([AreaType::NOT_WALKABLE, AreaType::NOT_WALKABLE]);
    }
    mesh
}
