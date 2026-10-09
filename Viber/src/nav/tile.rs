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

use bevy::prelude::*;
use bevy::shape::Aabb3d;
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
    /// Consecutive generations that died without a navmesh.
    ///
    /// `bevy_rerecast` only *logs* a failed bake ("Invalid contour…" is the one
    /// the `simple-rpg` spawn tile hit): no asset event, no callback. Before
    /// this counter existed `generating` stayed `true` forever, `retile_navmesh`
    /// never ran again, and every creature of the session walked the beeline
    /// ("fora-da-mesh" in `viber.debug.nav()`). Each retry nudges the voxel
    /// grid and eases the contour simplification — the failure is a property
    /// of one particular rasterisation, not of the world.
    pub failures: u32,
    /// Seconds until the in-flight generation is probed again.
    pub probe_cooldown: f32,
}

/// How often (s) an in-flight generation is probed for a silent failure.
pub const NAV_FAILURE_PROBE_SECS: f32 = 0.5;
/// Ceiling (s) of the probe back-off after repeated failures, so a world that
/// can never bake does not keep a core busy re-baking it.
pub const NAV_FAILURE_PROBE_MAX_SECS: f32 = 15.0;

/// Requests a new tile when the player has walked out of the current one's
/// inner margin (or when there is no tile at all).
#[allow(clippy::too_many_arguments)]
pub fn retile_navmesh(
    mut commands: Commands,
    time: Res<Time>,
    config: Res<super::NavConfig>,
    terrain: Option<Res<TerrainRuntime>>,
    archipelago: Option<Res<super::NavArchipelago>>,
    mut tile: ResMut<NavTile>,
    mut generator: NavmeshGenerator,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    obstacles: Query<
        (),
        (
            With<bevy_rapier3d::prelude::Collider>,
            super::backend::ObstacleFilter,
        ),
    >,
) {
    if !config.enabled || terrain.is_none() {
        return;
    }
    if tile.generating {
        probe_in_flight(
            &mut tile,
            &config,
            terrain.as_deref(),
            &mut generator,
            time.delta_secs(),
        );
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

    let settings = tile_settings_attempt(&config, here, terrain.as_deref(), tile.failures);
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
    tile.probe_cooldown = NAV_FAILURE_PROBE_SECS;
    tile.generations += 1;
    tile.baked_obstacles = Some(obstacle_count);
    info!(
        "nav: tile #{} pedido em ({:.0}, {:.0}) — {} m de lado, {} obstáculos",
        tile.generations, here.x, here.y, config.tile_size, obstacle_count
    );
}

/// Checks whether the in-flight generation died without a navmesh and, if
/// so, queues the retry.
///
/// `NavmeshGenerator::regenerate` refuses (returns `false`) while the handle
/// is still queued or baking — so a probe that is *accepted* means the bake is
/// gone, and since success clears `generating` first (the [`NavmeshReady`]
/// observer, flushed in the `PostUpdate` that polled the task), gone means
/// failed. The accepted probe *is* the retry: it carries the next attempt's
/// nudged settings.
///
/// [`NavmeshReady`]: bevy_rerecast::prelude::NavmeshReady
fn probe_in_flight(
    tile: &mut NavTile,
    config: &super::NavConfig,
    terrain: Option<&TerrainRuntime>,
    generator: &mut NavmeshGenerator,
    dt: f32,
) {
    tile.probe_cooldown -= dt;
    if tile.probe_cooldown > 0.0 {
        return;
    }
    let (Some(handle), Some(center)) = (tile.handle.clone(), tile.center) else {
        return;
    };
    let attempt = tile.failures + 1;
    tile.probe_cooldown = failure_backoff(attempt);
    let settings = tile_settings_attempt(config, center, terrain, attempt);
    if generator.regenerate(&handle, settings) {
        tile.failures = attempt;
        warn!(
            "nav: tile #{} falhou sem navmesh (tentativa {attempt}) — a repetir com a grelha deslocada",
            tile.generations
        );
    }
}

/// Probe period after `failures` consecutive failed bakes: doubles from
/// [`NAV_FAILURE_PROBE_SECS`] up to [`NAV_FAILURE_PROBE_MAX_SECS`].
pub fn failure_backoff(failures: u32) -> f32 {
    let doubled = NAV_FAILURE_PROBE_SECS * 2f32.powi(failures.min(8) as i32);
    doubled.min(NAV_FAILURE_PROBE_MAX_SECS)
}

/// The rerecast settings for a tile centred on `center`.
pub fn tile_settings(
    config: &super::NavConfig,
    center: Vec2,
    terrain: Option<&TerrainRuntime>,
) -> NavmeshSettings {
    tile_settings_attempt(config, center, terrain, 0)
}

/// [`tile_settings`] for the `attempt`-th retry after failed bakes.
///
/// Attempt 0 is the plain tile. Each retry shifts the tile's AABB — and with
/// it the whole voxel grid — by a fraction of a cell that never repeats (the
/// golden ratio), and relaxes the contour simplification one notch: the
/// "Invalid contour" failure comes from a simplified contour folding over
/// itself, which is a property of one exact rasterisation.
pub fn tile_settings_attempt(
    config: &super::NavConfig,
    center: Vec2,
    terrain: Option<&TerrainRuntime>,
    attempt: u32,
) -> NavmeshSettings {
    let center = center + attempt_offset(config, attempt);
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
    if attempt > 0 {
        settings.max_simplification_error = (settings.max_simplification_error
            - 0.2 * attempt as f32)
            .max(MIN_SIMPLIFICATION_ERROR);
    }
    settings
}

/// Floor of the relaxed contour simplification on retries (voxels). Below
/// ~0.5 the contours keep every stair-step of the rasterisation and the
/// polygon count explodes for no navigational gain.
const MIN_SIMPLIFICATION_ERROR: f32 = 0.5;

/// Sub-cell shift of the tile for the `attempt`-th retry (zero on attempt 0).
pub fn attempt_offset(config: &super::NavConfig, attempt: u32) -> Vec2 {
    if attempt == 0 {
        return Vec2::ZERO;
    }
    // Cell size as rerecast derives it (`agent_radius / cell_size_fraction`,
    // fraction 3 by default); the fractional part of k·φ walks the cell
    // without ever landing on the same phase twice.
    let cell = config.agent_radius / 3.0;
    const PHI: f32 = 0.618_034;
    let phase_x = (attempt as f32 * PHI).fract();
    let phase_z = (attempt as f32 * PHI * PHI).fract();
    Vec2::new(phase_x, phase_z) * cell
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nav::NavConfig;

    #[test]
    fn test_failure_backoff_doubles_and_caps() {
        assert_eq!(failure_backoff(0), NAV_FAILURE_PROBE_SECS);
        assert_eq!(failure_backoff(1), NAV_FAILURE_PROBE_SECS * 2.0);
        assert_eq!(failure_backoff(2), NAV_FAILURE_PROBE_SECS * 4.0);
        assert_eq!(failure_backoff(50), NAV_FAILURE_PROBE_MAX_SECS);
    }

    #[test]
    fn test_attempt_offset_is_zero_first_then_sub_cell_and_distinct() {
        let config = NavConfig::default();
        let cell = config.agent_radius / 3.0;
        assert_eq!(attempt_offset(&config, 0), Vec2::ZERO);
        let offsets: Vec<Vec2> = (1..6).map(|k| attempt_offset(&config, k)).collect();
        for offset in &offsets {
            assert!(offset.x >= 0.0 && offset.x < cell && offset.y >= 0.0 && offset.y < cell);
        }
        for (i, a) in offsets.iter().enumerate() {
            for b in &offsets[i + 1..] {
                assert!(a.distance(*b) > 1e-3, "each retry rasterises differently");
            }
        }
    }

    #[test]
    fn test_retry_settings_shift_the_tile_and_relax_simplification() {
        let config = NavConfig::default();
        let base = tile_settings(&config, Vec2::ZERO, None);
        let retry = tile_settings_attempt(&config, Vec2::ZERO, None, 1);
        assert_ne!(base.aabb.unwrap().min, retry.aabb.unwrap().min);
        assert!(retry.max_simplification_error < base.max_simplification_error);
        let deep = tile_settings_attempt(&config, Vec2::ZERO, None, 40);
        assert_eq!(deep.max_simplification_error, MIN_SIMPLIFICATION_ERROR);
    }
}
