//! Navigation: the navmesh input, the road pricing and the beeline fallback.
//!
//! Everything here is headless — no window, no render plugins. The expensive
//! half of navigation (rerecast's voxelisation) is exercised through the pieces
//! that decide *what* it voxelises and *how the answer is priced*, which is
//! where the engine's own decisions live; Recast itself has its own test suite
//! upstream.

use bevy::prelude::*;
use std::sync::Arc;
use viber::nav::agent::{GROUND_TYPE_INDEX, ROAD_TYPE_INDEX, default_profile};
use viber::nav::tile::{AREA_ROAD, road_area_volumes, tile_settings};
use viber::nav::{NavConfig, NavProfile};
use viber::terrain::brush::BrushGrid;
use viber::terrain::heightmap::HeightMapU16;
use viber::terrain::roads::{RoadPath, RoadProfile};
use viber::terrain::runtime::TerrainRuntime;
use viber::terrain::spec::TerrainSpec;

/// A flat-ish carved world with one straight road along +X through the origin.
fn world_with_a_road() -> TerrainRuntime {
    let spec = TerrainSpec {
        world_size: 256.0,
        max_height: 8.0,
        seed: 11,
        ..TerrainSpec::default()
    };
    let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
    let grid = BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    )
    .expect("grid builds");
    let stations: Vec<Vec2> = (-30..=30).map(|i| Vec2::new(i as f32 * 2.0, 0.0)).collect();
    let half_width = vec![2.4; stations.len()];
    TerrainRuntime {
        spec,
        grid: Arc::new(grid),
        water: vec![],
        roads: vec![RoadPath {
            name: Some("net/a-b".into()),
            stations,
            half_width,
            profile: RoadProfile::Artery,
            bridge: false,
            deck_y: None,
        }],
        pads: vec![],
        voxel: Arc::new(viber::terrain::voxel::VoxelField::default()),
        deltas: std::sync::Arc::new(viber::terrain::delta::DeltaGrid::default()),
    }
}

/// The ground lattice stops at the water instead of paving over it: a hole in
/// the navmesh input is what "you cannot walk here" means.
#[test]
fn ground_lattice_leaves_holes_where_the_world_is_not_walkable() {
    let terrain = world_with_a_road();
    let full = viber::nav::backend::ground_lattice(
        &terrain,
        None,
        Vec2::splat(-20.0),
        Vec2::splat(20.0),
        1.0,
    );
    assert!(
        !full.indices.is_empty(),
        "dry ground produces a lattice ({} triângulos)",
        full.indices.len()
    );
    assert_eq!(
        full.indices.len(),
        full.area_types.len(),
        "rerecast exige uma área por triângulo"
    );

    // The same tile, now mostly lake.
    let mut flooded = world_with_a_road();
    flooded.water = vec![viber::terrain::water::WaterBody {
        kind: viber::terrain::water::WaterKind::Lake,
        at: Vec2::ZERO,
        radius: 30.0,
        carve_radius: 30.0,
        water_y: 4.0,
        mirror_reach: 1.0,
        shape: viber::terrain::water::LakeShape::default(),
        stations: vec![],
        surface_y: vec![],
        water_width: 0.0,
        half_width: vec![],
        depths: vec![],
        cascades: vec![],
    }];
    let wet = viber::nav::backend::ground_lattice(
        &flooded,
        None,
        Vec2::splat(-20.0),
        Vec2::splat(20.0),
        1.0,
    );
    assert!(
        wet.indices.len() * 4 < full.indices.len(),
        "um lago no meio do tile tem de abrir um buraco grande: {} vs {} triângulos",
        wet.indices.len(),
        full.indices.len()
    );
}

/// The road is handed to rerecast as convex volumes, because the generator
/// overwrites per-triangle areas before it rasterises. Each volume covers the
/// ribbon and nothing else.
#[test]
fn roads_become_convex_area_volumes_clipped_to_the_tile() {
    let terrain = world_with_a_road();
    let volumes = road_area_volumes(&terrain, Vec2::ZERO, 20.0);
    assert!(!volumes.is_empty(), "a estrada atravessa o tile");
    for volume in &volumes {
        assert_eq!(volume.area, AREA_ROAD, "as áreas marcadas são de estrada");
        assert_eq!(volume.vertices.len(), 4, "cada segmento é um quad convexo");
        assert!(
            volume.max_y > volume.min_y,
            "a banda vertical tem de ser finita e positiva ({} → {})",
            volume.min_y,
            volume.max_y
        );
        assert!(
            volume.min_y.is_finite() && volume.max_y.is_finite(),
            "bandas infinitas pintariam o chão de uma gruta por baixo da estrada"
        );
    }

    // A tile far from the road gets nothing — the clipping is real, not
    // decorative: every volume costs voxelisation time.
    let elsewhere = road_area_volumes(&terrain, Vec2::new(0.0, 400.0), 20.0);
    assert!(
        elsewhere.is_empty(),
        "um tile longe da estrada não paga por ela ({} volumes)",
        elsewhere.len()
    );
}

/// A paved square (a `TerrainPad`) prices like a road: it is the most walked
/// surface in the city and it is not a road ribbon.
#[test]
fn a_paved_pad_is_priced_as_a_road() {
    let mut terrain = world_with_a_road();
    terrain.roads.clear();
    terrain.pads = vec![viber::terrain::sampler::ResolvedPad {
        at: Vec2::ZERO,
        size: Vec2::splat(20.0),
        falloff: 4.0,
        corner_radius: 2.0,
        height: 12.0,
    }];
    let volumes = road_area_volumes(&terrain, Vec2::ZERO, 30.0);
    assert_eq!(volumes.len(), 1, "um pad, um volume");
    assert_eq!(volumes[0].area, AREA_ROAD);
    assert!(
        volumes[0].min_y < 12.0 && volumes[0].max_y > 12.0,
        "a banda envolve a altura do pad"
    );
}

/// The tile follows the player and is finite in every axis — an unbounded AABB
/// would ask rerecast to voxelise the whole 4 km world.
#[test]
fn the_tile_is_centred_on_the_player_and_bounded() {
    let terrain = world_with_a_road();
    let config = NavConfig::default();
    let settings = tile_settings(&config, Vec2::new(100.0, -40.0), Some(&terrain));
    let aabb = settings.aabb.expect("o tile tem AABB");
    let half = config.tile_size * 0.5;
    assert!((aabb.min.x - (100.0 - half)).abs() < 1e-3);
    assert!((aabb.max.z - (-40.0 + half)).abs() < 1e-3);
    assert!(
        aabb.min.y.is_finite() && aabb.max.y.is_finite(),
        "a extensão vertical é finita"
    );
    assert_eq!(
        settings.walkable_slope_angle,
        config.max_slope_deg.to_radians(),
        "o declive máximo vem da config, não do default do crate"
    );
}

/// Who prefers roads is decided by hostility, which the engine already knows —
/// not by a second marker for the world author to keep in sync.
#[test]
fn hostility_decides_who_walks_the_streets() {
    let enemy = viber::luau::LuaScriptRef {
        path: "enemies/wolf.lua".into(),
    };
    let boss = viber::luau::LuaScriptRef {
        path: "bosses/witch.lua".into(),
    };
    let townsfolk = viber::luau::LuaScriptRef {
        path: "townsfolk.lua".into(),
    };
    assert_eq!(default_profile(Some(&enemy), false), NavProfile::Wild);
    assert_eq!(default_profile(Some(&boss), false), NavProfile::Wild);
    assert_eq!(default_profile(Some(&townsfolk), false), NavProfile::Civil);
    // A Rust-FSM creature has no script at all and is always a hunter.
    assert_eq!(default_profile(None, true), NavProfile::Wild);
    // Anything else scripted (a harvestable prop that walks, an NPC) is civil.
    assert_eq!(default_profile(None, false), NavProfile::Civil);
    assert_ne!(
        ROAD_TYPE_INDEX, GROUND_TYPE_INDEX,
        "estrada e chão têm de ser tipos distintos ou não há preferência a dar"
    );
}

/// `VIBER_NAV=0` is the documented way back to the old behaviour, and it has to
/// keep working without touching a world file.
#[test]
fn the_stack_can_be_turned_off_from_the_environment() {
    // SAFETY: single-threaded test body; the var is read immediately after.
    unsafe { std::env::set_var("VIBER_NAV", "0") };
    assert!(!NavConfig::from_env().enabled);
    unsafe { std::env::set_var("VIBER_NAV", "1") };
    assert!(NavConfig::from_env().enabled);
    unsafe { std::env::remove_var("VIBER_NAV") };
    assert!(NavConfig::from_env().enabled, "ligado por omissão");
}

/// `<NavMesh …>` was parsed and thrown away since the recipes were written.
/// Now it configures the stack — and a typo in it costs nothing.
#[test]
fn the_navmesh_tag_finally_configures_something() {
    let mut config = NavConfig::default();
    config.apply_attrs(&[
        ("agent-radius".into(), "0.8".into()),
        ("tile-size".into(), "128".into()),
        ("offroad-cost".into(), "4".into()),
        ("max-slope".into(), "38".into()),
        ("nonsense".into(), "42".into()),
        ("agent-height".into(), "não é um número".into()),
    ]);
    assert_eq!(config.agent_radius, 0.8);
    assert_eq!(config.tile_size, 128.0);
    assert_eq!(config.offroad_cost, 4.0);
    assert_eq!(config.max_slope_deg, 38.0);
    assert_eq!(
        config.agent_height,
        NavConfig::default().agent_height,
        "um valor ilegível deixa o default de pé"
    );
    assert!(
        config.retile_margin <= config.tile_size * 0.5,
        "a margem nunca pode ser maior que meio tile, ou o retile nunca dispara"
    );

    // Absurd values are clamped into a range the generator can actually serve.
    let mut config = NavConfig::default();
    config.apply_attrs(&[
        ("max-slope".into(), "180".into()),
        ("offroad-cost".into(), "0.1".into()),
        ("tile-size".into(), "1".into()),
    ]);
    assert_eq!(config.max_slope_deg, 89.0);
    assert_eq!(config.offroad_cost, 1.0, "custo < 1 inverteria a preferência");
    assert_eq!(config.tile_size, 16.0);
}
