//! The moving navmesh tile.
//!
//! The world is 4 km across and the terrain colliders only stream within a few
//! chunks of the player, so a world-wide navmesh is neither cheap nor
//! meaningful. It is also unnecessary: every scripted creature is gated to
//! `ScriptActivation::radius` (45 m by default) around the hero, so navigation
//! only ever has to be correct near the hero.
//!
//! So there is exactly one tile, centred on the player, regenerated in the
//! background whenever the player leaves its inner margin. Generation is async
//! (rerecast runs it on the task pool); until the first one lands, agents fall
//! back to the beeline they always had.

use bevy::math::bounding::Aabb3d;
use bevy::prelude::*;
use bevy_rerecast::prelude::{NavmeshGenerator, NavmeshSettings};
use bevy_rerecast::rerecast::{AreaType, ConvexVolume};

use crate::terrain::roads::RoadPath;
use crate::terrain::runtime::TerrainRuntime;

/// Area type of a road or a paved square. Everything else walkable keeps
/// `AreaType::DEFAULT_WALKABLE` (255), so the two are the only node types the
/// archipelago has to price.
pub const AREA_ROAD: AreaType = AreaType(1);

/// How far below a road's sampled surface the area marking reaches (m). Deep
/// enough to survive the slope of a segment, shallow enough not to paint a cave
/// floor that happens to run under the road.
pub const ROAD_BAND_BELOW: f32 = 3.0;
/// How far above (m) — under head height, so a bridge deck over a road does not
/// take the road's price.
pub const ROAD_BAND_ABOVE: f32 = 1.5;

/// State of the single navmesh tile.
#[derive(Resource, Default)]
pub struct NavTile {
    /// Centre of the tile currently generated or generating.
    pub center: Option<Vec2>,
    /// Handle of the navmesh being generated (or the live one).
    pub handle: Option<Handle<bevy_rerecast::Navmesh>>,
    /// A generation is in flight: do not queue another.
    pub generating: bool,
    /// Tiles generated so far — the counter the tests and the profiler read.
    pub generations: u32,
    /// How many static obstacles the live tile was baked with.
    ///
    /// The world does not exist all at once: colliders bake from glTF on a
    /// later frame, spawner groups stream in, destructibles despawn. A navmesh
    /// baked on frame one is a navmesh of an empty valley — characters walked
    /// clean through the city wall because the wall had no collider yet when
    /// the tile was made. Re-baking when this number changes is what keeps the
    /// mesh honest without polling geometry every frame.
    pub baked_obstacles: Option<usize>,
}

/// Requests a new tile when the player has walked out of the current one's
/// inner margin (or when there is no tile at all).
#[allow(clippy::too_many_arguments)]
pub fn retile_navmesh(
    mut commands: Commands,
    config: Res<super::NavConfig>,
    terrain: Option<Res<TerrainRuntime>>,
    archipelago: Option<Res<super::NavArchipelago>>,
    mut tile: ResMut<NavTile>,
    mut generator: NavmeshGenerator,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    obstacles: Query<(), (With<bevy_rapier3d::prelude::Collider>, super::backend::ObstacleFilter)>,
) {
    if !config.enabled || tile.generating || terrain.is_none() {
        return;
    }
    let Ok(player) = players.single() else {
        return; // no hero yet: nothing to centre on
    };
    let here = player.translation().xz();
    let obstacle_count = obstacles.iter().count();
    let moved = tile
        .center
        .is_none_or(|center| here.distance(center) >= config.retile_margin);
    let geometry_changed = tile.baked_obstacles != Some(obstacle_count);
    if !moved && !geometry_changed {
        return;
    }

    let settings = tile_settings(&config, here, terrain.as_deref());
    let handle = match tile.handle.clone() {
        // Same handle across regenerations: the island keeps pointing at it and
        // the agents never see a frame without a navmesh.
        Some(handle) => {
            generator.regenerate(&handle, settings);
            handle
        }
        None => {
            let handle = generator.generate(settings);
            // First tile: point the island at it. The component is immutable
            // and its insert hook allocates the landmass side, so this happens
            // exactly once per run.
            if let Some(archipelago) = archipelago {
                commands
                    .entity(archipelago.island)
                    .insert(landmass_rerecast::NavMeshHandle3d(handle.clone()));
            }
            handle
        }
    };
    tile.handle = Some(handle);
    tile.center = Some(here);
    tile.generating = true;
    tile.generations += 1;
    tile.baked_obstacles = Some(obstacle_count);
    info!(
        "nav: tile #{} pedido em ({:.0}, {:.0}) — {} m de lado, {} obstáculos",
        tile.generations, here.x, here.y, config.tile_size, obstacle_count
    );
}

/// The rerecast settings for a tile centred on `center`.
pub fn tile_settings(
    config: &super::NavConfig,
    center: Vec2,
    terrain: Option<&TerrainRuntime>,
) -> NavmeshSettings {
    let half = config.tile_size * 0.5;
    // Vertical extent: the carved world is at most `max_height` tall, plus room
    // for bridge decks and cliff brows above it.
    let max_height = terrain.map(|t| t.spec.max_height).unwrap_or(256.0);
    let mut settings = NavmeshSettings::from_agent_3d(config.agent_radius, config.agent_height);
    settings.walkable_climb = config.walkable_climb;
    settings.walkable_slope_angle = config.max_slope_deg.to_radians();
    settings.aabb = Some(Aabb3d {
        min: Vec3A::new(center.x - half, -max_height, center.y - half),
        max: Vec3A::new(center.x + half, max_height * 2.0, center.y + half),
    });
    if let Some(terrain) = terrain {
        settings.area_volumes = road_area_volumes(terrain, center, half);
    }
    settings
}

/// Convex volumes that paint the roads (and the paved pads) with
/// [`AREA_ROAD`], clipped to the tile.
///
/// Why volumes and not per-triangle areas: `generate_navmesh` calls
/// `TriMesh::mark_walkable_triangles`, which overwrites every area type of the
/// input with `DEFAULT_WALKABLE` before rasterising. Convex volumes are applied
/// *after* that, on the compact heightfield, which is precisely the hook Recast
/// provides for this. Each pair of consecutive road stations becomes one quad,
/// so a curved road is painted as a fan of convex pieces.
pub fn road_area_volumes(terrain: &TerrainRuntime, center: Vec2, half: f32) -> Vec<ConvexVolume> {
    let min = center - Vec2::splat(half);
    let max = center + Vec2::splat(half);
    let mut volumes = Vec::new();
    for road in &terrain.roads {
        volumes.extend(road_quads(terrain, road, min, max));
    }
    // The city square is a flattened pad with a cobble decal on it, not a road
    // ribbon — but it is the most walked surface in the world, so it prices
    // like one.
    for pad in &terrain.pads {
        let half_size = pad.size * 0.5;
        let pad_min = pad.at - half_size;
        let pad_max = pad.at + half_size;
        if pad_max.x < min.x || pad_min.x > max.x || pad_max.y < min.y || pad_min.y > max.y {
            continue;
        }
        volumes.push(ConvexVolume {
            vertices: vec![
                Vec2::new(pad_min.x, pad_min.y),
                Vec2::new(pad_max.x, pad_min.y),
                Vec2::new(pad_max.x, pad_max.y),
                Vec2::new(pad_min.x, pad_max.y),
            ],
            min_y: pad.height - 4.0,
            max_y: pad.height + 4.0,
            area: AREA_ROAD,
        });
    }
    volumes
}

/// One quad per road segment inside the tile.
fn road_quads(
    terrain: &TerrainRuntime,
    road: &RoadPath,
    min: Vec2,
    max: Vec2,
) -> Vec<ConvexVolume> {
    let mut out = Vec::new();
    for window in road.stations.windows(2).enumerate() {
        let (i, pair) = window;
        let (a, b) = (pair[0], pair[1]);
        // Cheap reject: the segment's own bounds against the tile.
        let seg_min = a.min(b);
        let seg_max = a.max(b);
        let widest = road
            .half_width
            .get(i)
            .copied()
            .unwrap_or(2.0)
            .max(road.half_width.get(i + 1).copied().unwrap_or(2.0));
        if seg_max.x + widest < min.x
            || seg_min.x - widest > max.x
            || seg_max.y + widest < min.y
            || seg_min.y - widest > max.y
        {
            continue;
        }
        let along = b - a;
        if along.length_squared() < 1e-6 {
            continue;
        }
        let side = Vec2::new(-along.y, along.x).normalize() * widest;
        // Vertical band around the ribbon itself. Infinite bounds would
        // paint the cave floor under a road and, on a bridge, the river bank
        // below the deck — the band is what keeps the marking on the surface
        // the road actually is.
        let mid = (a + b) * 0.5;
        let y = road
            .deck_y
            .unwrap_or_else(|| terrain.sample_mesh_surface(mid.x, mid.y));
        out.push(ConvexVolume {
            vertices: vec![a - side, a + side, b + side, b - side],
            min_y: y - ROAD_BAND_BELOW,
            max_y: y + ROAD_BAND_ABOVE,
            area: AREA_ROAD,
        });
    }
    out
}
