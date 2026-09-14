//! Navigation: a real navmesh, real pathfinding and real local avoidance.
//!
//! Before this module the AI moved in a straight line toward whatever point it
//! wanted and wrote the result into `Transform`. Creatures walked through
//! houses, through walls and through each other; the only obstacle handling in
//! the whole repository was a per-script `if the distance stopped shrinking for
//! six seconds, pick somewhere else`. The `<NavMesh>` tag was parsed and thrown
//! away.
//!
//! The stack is three crates, all built against Bevy 0.19 (see
//! `docs/CRATES.md`):
//!
//! * **`bevy_rerecast`** — Recast, the industry-standard navmesh voxeliser,
//!   ported to Rust. It asks a *backend* for the world geometry; ours is
//!   [`backend::nav_backend`], because neither the render meshes nor the
//!   streamed terrain colliders describe the world completely.
//! * **`landmass_rerecast`** — the bridge.
//! * **`bevy_landmass`** — A* over the navmesh, RVO avoidance between agents,
//!   and a cost multiplier per polygon type. That last one is the whole of
//!   "prefer roads": the road polygons are one node type, the ground is
//!   another, and a townsfolk simply prices the ground higher.
//!
//! Three properties are deliberate:
//!
//! * **One moving tile.** The world is 4 km wide and every script is gated to
//!   45 m around the hero, so a world navmesh would be expense without effect.
//!   See [`tile`].
//! * **No new API for the scripts.** `viber.move_towards` keeps its signature;
//!   [`agent`] intercepts the ask and returns a navigated one. All seventeen
//!   scripts in `examples/simple-rpg` improve without an edit.
//! * **Failure is the old behaviour.** No navmesh, agent off the mesh, no path,
//!   `VIBER_NAV=0` — the character beelines exactly as it used to. Navigation
//!   can make the world better, never worse.
//!
//! Wiring: `app.add_plugins(crate::nav::NavPlugin);`

pub mod agent;
pub mod backend;
pub mod tile;

use bevy::prelude::*;
use bevy_landmass::prelude::*;
use bevy_rerecast::prelude::*;

pub use agent::{NavArchipelago, NavProfile};
pub use tile::NavTile;

/// Default agent radius (m) — the fattest creature in the example plus a
/// little, so one navmesh serves every rig instead of one per species.
pub const DEFAULT_AGENT_RADIUS: f32 = 0.5;
/// Default agent height (m).
pub const DEFAULT_AGENT_HEIGHT: f32 = 1.9;
/// Default side of the navmesh tile (m).
///
/// 256 m around the hero covers the 45 m activation radius with room for the
/// hero to keep walking while the next tile bakes.
pub const DEFAULT_TILE_SIZE: f32 = 256.0;
/// The hero has to walk this far (m) from the tile centre before the next tile
/// is requested. Half the spare margin, so a generation never races the edge.
pub const DEFAULT_RETILE_MARGIN: f32 = 64.0;
/// Side (m) of a ground lattice cell handed to the voxeliser.
pub const DEFAULT_GROUND_CELL: f32 = 0.75;
/// What walking overland costs a civil profile, relative to a road.
pub const DEFAULT_OFFROAD_COST: f32 = 2.5;

/// Tunables of the navigation stack, filled from `<NavMesh …>` in the world XML
/// (see `crate::recipes`) and overridable per run through the environment.
#[derive(Resource, Debug, Clone, Copy)]
pub struct NavConfig {
    /// `VIBER_NAV=0` turns the whole stack off and restores the beeline.
    pub enabled: bool,
    pub agent_radius: f32,
    pub agent_height: f32,
    pub tile_size: f32,
    pub retile_margin: f32,
    pub ground_cell: f32,
    /// Maximum ledge a character walks up (m).
    pub walkable_climb: f32,
    /// Maximum walkable slope (degrees).
    pub max_slope_deg: f32,
    /// Cost multiplier of off-road ground for a [`NavProfile::Civil`] agent.
    pub offroad_cost: f32,
    /// How far ahead (s of travel) the locomotion ask is projected into a
    /// landmass destination.
    pub lookahead_secs: f32,
    /// Floor for that projection (m), so a slow walker still paths somewhere.
    pub min_lookahead: f32,
    /// Headroom landmass gets over the asked speed, for sidestepping.
    pub max_speed_factor: f32,
    /// How close (m) to its STATED destination a character has to be before a
    /// landmass "reached target" is believed.
    pub arrive_distance: f32,
    /// `VIBER_NAV_DEBUG=1`: census of agent states every frame, at `debug!`.
    /// The one question worth asking when characters stop moving is *why*
    /// landmass says no, and this is the cheapest way to ask it.
    pub debug: bool,
}

impl Default for NavConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            agent_radius: DEFAULT_AGENT_RADIUS,
            agent_height: DEFAULT_AGENT_HEIGHT,
            tile_size: DEFAULT_TILE_SIZE,
            retile_margin: DEFAULT_RETILE_MARGIN,
            ground_cell: DEFAULT_GROUND_CELL,
            walkable_climb: 0.5,
            max_slope_deg: 50.0,
            offroad_cost: DEFAULT_OFFROAD_COST,
            lookahead_secs: 2.0,
            min_lookahead: 3.0,
            max_speed_factor: 1.35,
            arrive_distance: 1.5,
            debug: false,
        }
    }
}

impl NavConfig {
    /// Reads the env overrides on top of the defaults. `VIBER_NAV=0` is the
    /// documented escape hatch.
    pub fn from_env() -> Self {
        let mut config = Self::default();
        if let Ok(value) = std::env::var("VIBER_NAV") {
            config.enabled = !matches!(value.trim(), "0" | "false" | "off");
        }
        if let Ok(value) = std::env::var("VIBER_NAV_DEBUG") {
            config.debug = !matches!(value.trim(), "0" | "false" | "off");
        }
        config
    }

    /// A janela de amostragem que assenta um ponto de query na navmesh — a
    /// MESMA que [`spawn_archipelago`] usa na criação do arquipélago. O debug
    /// bridge (`viber.nav_path`) reutiliza-a para que uma query responda o
    /// mesmo que um agente real leria naquele ponto.
    pub fn point_sample_distance(&self) -> bevy_landmass::PointSampleDistance3d {
        bevy_landmass::PointSampleDistance3d {
            horizontal_distance: self.agent_radius,
            distance_above: self.agent_height,
            distance_below: self.agent_height * 2.0,
            vertical_preference_ratio: 2.0,
            animation_link_max_vertical_distance: self.agent_height,
        }
    }

    /// Folds the attributes of a `<NavMesh …>` element into this config.
    ///
    /// The tag has been parsed since the XML recipes were written and kept as
    /// data with nobody to read it (`AGENTS.md`: "data-only, nenhum consumidor
    /// runtime ainda"). This is that consumer. Unknown attributes are ignored
    /// rather than fatal, and a value that does not parse leaves the default
    /// in place — a typo in a world file must not cost the world its
    /// navigation.
    pub fn apply_attrs(&mut self, attrs: &[(String, String)]) {
        for (key, value) in attrs {
            let Ok(number) = value.trim().parse::<f32>() else {
                if key == "enabled" {
                    self.enabled = !matches!(value.trim(), "0" | "false" | "off");
                }
                continue;
            };
            match key.as_str() {
                "agent-radius" => self.agent_radius = number.max(0.05),
                "agent-height" => self.agent_height = number.max(0.1),
                "tile-size" => self.tile_size = number.max(16.0),
                "retile-margin" => self.retile_margin = number.max(1.0),
                "cell-size" => self.ground_cell = number.max(0.1),
                "walkable-climb" => self.walkable_climb = number.max(0.0),
                "max-slope" => self.max_slope_deg = number.clamp(1.0, 89.0),
                "offroad-cost" => self.offroad_cost = number.max(1.0),
                _ => {}
            }
        }
        // A margin wider than the tile would never trigger a retile.
        self.retile_margin = self.retile_margin.min(self.tile_size * 0.5);
    }
}

/// System set of the navigation bridge, so ordering against landmass is stated
/// once instead of at every call site.
#[derive(SystemSet, Debug, Clone, PartialEq, Eq, Hash)]
pub enum NavSystems {
    /// Locomotion ask → landmass destination. Before landmass runs.
    Push,
    /// Landmass answer → locomotion ask. After landmass runs.
    Pull,
}

/// Registers the navmesh generator, the archipelago and the bridge.
#[derive(Default)]
pub struct NavPlugin;

impl Plugin for NavPlugin {
    fn build(&self, app: &mut App) {
        let config = NavConfig::from_env();
        if !config.enabled {
            info!("nav: VIBER_NAV=0 — navegação desligada, IA em linha recta");
            app.insert_resource(config);
            return;
        }
        app.insert_resource(config)
            .init_resource::<NavTile>()
            .add_systems(PreStartup, apply_navmesh_tag)
            .add_plugins((
                NavmeshPlugins::default(),
                // Landmass defaults to `FixedPreUpdate`, which in Bevy's main
                // schedule runs BEFORE `Update` — before any producer has
                // written this frame's locomotion ask. The bridge would then
                // read the ask that `apply_ai_locomotion` cleared at the end of
                // the previous frame, every agent would look motionless, and
                // landmass would (correctly) report every one of them Idle.
                // The whole pipeline therefore lives in `PostUpdate`, between
                // the producers and the single consumer of the ask.
                Landmass3dPlugin::default().in_schedule(bevy::app::PostUpdate),
                landmass_rerecast::LandmassRerecastPlugin::default()
                    .in_schedule(bevy::app::PostUpdate),
            ))
            .set_navmesh_backend(backend::nav_backend)
            .add_systems(Startup, spawn_archipelago)
            .add_systems(Update, tile::retile_navmesh)
            .add_systems(Update, mark_tile_ready)
            .add_systems(Update, agent::attach_nav_agents);

        // One frame, one pipeline: the producers state an ask in `Update`, the
        // bridge turns it into a destination, landmass answers, the bridge
        // writes the answer back, and `ai::apply_ai_locomotion` moves the
        // character — all before transforms propagate.
        app.configure_sets(
            bevy::app::PostUpdate,
            (
                NavSystems::Push.before(LandmassSystems::SyncExistence),
                NavSystems::Pull
                    .after(LandmassSystems::Output)
                    .before(crate::ai::AiLocomotionSystems),
            ),
        )
        .add_systems(
            bevy::app::PostUpdate,
            (
                agent::push_nav_targets.in_set(NavSystems::Push),
                agent::pull_nav_velocities.in_set(NavSystems::Pull),
            ),
        );
    }
}

/// Creates the single archipelago and its single island, and prices the two
/// node types.
fn spawn_archipelago(mut commands: Commands, config: Res<NavConfig>) {
    let mut options = ArchipelagoOptions::from_agent_radius(config.agent_radius);
    // The default sampling window is sized for a navmesh authored around the
    // agent (10 cm horizontally, 25 cm above). A character on carved voxel
    // terrain is nowhere near that precise: its Y comes from the rendered
    // surface sampler, the navmesh's from Recast's voxel spans, and the two
    // agree to roughly a cell. Too tight a window and every agent reads as
    // "not on the navmesh" — which is indistinguishable from having no
    // navigation at all. (Aqui e no debug bridge: `point_sample_distance`.)
    options.point_sample_distance = config.point_sample_distance();
    let mut archipelago = Archipelago3d::new(options);
    // Base prices are neutral: a road costs what ground costs. The preference
    // is per-agent (`AgentTypeIndexCostOverrides`), so a wolf and a merchant
    // can disagree about the same polygon.
    // `set_type_index_cost` refuses a non-positive cost; 1.0 never is, so the
    // Result carries no information worth branching on.
    let _ = archipelago.set_type_index_cost(agent::ROAD_TYPE_INDEX, 1.0);
    let _ = archipelago.set_type_index_cost(agent::GROUND_TYPE_INDEX, 1.0);
    let archipelago = commands.spawn(archipelago).id();
    // The island is born without a navmesh: the handle only exists once the
    // first tile is requested, and `landmass_rerecast::NavMeshHandle3d` is an
    // immutable component whose insert hook does the conversion wiring for us.
    let island = commands
        .spawn((
            Island,
            ArchipelagoRef3d::new(archipelago),
            Transform::default(),
            GlobalTransform::default(),
        ))
        .id();
    commands.insert_resource(NavArchipelago {
        archipelago,
        island,
    });
}

/// Clears the in-flight flag once the baked tile lands in the asset store.
///
/// The island itself needs no update: the rerecast handle is inserted once (by
/// [`tile::retile_navmesh`], on the first tile) and `regenerate` reuses it, so
/// `landmass_rerecast` re-converts in place and the agents never see a frame
/// without a navmesh.
fn mark_tile_ready(
    mut events: MessageReader<AssetEvent<bevy_rerecast::Navmesh>>,
    mut tile: ResMut<NavTile>,
) {
    for event in events.read() {
        let (AssetEvent::Added { id } | AssetEvent::Modified { id }) = event else {
            continue;
        };
        if tile.handle.as_ref().is_some_and(|handle| handle.id() == *id) {
            tile.generating = false;
            info!("nav: tile #{} pronto", tile.generations);
        }
    }
}

/// Lets `<NavMesh …>` in the world XML tune the stack.
///
/// The recipes keep the tag as a raw [`crate::worldsys::EngineConfigData`]
/// resource; this reads it once, before the archipelago is built, so the agent
/// radius the world asked for is the one the archipelago is priced with.
fn apply_navmesh_tag(
    mut config: ResMut<NavConfig>,
    declared: Option<Res<crate::worldsys::EngineConfigData>>,
) {
    let Some(declared) = declared else {
        return;
    };
    if declared.tag != "navmesh" {
        return;
    }
    config.apply_attrs(&declared.attrs);
    info!(
        "nav: <NavMesh> aplicada — raio {:.2} m, tile {:.0} m, custo fora-de-estrada {:.2}×",
        config.agent_radius, config.tile_size, config.offroad_cost
    );
}
