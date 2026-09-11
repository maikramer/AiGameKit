//! Ground cover — the instanced grass field that turns the terrain shader
//! from "a green surface" into "a meadow you are standing in".
//!
//! `<Vegetation>` spawns one glTF scene per tuft; at ~800 scenes per tag over
//! a 4 km world that is one plant every 90 m — invisible. This module takes
//! the opposite approach, the one every open-world engine takes for grass:
//!
//! * geometry is **baked**, not instanced per entity — one merged mesh per
//!   16 m tile, so a whole square of meadow is a single draw call;
//! * tiles **stream** around the camera in three density tiers and are
//!   dropped (mesh asset included) the moment they leave the ring;
//! * the wind lives in the **vertex shader** ([`GRASS_WGSL`]): one travelling
//!   gust wave shared by the entire field, so the meadow breathes as one
//!   surface instead of N props twitching out of phase.
//!
//! Placement is a pure function of world position (hash noise + the terrain
//! sampler), so the field is stable frame to frame and across runs: walking
//! away and back rebuilds exactly the same blades.
//!
//! Knobs: `VIBER_GRASS=0` disables the field, `VIBER_GRASS_DENSITY=<f32>`
//! scales every tier (0.5 = half the blades, for a slow GPU).

use std::collections::HashMap;

use bevy::asset::RenderAssetUsages;
use bevy::camera::primitives::Aabb;
use bevy::light::NotShadowCaster;
use bevy::math::{Vec2, Vec3};
use bevy::mesh::PrimitiveTopology;
use bevy::pbr::{ExtendedMaterial, MaterialExtension, StandardMaterial};
use bevy::prelude::*;
use bevy::reflect::TypePath;
use bevy::render::render_resource::AsBindGroup;
use bevy::shader::ShaderRef;

use crate::profiler::{Group, timed};
use crate::terrain::runtime::TerrainRuntime;
use crate::worldsys::{BiomeRegions, WeatherState};

/// Vertex stage of the grass material (wind). Config block rewritten from
/// `<Weather>` before the asset is inserted — see [`configure_shader`].
pub const GRASS_WGSL: &str = include_str!("grass.wgsl");

const CONFIG_BEGIN: &str = "// === WORLD CONFIG";
const CONFIG_END: &str = "// === END WORLD CONFIG ===";

/// Fixed id for the embedded grass vertex shader — the field has no world
/// asset folder to write into (unlike sky/water/layers, which are authored
/// per world), so the shader travels with the binary.
pub const GRASS_SHADER: Handle<Shader> =
    bevy::asset::uuid_handle!("6b1f5c30-9a4e-4d7f-8c21-9d4f7a2e5b10");

/// Grass = `StandardMaterial` (lighting/shadows/fog untouched) + a custom
/// vertex stage that bends every blade with the shared wind.
pub type GrassMaterial = ExtendedMaterial<StandardMaterial, GrassExtension>;

/// The extension carries no bindings of its own: everything per-blade rides
/// in the mesh (vertex colours + the UV pair), which is what lets one tile be
/// one draw call.
#[derive(Debug, Clone, Default, Asset, TypePath, AsBindGroup)]
pub struct GrassExtension {
    /// Reserved: `x` = extra gust scale, `yzw` free. Kept as a real binding
    /// so the layout is never an empty bind group.
    #[uniform(100)]
    pub params: Vec4,
}

impl MaterialExtension for GrassExtension {
    fn vertex_shader() -> ShaderRef {
        ShaderRef::Handle(GRASS_SHADER)
    }

    /// The prepass runs the *base* vertex shader, which knows nothing about
    /// the wind — grass in the depth/normal prepass would sit where the blade
    /// is not. Grass contributes nothing to SSAO anyway; keep it out.
    fn enable_prepass() -> bool {
        false
    }

    fn enable_shadows() -> bool {
        false
    }
}

// ───────────────────────────────────────────────────────────── settings

/// Tile side (meters). Small enough that frustum culling actually throws work
/// away, big enough that the ring stays under ~100 draw calls.
const TILE: f32 = 16.0;

/// One density ring: everything with a tile centre inside `radius`.
#[derive(Debug, Clone, Copy)]
struct Tier {
    /// Outer radius (meters) of the ring.
    radius: f32,
    /// Tufts per m² inside it.
    density: f32,
    /// Blades per tuft.
    blades: u32,
    /// Segments per blade (vertices = 2·segments + 1).
    segments: u32,
    /// Blade size multiplier — coarse tiers grow fatter blades so the
    /// silhouette survives the thinner count.
    scale: f32,
}

/// r2 budget: the field was costing frames out at 88 m where a blade is two
/// pixels tall, and starving the foreground where it is the whole picture.
/// The ring pulled in (88 → 66 m, ~90 → ~58 tiles) and the near tier got the
/// savings back as density — "corta a distância, não a densidade perto".
const TIERS: [Tier; 3] = [
    Tier {
        radius: 30.0,
        density: 4.6,
        blades: 3,
        segments: 3,
        scale: 1.0,
    },
    Tier {
        radius: 46.0,
        density: 1.5,
        blades: 2,
        segments: 2,
        scale: 1.4,
    },
    // One triangle per blade: at 50+ m a blade is a smear of colour on the
    // hillside, and the only thing that matters is that the hillside is not
    // bald where the near ring ends.
    Tier {
        radius: 66.0,
        density: 0.45,
        blades: 2,
        segments: 1,
        scale: 2.1,
    },
];

/// Distance past the last tier at which a live tile is dropped (hysteresis,
/// so a player pacing a boundary does not rebuild the same tile forever).
const DROP_MARGIN: f32 = 12.0;
/// Tiles built per frame in steady state, and while the ring is still empty
/// (fast travel / boot — the field has to fill in under a second). The cold
/// budget bounds the boot spike: one tile bakes a 17×17 terrain lattice plus
/// ~900 candidate placements, which is milliseconds — not tens of them.
const BUILD_BUDGET: usize = 2;
const BUILD_BUDGET_COLD: usize = 6;
/// Terrain lattice resolution inside one tile (samples per side). 1 m spacing
/// at `TILE = 16`; blades bilinear-interpolate it instead of paying for a
/// smoothed heightfield sample each.
const LATTICE: usize = 17;

/// Runtime settings, so a slow GPU can be dialed back without a rebuild.
#[derive(Resource, Debug, Clone)]
pub struct GrassSettings {
    pub enabled: bool,
    /// Multiplies every tier's density.
    pub density_scale: f32,
}

impl Default for GrassSettings {
    fn default() -> Self {
        let enabled = std::env::var("VIBER_GRASS").as_deref() != Ok("0");
        let density_scale = std::env::var("VIBER_GRASS_DENSITY")
            .ok()
            .and_then(|v| v.parse::<f32>().ok())
            .unwrap_or(1.0)
            .clamp(0.0, 4.0);
        Self {
            enabled,
            density_scale,
        }
    }
}

/// Live tiles, keyed by tile coordinate.
#[derive(Resource, Default)]
pub struct GrassField {
    tiles: HashMap<(i32, i32), LiveTile>,
    material: Option<Handle<GrassMaterial>>,
    /// Blades emitted by the last full rebuild — profiler / QA readout.
    pub blade_count: usize,
    /// Last day-tint applied to the shared material, so the update system
    /// only touches `Assets<GrassMaterial>` when the factor actually moved
    /// (a `get_mut` per frame would re-extract the material needlessly).
    last_tint: [f32; 3],
    /// Tiles where nothing grows (plaza, lake, road spaghetti) — the built
    /// mesh would be empty. WITHOUT this set the streaming loop retries the
    /// same nearest `BUILD_BUDGET` blocked tiles EVERY frame, starving the
    /// rest of the ring and burning the frame budget forever. Terrain
    /// blocking state (pads/roads/water) is static after bootstrap, so the
    /// tombstone is permanent for the session.
    failed: std::collections::HashSet<(i32, i32)>,
}

struct LiveTile {
    entity: Entity,
    mesh: Handle<Mesh>,
    tier: u8,
    blades: usize,
}

/// Marker on a grass tile entity.
#[derive(Component)]
pub struct GrassTile;

// ───────────────────────────────────────────────────────────── biomes

/// Vegetation identity per biome — the palette and the silhouette are what
/// tell a player which quarter of the map they are standing in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GrassBiome {
    /// The neutral valley ring around the village.
    Vale,
    Forest,
    Desert,
    Swamp,
    Peaks,
}

/// Per-biome blade profile.
///
/// All colours are **linear** RGB (the vertex-colour attribute is linear, and
/// it multiplies the material's base colour before lighting). The r1 palette
/// was authored by eye in the sRGB range and came out as lime plastic: the
/// tips were brighter than the ground they grew from, the hue jitter reached
/// into cyan, and every biome shared one green. r2 darkens and desaturates —
/// a blade averages ~0.8× the terrain albedo, so grass reads as *depth* over
/// the ground instead of a highlight on top of it.
struct Profile {
    /// Density multiplier applied on top of the tier.
    density: f32,
    /// Blade height (meters) before the per-blade jitter.
    height: f32,
    /// Blade half-width at the root (meters).
    width: f32,
    /// Colour at the root and at the tip of a normal blade.
    root: [f32; 3],
    tip: [f32; 3],
    /// Same pair for the "dry" end of the species jitter. Blades interpolate
    /// between the two, so a meadow has straw in it without any blade ever
    /// travelling through the blue-green that made r1 look like AstroTurf.
    dry_root: [f32; 3],
    dry_tip: [f32; 3],
    /// How far a blade arcs over, as a fraction of its height.
    curve: f32,
    /// Flower heads per tuft (probability). Zero = the biome has none.
    flower_rate: f32,
    /// Flower head palette — three species per biome, picked per plant.
    flowers: [[f32; 3]; 3],
    /// Loose pebbles per tuft (probability), and their colour.
    pebble_rate: f32,
    pebble: [f32; 3],
    /// Low bushes per tuft (probability): a squat fan of wide blades. The
    /// middle storey between the carpet and the canopy.
    bush_rate: f32,
}

impl GrassBiome {
    fn profile(self) -> Profile {
        match self {
            // Vale: the meadow green of the village ring — mown, warm,
            // freckled with the small white/yellow field flowers that make a
            // pasture read as pasture.
            GrassBiome::Vale => Profile {
                density: 1.0,
                height: 0.54,
                width: 0.028,
                root: [0.045, 0.082, 0.032],
                tip: [0.235, 0.310, 0.118],
                dry_root: [0.086, 0.076, 0.032],
                dry_tip: [0.370, 0.320, 0.140],
                curve: 0.46,
                flower_rate: 0.055,
                flowers: [
                    [0.92, 0.88, 0.72], // field white
                    [0.90, 0.70, 0.14], // buttercup
                    [0.62, 0.30, 0.52], // clover
                ],
                pebble_rate: 0.012,
                pebble: [0.20, 0.19, 0.17],
                bush_rate: 0.012,
            },
            // Forest: taller, colder, much darker — undergrowth in shade,
            // with the pale mushroom-and-fern floor of a conifer stand.
            GrassBiome::Forest => Profile {
                density: 1.2,
                height: 0.74,
                width: 0.033,
                root: [0.024, 0.052, 0.030],
                tip: [0.140, 0.230, 0.100],
                dry_root: [0.052, 0.050, 0.024],
                dry_tip: [0.235, 0.215, 0.090],
                curve: 0.52,
                flower_rate: 0.030,
                flowers: [
                    [0.86, 0.84, 0.78], // wood anemone
                    [0.36, 0.24, 0.58], // bluebell
                    [0.74, 0.24, 0.20], // fly agaric cap
                ],
                pebble_rate: 0.020,
                pebble: [0.14, 0.15, 0.13],
                bush_rate: 0.032,
            },
            // Desert: sparse straw tufts, bleached, stiff (barely curves).
            GrassBiome::Desert => Profile {
                density: 0.30,
                height: 0.42,
                width: 0.024,
                root: [0.140, 0.112, 0.052],
                tip: [0.505, 0.418, 0.208],
                dry_root: [0.175, 0.130, 0.055],
                dry_tip: [0.600, 0.470, 0.205],
                curve: 0.26,
                flower_rate: 0.010,
                flowers: [
                    [0.86, 0.42, 0.22], // desert bloom
                    [0.88, 0.76, 0.34], // brittlebush
                    [0.72, 0.66, 0.52], // seed head
                ],
                pebble_rate: 0.060,
                pebble: [0.32, 0.24, 0.16],
                bush_rate: 0.020,
            },
            // Swamp: rank olive reeds, the tallest of the four. Never the
            // chartreuse of r1 — a bog is brown-green, not neon.
            GrassBiome::Swamp => Profile {
                density: 1.05,
                height: 0.95,
                width: 0.034,
                root: [0.038, 0.048, 0.024],
                tip: [0.215, 0.230, 0.090],
                dry_root: [0.072, 0.058, 0.028],
                dry_tip: [0.330, 0.270, 0.110],
                curve: 0.40,
                flower_rate: 0.022,
                flowers: [
                    [0.60, 0.52, 0.26], // cattail
                    [0.30, 0.42, 0.24], // sedge head
                    [0.72, 0.70, 0.58], // cotton grass
                ],
                pebble_rate: 0.018,
                pebble: [0.12, 0.13, 0.11],
                bush_rate: 0.026,
            },
            // Peaks: short alpine tussock, grey-green, wind-flattened.
            GrassBiome::Peaks => Profile {
                density: 0.50,
                height: 0.30,
                width: 0.025,
                root: [0.060, 0.076, 0.060],
                tip: [0.225, 0.255, 0.200],
                dry_root: [0.090, 0.082, 0.058],
                dry_tip: [0.310, 0.285, 0.210],
                curve: 0.34,
                flower_rate: 0.028,
                flowers: [
                    [0.88, 0.88, 0.90], // edelweiss
                    [0.48, 0.52, 0.78], // gentian
                    [0.84, 0.62, 0.66], // moss campion
                ],
                pebble_rate: 0.075,
                pebble: [0.26, 0.27, 0.29],
                bush_rate: 0.008,
            },
        }
    }
}

/// Biome at a world XZ, from the authored `<BiomeRegion>` polygons when the
/// world has them (the four cardinal wedges of `simple-rpg`), else the same
/// wedges derived geometrically so a bare world still gets identity.
pub fn biome_at(regions: Option<&BiomeRegions>, x: f32, z: f32) -> GrassBiome {
    if let Some(regions) = regions {
        for region in &regions.list {
            if crate::ambient::point_in_polygon(x, z, &region.polygon) {
                return match region.id.as_str() {
                    "dark-forest" | "forest" => GrassBiome::Forest,
                    "desert" => GrassBiome::Desert,
                    "swamp" => GrassBiome::Swamp,
                    "frozen-peaks" | "peaks" => GrassBiome::Peaks,
                    _ => GrassBiome::Vale,
                };
            }
        }
        return GrassBiome::Vale;
    }
    GrassBiome::Vale
}

// ───────────────────────────────────────────────────────────── noise

/// Integer hash → `0.0..1.0`. Deterministic across runs and machines.
fn hash01(x: i32, z: i32, salt: u32) -> f32 {
    let mut h = (x as u32)
        .wrapping_mul(0x9E37_79B1)
        .wrapping_add((z as u32).wrapping_mul(0x85EB_CA6B))
        .wrapping_add(salt.wrapping_mul(0xC2B2_AE35));
    h ^= h >> 15;
    h = h.wrapping_mul(0x2545_F491);
    h ^= h >> 13;
    (h & 0x00FF_FFFF) as f32 / 16_777_216.0
}

/// Smooth value noise in `0.0..1.0` at `scale` meters per cell.
fn value_noise(x: f32, z: f32, scale: f32, salt: u32) -> f32 {
    let gx = x / scale;
    let gz = z / scale;
    let x0 = gx.floor();
    let z0 = gz.floor();
    let fx = gx - x0;
    let fz = gz - z0;
    // Smoothstep the cell coordinates: linear interpolation of a hash grid
    // shows its lattice as diamond seams at grazing angles.
    let sx = fx * fx * (3.0 - 2.0 * fx);
    let sz = fz * fz * (3.0 - 2.0 * fz);
    let (ix, iz) = (x0 as i32, z0 as i32);
    let n00 = hash01(ix, iz, salt);
    let n10 = hash01(ix + 1, iz, salt);
    let n01 = hash01(ix, iz + 1, salt);
    let n11 = hash01(ix + 1, iz + 1, salt);
    let a = n00 + (n10 - n00) * sx;
    let b = n01 + (n11 - n01) * sx;
    a + (b - a) * sz
}

/// Coverage at a point: clearings, thickets and a fine break-up, in `0..1`.
///
/// This is the difference between "grass everywhere at one density" (reads as
/// a texture) and a meadow with bald patches and lush hollows (reads as a
/// place). Two octaves: a 46 m field that opens clearings, and a 11 m field
/// that frays their edges.
pub fn coverage_at(x: f32, z: f32) -> f32 {
    let broad = value_noise(x, z, 46.0, 11);
    let fine = value_noise(x, z, 11.0, 29);
    // Clearings: below the low edge nothing grows at all; above the high edge
    // the meadow closes. `0.18` keeps a thin scatter inside a clearing so the
    // boundary is a fade, not a cut.
    let clearing = smoothstep(0.24, 0.62, broad);
    let patch = 0.55 + 0.45 * fine;
    (clearing * patch).clamp(0.0, 1.0) * 0.82 + 0.18 * clearing
}

fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let t = ((x - edge0) / (edge1 - edge0).max(1e-6)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

// ───────────────────────────────────────────────────────────── day/night

/// Albedo multiplier for the whole field at daylight factor `day`
/// (`crate::worldsys::daylight_factor`: 0 = night, 1 = full day).
///
/// The blades' colours live in vertex attributes, which a shader const can't
/// reach (`Globals` carries only `time` — no clock), so the factor rides in
/// the shared material's `base_color`: the standard extract path re-uploads
/// it every change, unlike custom extension uniforms. `day = 1.0` returns
/// exact `[1, 1, 1]` — the r1-approved daylight look, bit-identical.
pub(crate) fn day_tint(day: f32) -> [f32; 3] {
    let day = day.clamp(0.0, 1.0);
    // Night: dark, slightly blue-lifted silhouettes — grass must never glow
    // against the night sky (the r2 white-wedge bug).
    const NIGHT: [f32; 3] = [0.10, 0.12, 0.17];
    // Golden hour: peaked mid-ramp (`day*(1-day)*4` is 1 there, 0 at both
    // ends) — a LIGHT warm hand, not a sunset filter.
    const GOLDEN: [f32; 3] = [1.12, 0.97, 0.80];
    let warmth = day * (1.0 - day) * 4.0;
    let mut out = [0.0f32; 3];
    for i in 0..3 {
        let neutral = NIGHT[i] + (1.0 - NIGHT[i]) * day;
        out[i] = neutral * (1.0 - warmth + GOLDEN[i] * warmth);
    }
    out
}

/// Apply [`day_tint`] to the field material, following `<DayCycle>`. Worlds
/// without a clock stay at full day.
fn grass_daynight_tint(
    clock: Option<Res<crate::worldsys::DayCycleState>>,
    mut field: ResMut<GrassField>,
    mut materials: ResMut<Assets<GrassMaterial>>,
) {
    let day = clock
        .as_deref()
        .map(|clock| {
            crate::worldsys::daylight_factor(
                clock.minute_of_day,
                clock.dawn_minute,
                clock.dusk_minute,
            )
        })
        .unwrap_or(1.0);
    let tint = day_tint(day);
    if (tint[0] - field.last_tint[0]).abs() < 1e-3
        && (tint[1] - field.last_tint[1]).abs() < 1e-3
        && (tint[2] - field.last_tint[2]).abs() < 1e-3
    {
        return;
    }
    let Some(handle) = field.material.clone() else {
        return;
    };
    let Some(mut material) = materials.get_mut(&handle) else {
        return;
    };
    material.base.base_color = Color::linear_rgb(tint[0], tint[1], tint[2]);
    field.last_tint = tint;
}

// ───────────────────────────────────────────────────────────── plugin

pub struct GrassPlugin;

impl bevy::app::Plugin for GrassPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<GrassSettings>()
            .init_resource::<GrassField>()
            .add_plugins(bevy::pbr::MaterialPlugin::<GrassMaterial>::default())
            // The wind consts come from `<Weather>`, which `spawn::startup`
            // inserts — without the ordering, the shader could specialize on
            // the fallback wind before the world's weather lands.
            .add_systems(
                bevy::app::Startup,
                configure_shader.after(crate::recipes::spawn::startup),
            )
            .add_systems(
                bevy::app::Update,
                timed(Group::Terrain, grass_daynight_tint).after(stream_grass_tiles),
            )
            .add_systems(bevy::app::Update, timed(Group::Terrain, stream_grass_tiles));
    }
}

/// Insert the vertex shader, with the wind of this world baked in as consts.
///
/// A material *uniform* would be the obvious home for the wind vector, but
/// the Bevy 0.19 slot-1 storage promotion never re-uploads custom material
/// uniforms (the reason `sky.wgsl` and `layers.wgsl` specialize by consts
/// too), so the world's `<Weather wind="…">` is written into the shader
/// source instead — one wind for the sky, the water and the grass.
fn configure_shader(mut shaders: ResMut<Assets<Shader>>, weather: Option<Res<WeatherState>>) {
    let (wind, strength) = weather
        .map(|w| (w.wind, w.wind_strength))
        .unwrap_or(([0.94, 0.34], 1.0));
    let len = (wind[0] * wind[0] + wind[1] * wind[1]).sqrt();
    let dir = if len > 1e-4 {
        [wind[0] / len, wind[1] / len]
    } else {
        [0.94, 0.34]
    };
    let config = format!(
        "{CONFIG_BEGIN} (generated by src/grass.rs — edit the XML, not this block) ===\n\
         const CFG_WIND_X: f32 = {:.4};\n\
         const CFG_WIND_Z: f32 = {:.4};\n\
         const CFG_WIND_STRENGTH: f32 = {:.4};\n\
         {CONFIG_END}",
        dir[0],
        dir[1],
        strength.clamp(0.25, 2.5),
    );
    let source = replace_config_block(GRASS_WGSL, &config);
    if let Err(error) = shaders.insert(
        GRASS_SHADER.id(),
        Shader::from_wgsl(source, "viber/grass.wgsl"),
    ) {
        warn!("grass: shader não inserido ({error}) — o vento fica com as consts default");
    }
}

/// Swap the `WORLD CONFIG` block of `source` for `config` (whole lines).
fn replace_config_block(source: &str, config: &str) -> String {
    let Some(start) = source.find(CONFIG_BEGIN) else {
        return source.to_string();
    };
    let Some(end) = source[start..].find(CONFIG_END).map(|i| {
        // Include the marker line itself.
        start + i + CONFIG_END.len()
    }) else {
        return source.to_string();
    };
    let mut out = String::with_capacity(source.len() + config.len());
    out.push_str(&source[..start]);
    out.push_str(config);
    out.push_str(&source[end..]);
    out
}

/// Build / drop grass tiles so the ring follows the camera.
#[allow(clippy::too_many_arguments)]
fn stream_grass_tiles(
    mut commands: Commands,
    settings: Res<GrassSettings>,
    mut field: ResMut<GrassField>,
    terrain: Option<Res<TerrainRuntime>>,
    regions: Option<Res<BiomeRegions>>,
    cliffs: Option<Res<crate::terrain::cliffs::CliffMask>>,
    cameras: Query<&GlobalTransform, With<Camera3d>>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<GrassMaterial>>,
    mut starve: Local<StarveDiag>,
) {
    if !settings.enabled {
        return;
    }
    let Some(terrain) = terrain else {
        return;
    };
    let Some(camera) = cameras.iter().next() else {
        return;
    };
    let eye = camera.translation();
    // Fora da pegada do heightmap não há relva que fazer: a rede de tiles
    // segue a câmara e o lattice do terreno SATURA na orla, portanto uma cena
    // de interior posta fora do mapa acordava com tufos de erva a flutuar à
    // cota da borda do mundo, dentro da sala.
    if !terrain.in_field(eye.x, eye.z) {
        return;
    }

    let material = match &field.material {
        Some(handle) => handle.clone(),
        None => {
            let handle = materials.add(GrassMaterial {
                base: StandardMaterial {
                    base_color: Color::WHITE,
                    perceptual_roughness: 0.92,
                    reflectance: 0.05,
                    // Blades are single-sided geometry seen from every angle:
                    // draw both faces, but do NOT flip the normal on the back
                    // (the normal is the *ground* normal — flipping it turns
                    // half the meadow black).
                    double_sided: false,
                    cull_mode: None,
                    ..default()
                },
                extension: GrassExtension { params: Vec4::ZERO },
            });
            field.material = Some(handle.clone());
            handle
        }
    };

    // ---- drop tiles that fell out of the ring ---------------------------
    let drop_radius = TIERS[TIERS.len() - 1].radius + DROP_MARGIN;
    let stale: Vec<(i32, i32)> = field
        .tiles
        .iter()
        .filter(|(coord, _)| tile_distance(**coord, eye) > drop_radius)
        .map(|(coord, _)| *coord)
        .collect();
    for coord in stale {
        if let Some(tile) = field.tiles.remove(&coord) {
            commands.entity(tile.entity).despawn();
            meshes.remove(&tile.mesh);
        }
    }

    // ---- collect the tiles the ring wants, nearest first ----------------
    let reach = TIERS[TIERS.len() - 1].radius;
    let span = (reach / TILE).ceil() as i32 + 1;
    let cx = (eye.x / TILE).floor() as i32;
    let cz = (eye.z / TILE).floor() as i32;
    let mut wanted: Vec<((i32, i32), u8, f32)> = Vec::new();
    for tz in (cz - span)..=(cz + span) {
        for tx in (cx - span)..=(cx + span) {
            let coord = (tx, tz);
            if field.failed.contains(&coord) {
                continue;
            }
            let distance = tile_distance(coord, eye);
            let Some(tier) = tier_for(distance) else {
                continue;
            };
            match field.tiles.get(&coord) {
                // Already at the right density — nothing to do.
                Some(live) if live.tier == tier => continue,
                // Wrong tier: only *upgrade* eagerly (walking toward). A
                // downgrade can wait for the drop radius, so pacing a
                // boundary never thrashes the mesh allocator.
                Some(live) if live.tier > tier => {}
                Some(_) => continue,
                None => {}
            }
            wanted.push((coord, tier, distance));
        }
    }
    if wanted.is_empty() {
        starve.frames = 0;
        return;
    }
    wanted.sort_by(|a, b| a.2.total_cmp(&b.2));

    let budget = if field.tiles.is_empty() {
        BUILD_BUDGET_COLD
    } else {
        BUILD_BUDGET
    };
    starve.frames += 1;
    starve.wanted += wanted.len();
    starve.budget = budget;
    for (coord, tier, _) in wanted.into_iter().take(budget) {
        if let Some(old) = field.tiles.remove(&coord) {
            commands.entity(old.entity).despawn();
            meshes.remove(&old.mesh);
        }
        let origin = Vec3::new(coord.0 as f32 * TILE, 0.0, coord.1 as f32 * TILE);
        let outcome = build_tile(
            coord,
            tier,
            &settings,
            terrain.as_ref(),
            regions.as_deref(),
            cliffs.as_deref(),
        );
        let Some(built) = outcome.tile else {
            // Nothing grows on this square (plaza, lake, road spaghetti).
            // Tombstone it — see `GrassField::failed`.
            field.failed.insert(coord);
            starve.blocked += 1;
            continue;
        };
        starve.built += 1;
        starve.blades += built.blades;
        let blades = built.blades;
        let half_y = (built.max_y - built.min_y) * 0.5;
        let center_y = (built.max_y + built.min_y) * 0.5;
        let mesh = meshes.add(built.mesh);
        let entity = commands
            .spawn((
                GrassTile,
                Mesh3d(mesh.clone()),
                MeshMaterial3d(material.clone()),
                Transform::from_translation(origin),
                NotShadowCaster,
                // Wind pushes vertices past the baked bounds; a padded AABB
                // keeps a leaning tile from popping at the screen edge.
                Aabb {
                    center: Vec3::new(TILE * 0.5, center_y, TILE * 0.5).into(),
                    half_extents: Vec3::new(TILE * 0.5 + 1.5, half_y + 1.0, TILE * 0.5 + 1.5)
                        .into(),
                },
            ))
            .id();
        field.tiles.insert(
            coord,
            LiveTile {
                entity,
                mesh,
                tier,
                blades,
            },
        );
    }
    field.blade_count = field.tiles.values().map(|t| t.blades).sum();
    // Tripwire: the ring should drain in a few frames. If it is still
    // starving after ~3 s, say so (with the tombstone count) instead of
    // failing silently.
    if starve.frames == 180 {
        warn!(
            "grass: anel sem escoar por 180 frames — wanted {} (built {} tiles / {} lâminas, {} bloqueados, {} tombstones, budget {})",
            starve.wanted,
            starve.built,
            starve.blades,
            starve.blocked,
            field.failed.len(),
            starve.budget,
        );
    }
}

/// Rolling diagnosis of the streaming loop — logged if the ring fails to
/// drain (the "grass never appears" failure mode).
#[derive(Default)]
struct StarveDiag {
    frames: u32,
    wanted: usize,
    built: usize,
    blades: usize,
    blocked: usize,
    budget: usize,
}

/// Distance from `eye` to the centre of tile `coord`, on XZ.
fn tile_distance(coord: (i32, i32), eye: Vec3) -> f32 {
    let center = Vec2::new((coord.0 as f32 + 0.5) * TILE, (coord.1 as f32 + 0.5) * TILE);
    center.distance(Vec2::new(eye.x, eye.z))
}

/// Tier index for a tile centre distance (`None` = outside the field).
fn tier_for(distance: f32) -> Option<u8> {
    TIERS
        .iter()
        .position(|tier| distance <= tier.radius)
        .map(|i| i as u8)
}

// ───────────────────────────────────────────────────────────── baking

struct BuiltTile {
    mesh: Mesh,
    blades: usize,
    min_y: f32,
    max_y: f32,
}

/// Highest slope (degrees) that still holds grass; past it the blades would
/// stick out of a cliff face sideways.
const MAX_SLOPE_DEG: f32 = 38.0;
/// Altitude band (meters) over which grass thins out to nothing near the
/// snow line — `<Terrain max-height="200" snow-height="0.92">`.
const ALTITUDE_FADE_START: f32 = 118.0;
const ALTITUDE_FADE_END: f32 = 168.0;
/// Clearance (meters) kept around road ribbons and water edges.
const ROAD_CLEARANCE: f32 = 1.1;

/// Bake one tile's merged blade mesh. `tile: None` when nothing grows there
/// (a plaza, a lake, a cliff) — the caller tombstones it. The counts ride
/// along for the streaming loop's diagnosis.
#[allow(clippy::too_many_arguments)]
fn build_tile(
    coord: (i32, i32),
    tier: u8,
    settings: &GrassSettings,
    terrain: &TerrainRuntime,
    regions: Option<&BiomeRegions>,
    cliffs: Option<&crate::terrain::cliffs::CliffMask>,
) -> BuildOutcome {
    let mut outcome = BuildOutcome::default();
    let tier_cfg = TIERS[tier as usize];
    let origin = Vec2::new(coord.0 as f32 * TILE, coord.1 as f32 * TILE);

    // ---- terrain lattice: sample once, interpolate per blade ------------
    // `sample_mesh_surface` reproduces the drawn chunk triangulation, so the
    // blades sit ON the visible ground instead of hovering over ridges.
    let step = TILE / (LATTICE - 1) as f32;
    let mut height = [0.0f32; LATTICE * LATTICE];
    let mut blocked = [false; LATTICE * LATTICE];
    let mut any_open = false;
    for iz in 0..LATTICE {
        for ix in 0..LATTICE {
            let x = origin.x + ix as f32 * step;
            let z = origin.y + iz as f32 * step;
            let y = terrain.sample_mesh_surface(x, z);
            height[iz * LATTICE + ix] = y;
            let wet = terrain
                .water_surface_at(x, z)
                .is_some_and(|surface| y <= surface + 0.25)
                || terrain.in_water(x, z);
            let paved = terrain
                .roads
                .iter()
                .any(|road| road.distance_to_road(Vec2::new(x, z)) <= ROAD_CLEARANCE);
            let paved = paved || in_pad_core(terrain, x, z);
            // Slab standing surface (arch band, tight overhang brow): blades
            // would root on floating rock or interpolate across its vertical
            // edge. Flat world never sets this (the check is free there).
            let roofed = terrain.has_thin_roof(x, z);
            blocked[iz * LATTICE + ix] = wet || paved || roofed;
            any_open |= !(wet || paved || roofed);
        }
    }
    if !any_open {
        return outcome;
    }

    let area = TILE * TILE;
    let target = (area * tier_cfg.density * settings.density_scale.max(0.0)).round() as usize;
    if target == 0 {
        return outcome;
    }
    outcome.target = target;

    let mut positions: Vec<[f32; 3]> = Vec::with_capacity(target * 24);
    let mut normals: Vec<[f32; 3]> = Vec::with_capacity(target * 24);
    let mut uvs: Vec<[f32; 2]> = Vec::with_capacity(target * 24);
    let mut colors: Vec<[f32; 4]> = Vec::with_capacity(target * 24);
    let mut indices: Vec<u32> = Vec::with_capacity(target * 36);
    let mut blades = 0usize;
    let mut min_y = f32::INFINITY;
    let mut max_y = f32::NEG_INFINITY;

    // Deterministic stream per (tile, tier): the same square of world always
    // grows the same meadow, whichever direction the player walked in from.
    let seed = ((coord.0 as i64) << 32 ^ (coord.1 as i64 & 0xFFFF_FFFF)) as u64;
    let mut rng = crate::spawner::Rng::new(
        seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ ((tier as u64 + 1) * 0x0123_4567_89AB_CDEF),
    );

    for _ in 0..target {
        let fx = rng.next_f32();
        let fz = rng.next_f32();
        let x = origin.x + fx * TILE;
        let z = origin.y + fz * TILE;

        // Coverage first: it rejects most candidates and costs one hash.
        let coverage = coverage_at(x, z);
        if rng.next_f32() > coverage {
            continue;
        }
        if lattice_blocked(&blocked, fx, fz) {
            continue;
        }
        // Cliff regions (region-filtered mask, margin baked in): blades
        // inside the wall band or its verge pierce the rock face up close.
        if cliffs.is_some_and(|m| m.is_cliff_at(Vec2::new(x, z))) {
            continue;
        }
        // Thin slab under this exact blade (the lattice blocked-array can
        // miss up to a step away from the band edge).
        if terrain.has_thin_roof(x, z) {
            continue;
        }
        let y = lattice_height(&height, fx, fz);
        // Altitude thinning toward the snow line.
        let altitude = 1.0 - smoothstep(ALTITUDE_FADE_START, ALTITUDE_FADE_END, y);
        if altitude <= 0.0 || rng.next_f32() > altitude {
            continue;
        }
        let normal = lattice_normal(&height, fx, fz, step);
        let slope_deg = normal.y.clamp(-1.0, 1.0).acos().to_degrees();
        if slope_deg > MAX_SLOPE_DEG {
            continue;
        }
        // Slopes thin out before the hard cut, so the boundary is a fade.
        let slope_keep = 1.0 - smoothstep(MAX_SLOPE_DEG - 14.0, MAX_SLOPE_DEG, slope_deg);
        if rng.next_f32() > slope_keep {
            continue;
        }

        let biome = biome_at(regions, x, z);
        let profile = biome.profile();
        if rng.next_f32() > profile.density.min(1.0) {
            continue;
        }
        // Biomes denser than 1.0 get their surplus as extra blades per tuft.
        let extra = if profile.density > 1.0 && rng.next_f32() < profile.density - 1.0 {
            1
        } else {
            0
        };

        // Wetland bonus: reeds crowd the waterline, the way a real bank does.
        let near_water = terrain.water_surface_at(x, z).is_some() || nearby_water(&blocked, fx, fz);
        let height_boost = if near_water && biome == GrassBiome::Swamp {
            1.35
        } else {
            1.0
        };

        // Zone tint: a slow field that makes one hollow drier and the next
        // one lusher. Without it every tuft samples the same jitter and the
        // meadow averages out to a single flat colour at any distance.
        let patch = value_noise(x, z, 62.0, 47);
        let zone_dry = smoothstep(0.42, 0.86, patch);
        let zone_shade = 0.88 + 0.24 * value_noise(x, z, 19.0, 53);

        // Tuft: N blades fanned around one root, each with its own heading,
        // height and lean — the reason a tuft reads as a plant and a single
        // quad reads as a decal.
        let tuft_yaw = rng.range(0.0, std::f32::consts::TAU);
        // Wide height spread inside one tuft: r1's blades were all the same
        // length, which is what made the field read as a brush, not a plant.
        let tuft_scale = rng.range(0.55, 1.55);
        let local = Vec3::new(x - origin.x, y, z - origin.y);
        let fan = (tier_cfg.blades + extra).max(1);
        for blade in 0..fan {
            let spread = 0.10 * tier_cfg.scale;
            let offset = rng.unit_disc() * spread;
            let yaw = tuft_yaw
                + blade as f32 * (std::f32::consts::TAU / fan as f32)
                + rng.range(-0.5, 0.5);
            // One blade in five is a broad leaf: shorter, twice as wide. A
            // tuft of identical needles is a hairbrush; a tuft with one leaf
            // in it is a plant.
            let leaf = rng.next_f32() < 0.22;
            let (width_mul, height_mul) = if leaf { (2.3, 0.72) } else { (1.0, 1.0) };
            let h = profile.height * tuft_scale * height_boost * height_mul * rng.range(0.74, 1.26);
            let w = profile.width * tier_cfg.scale * width_mul * rng.range(0.8, 1.3);
            // Leaves flop harder than blades do.
            let curve = profile.curve * rng.range(0.55, 1.7) * if leaf { 1.35 } else { 1.0 };
            let shade = rng.range(0.80, 1.14) * zone_shade;
            let hue = (rng.next_f32() * 0.6 + zone_dry * 0.7).clamp(0.0, 1.0);
            emit_blade(
                &mut positions,
                &mut normals,
                &mut uvs,
                &mut colors,
                &mut indices,
                local + Vec3::new(offset.x, 0.0, offset.y),
                normal,
                yaw,
                h,
                w,
                curve,
                shade,
                hue,
                &profile,
                tier_cfg.segments,
            );
            blades += 1;
            min_y = min_y.min(y);
            max_y = max_y.max(y + h * 1.1);
        }

        // ---- middle storey ---------------------------------------------
        // Flowers, bushes and pebbles ride in the SAME merged mesh as the
        // grass: no entity, no draw call, no LOD ladder, and they inherit the
        // tile's terrain seating and wind for free. This is the layer between
        // the carpet and the canopy that the r1 captures had nothing in.
        if tier_cfg.segments >= 2 {
            if rng.next_f32() < profile.flower_rate {
                let species = (rng.next_f32() * 3.0) as usize % 3;
                let head = profile.flowers[species];
                let stalk = [
                    profile.tip[0] * 0.7,
                    profile.tip[1] * 0.85,
                    profile.tip[2] * 0.7,
                ];
                emit_flower(
                    &mut positions,
                    &mut normals,
                    &mut uvs,
                    &mut colors,
                    &mut indices,
                    local,
                    normal,
                    rng.range(0.0, std::f32::consts::TAU),
                    profile.height * rng.range(1.05, 1.55),
                    [
                        (head[0] * zone_shade).min(1.0),
                        (head[1] * zone_shade).min(1.0),
                        (head[2] * zone_shade).min(1.0),
                    ],
                    stalk,
                );
                max_y = max_y.max(y + profile.height * 1.8);
            }
            if rng.next_f32() < profile.pebble_rate {
                emit_pebble(
                    &mut positions,
                    &mut normals,
                    &mut uvs,
                    &mut colors,
                    &mut indices,
                    local,
                    rng.range(0.0, std::f32::consts::TAU),
                    rng.range(0.05, 0.22) * tier_cfg.scale,
                    profile.pebble,
                    rng.range(0.75, 1.25),
                );
            }
            if rng.next_f32() < profile.bush_rate {
                // A bush is a squat, dense fan of very wide blades — the
                // same primitive, used at a different aspect ratio.
                let bush_yaw = rng.range(0.0, std::f32::consts::TAU);
                let bush_h = profile.height * rng.range(1.4, 2.4);
                let bush_shade = rng.range(0.62, 0.88) * zone_shade;
                for leaf in 0..7u32 {
                    let angle = bush_yaw + leaf as f32 * (std::f32::consts::TAU / 7.0);
                    let (s, c) = angle.sin_cos();
                    let reach = bush_h * 0.22 * rng.range(0.5, 1.0);
                    emit_blade(
                        &mut positions,
                        &mut normals,
                        &mut uvs,
                        &mut colors,
                        &mut indices,
                        local + Vec3::new(c * reach, 0.0, s * reach),
                        normal,
                        angle,
                        bush_h * rng.range(0.6, 1.0),
                        profile.width * 3.4 * rng.range(0.8, 1.2),
                        profile.curve * rng.range(1.4, 2.2),
                        bush_shade,
                        rng.next_f32() * 0.4,
                        &profile,
                        tier_cfg.segments,
                    );
                    blades += 1;
                }
                max_y = max_y.max(y + bush_h * 1.2);
            }
        }
    }

    if indices.is_empty() {
        return outcome;
    }

    let mut mesh = Mesh::new(
        PrimitiveTopology::TriangleList,
        // MAIN_WORLD kept: the debug bridge (`viber.debug.entities/info/stats`)
        // reads vertex attributes of any Mesh3d it snapshots; RENDER_WORLD-only
        // meshes panic there ("ExtractedToRenderWorld") and take the engine
        // down with them. A few MB of CPU-side blades is the insurance.
        RenderAssetUsages::MAIN_WORLD.union(RenderAssetUsages::RENDER_WORLD),
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
    mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, normals);
    mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, uvs);
    mesh.insert_attribute(Mesh::ATTRIBUTE_COLOR, colors);
    mesh.insert_indices(bevy::mesh::Indices::U32(indices));
    outcome.blades = blades;
    outcome.tile = Some(BuiltTile {
        mesh,
        blades,
        min_y,
        max_y,
    });
    outcome
}

/// What one `build_tile` attempt produced — the diagnosis half is what turns
/// "grass never appears" from a mystery into a number.
#[derive(Default)]
struct BuildOutcome {
    tile: Option<BuiltTile>,
    /// Blades emitted (0 when the tile came back empty).
    blades: usize,
    /// Candidate placements the tile aimed for.
    target: usize,
}

/// True when the point sits on the flat core of an authored `<TerrainPad>` —
/// the village plaza, a building floor. Those are paved; grass there reads as
/// weeds growing through a cathedral.
fn in_pad_core(terrain: &TerrainRuntime, x: f32, z: f32) -> bool {
    terrain.pads.iter().any(|pad| {
        (x - pad.at.x).abs() <= pad.size.x * 0.5 && (z - pad.at.y).abs() <= pad.size.y * 0.5
    })
}

/// Bilinear height from the tile lattice (`fx`/`fz` are 0..1 within a tile).
fn lattice_height(height: &[f32], fx: f32, fz: f32) -> f32 {
    let gx = (fx * (LATTICE - 1) as f32).clamp(0.0, (LATTICE - 1) as f32);
    let gz = (fz * (LATTICE - 1) as f32).clamp(0.0, (LATTICE - 1) as f32);
    let x0 = gx.floor() as usize;
    let z0 = gz.floor() as usize;
    let x1 = (x0 + 1).min(LATTICE - 1);
    let z1 = (z0 + 1).min(LATTICE - 1);
    let tx = gx - x0 as f32;
    let tz = gz - z0 as f32;
    let h00 = height[z0 * LATTICE + x0];
    let h10 = height[z0 * LATTICE + x1];
    let h01 = height[z1 * LATTICE + x0];
    let h11 = height[z1 * LATTICE + x1];
    let a = h00 + (h10 - h00) * tx;
    let b = h01 + (h11 - h01) * tx;
    a + (b - a) * tz
}

/// Surface normal from the lattice by central differences.
fn lattice_normal(height: &[f32], fx: f32, fz: f32, step: f32) -> Vec3 {
    let d = 1.0 / (LATTICE - 1) as f32;
    let hx0 = lattice_height(height, (fx - d).max(0.0), fz);
    let hx1 = lattice_height(height, (fx + d).min(1.0), fz);
    let hz0 = lattice_height(height, fx, (fz - d).max(0.0));
    let hz1 = lattice_height(height, fx, (fz + d).min(1.0));
    Vec3::new(hx0 - hx1, 2.0 * step, hz0 - hz1).normalize_or_zero()
}

/// Nearest-lattice occupancy test, with the four neighbours included so the
/// grass line stops a lattice cell short of a road or a waterline instead of
/// growing right up to (and visibly through) the edge.
fn lattice_blocked(blocked: &[bool], fx: f32, fz: f32) -> bool {
    let gx = (fx * (LATTICE - 1) as f32).round() as usize;
    let gz = (fz * (LATTICE - 1) as f32).round() as usize;
    let gx = gx.min(LATTICE - 1);
    let gz = gz.min(LATTICE - 1);
    blocked[gz * LATTICE + gx]
}

/// A blocked lattice cell within one step — used to crowd reeds at the bank.
fn nearby_water(blocked: &[bool], fx: f32, fz: f32) -> bool {
    let gx = (fx * (LATTICE - 1) as f32).round() as i32;
    let gz = (fz * (LATTICE - 1) as f32).round() as i32;
    for dz in -1i32..=1 {
        for dx in -1i32..=1 {
            let x = gx + dx;
            let z = gz + dz;
            if x < 0 || z < 0 || x >= LATTICE as i32 || z >= LATTICE as i32 {
                continue;
            }
            if blocked[z as usize * LATTICE + x as usize] {
                return true;
            }
        }
    }
    false
}

/// Append one blade: a tapered strip that arcs over and ends in a point.
///
/// `uv.x` carries the height fraction and `uv.y` the blade height in meters —
/// that pair is the entire contract the wind shader needs.
#[allow(clippy::too_many_arguments)]
fn emit_blade(
    positions: &mut Vec<[f32; 3]>,
    normals: &mut Vec<[f32; 3]>,
    uvs: &mut Vec<[f32; 2]>,
    colors: &mut Vec<[f32; 4]>,
    indices: &mut Vec<u32>,
    root: Vec3,
    ground: Vec3,
    yaw: f32,
    height: f32,
    width: f32,
    curve: f32,
    shade: f32,
    hue: f32,
    profile: &Profile,
    segments: u32,
) {
    let segments = segments.max(1);
    let (sin_y, cos_y) = yaw.sin_cos();
    let forward = Vec3::new(cos_y, 0.0, sin_y);
    let side = Vec3::new(-sin_y, 0.0, cos_y);
    // Shade the blade off the ground normal, leaned slightly toward its own
    // heading: flat enough to read as stylized, varied enough that a field of
    // tufts is not one flat colour.
    let normal = (ground + forward * 0.22).normalize_or_zero();
    let normal = if normal.length_squared() < 0.5 {
        Vec3::Y
    } else {
        normal
    };
    let base = positions.len() as u32;
    // Species jitter along ONE axis: green ↔ straw. r1 jittered the hue in
    // both directions, which pushed a slice of every meadow into cyan — the
    // teal spikes visible in the forest capture. Squaring keeps most blades
    // green and lets a minority go dry.
    let dry = (hue * hue).clamp(0.0, 1.0);

    let color_at = |t: f32| -> [f32; 4] {
        let mut c = [0.0f32; 4];
        for (i, slot) in c.iter_mut().enumerate().take(3) {
            let green = profile.root[i] + (profile.tip[i] - profile.root[i]) * t;
            let straw = profile.dry_root[i] + (profile.dry_tip[i] - profile.dry_root[i]) * t;
            *slot = ((green + (straw - green) * dry) * shade).clamp(0.0, 1.0);
        }
        c[3] = 1.0;
        c
    };

    for seg in 0..segments {
        let t = seg as f32 / segments as f32;
        // Taper hard: a blade that keeps its width to the top reads as a
        // ribbon, and a field of ribbons reads as spikes.
        let half = width * (1.0 - t * 0.78);
        let p = root + Vec3::Y * bend_height(height, curve, t) + forward * (height * curve * t * t);
        let color = color_at(t);
        for sign in [-1.0f32, 1.0] {
            positions.push((p + side * (half * sign)).to_array());
            normals.push(normal.to_array());
            uvs.push([t, height]);
            colors.push(color);
        }
    }
    // Tip: a single vertex, so the blade ends in a point instead of a stump.
    let tip = root + Vec3::Y * bend_height(height, curve, 1.0) + forward * (height * curve);
    positions.push(tip.to_array());
    normals.push(normal.to_array());
    uvs.push([1.0, height]);
    colors.push(color_at(1.0));

    for seg in 0..(segments - 1) {
        let a = base + seg * 2;
        let b = a + 1;
        let c = a + 2;
        let d = a + 3;
        indices.extend_from_slice(&[a, c, b, b, c, d]);
    }
    let last = base + (segments - 1) * 2;
    let tip_index = base + segments * 2;
    indices.extend_from_slice(&[last, tip_index, last + 1]);
}

/// Height of a blade at height-fraction `t` once it has arced over.
///
/// A blade that leans forward without losing height is a straight spike drawn
/// at an angle; the arc has to cost altitude or the silhouette never curls.
/// The lost height is quadratic in the horizontal travel — the small-angle
/// arc-length correction — which is exactly the shape a stalk makes.
fn bend_height(height: f32, curve: f32, t: f32) -> f32 {
    let reach = curve * t * t;
    height * t * (1.0 - 0.42 * reach * reach)
}

/// Append a flower: a stalk plus a whorl of petal quads at its top.
///
/// Cheap (a stalk blade + 3 petals ≈ 9 triangles) and it is the single thing
/// that most separates "a lawn" from "a meadow" in the BOTW frames — small
/// saturated dots scattered through a desaturated field.
#[allow(clippy::too_many_arguments)]
fn emit_flower(
    positions: &mut Vec<[f32; 3]>,
    normals: &mut Vec<[f32; 3]>,
    uvs: &mut Vec<[f32; 2]>,
    colors: &mut Vec<[f32; 4]>,
    indices: &mut Vec<u32>,
    root: Vec3,
    ground: Vec3,
    yaw: f32,
    height: f32,
    head: [f32; 3],
    stem: [f32; 3],
) {
    let (sin_y, cos_y) = yaw.sin_cos();
    let forward = Vec3::new(cos_y, 0.0, sin_y);
    let side = Vec3::new(-sin_y, 0.0, cos_y);
    let normal = (ground * 0.7 + Vec3::Y * 0.3).normalize_or(Vec3::Y);
    let stem_color = [stem[0], stem[1], stem[2], 1.0];
    let head_color = [head[0], head[1], head[2], 1.0];

    // Stalk: a two-vertex ribbon, thin enough to read as a stem.
    let base = positions.len() as u32;
    let tip = root + Vec3::Y * height + forward * (height * 0.12);
    let stem_half = 0.006;
    for (p, t) in [(root, 0.0f32), (tip, 1.0f32)] {
        for sign in [-1.0f32, 1.0] {
            positions.push((p + side * (stem_half * sign)).to_array());
            normals.push(normal.to_array());
            uvs.push([t, height]);
            colors.push(stem_color);
        }
    }
    indices.extend_from_slice(&[base, base + 2, base + 1, base + 1, base + 2, base + 3]);

    // Head: three petal quads fanned around the stalk tip. They sit at the
    // same uv height as the tip, so the wind carries the flower with it.
    let petal = (height * 0.22).clamp(0.025, 0.075);
    for i in 0..3u32 {
        let angle = yaw + i as f32 * (std::f32::consts::TAU / 3.0);
        let (s, c) = angle.sin_cos();
        let dir = Vec3::new(c, 0.0, s);
        let perp = Vec3::new(-s, 0.0, c);
        let quad = positions.len() as u32;
        let center = tip + Vec3::Y * (petal * 0.25);
        for (offset, _) in [
            (-perp * petal * 0.5, 0),
            (perp * petal * 0.5, 1),
            (-perp * petal * 0.35 + dir * petal, 2),
            (perp * petal * 0.35 + dir * petal, 3),
        ] {
            positions.push((center + offset).to_array());
            normals.push(Vec3::Y.to_array());
            uvs.push([1.0, height]);
            colors.push(head_color);
        }
        indices.extend_from_slice(&[quad, quad + 2, quad + 1, quad + 1, quad + 2, quad + 3]);
    }
}

/// Append a loose pebble: a squat six-triangle dome.
///
/// `uv.x = 0` on every vertex, so the wind shader leaves it planted — stone
/// that sways is worse than no stone at all.
#[allow(clippy::too_many_arguments)]
fn emit_pebble(
    positions: &mut Vec<[f32; 3]>,
    normals: &mut Vec<[f32; 3]>,
    uvs: &mut Vec<[f32; 2]>,
    colors: &mut Vec<[f32; 4]>,
    indices: &mut Vec<u32>,
    root: Vec3,
    yaw: f32,
    radius: f32,
    tint: [f32; 3],
    shade: f32,
) {
    const SIDES: u32 = 6;
    let base = positions.len() as u32;
    let color = [
        (tint[0] * shade).clamp(0.0, 1.0),
        (tint[1] * shade).clamp(0.0, 1.0),
        (tint[2] * shade).clamp(0.0, 1.0),
        1.0,
    ];
    // Ring slightly below ground so the pebble never shows a floating rim.
    for i in 0..SIDES {
        let angle = yaw + i as f32 * (std::f32::consts::TAU / SIDES as f32);
        let (s, c) = angle.sin_cos();
        let wobble = 0.72 + 0.5 * ((i * 2654) as f32 * 0.000_15).fract();
        let p = root + Vec3::new(c, 0.0, s) * radius * wobble - Vec3::Y * radius * 0.25;
        positions.push(p.to_array());
        normals.push(Vec3::new(c * 0.6, 0.8, s * 0.6).normalize().to_array());
        uvs.push([0.0, 0.0]);
        colors.push(color);
    }
    let apex = positions.len() as u32;
    positions.push((root + Vec3::Y * radius * 0.62).to_array());
    normals.push(Vec3::Y.to_array());
    uvs.push([0.0, 0.0]);
    colors.push([
        (color[0] * 1.18).min(1.0),
        (color[1] * 1.18).min(1.0),
        (color[2] * 1.18).min(1.0),
        1.0,
    ]);
    for i in 0..SIDES {
        let a = base + i;
        let b = base + (i + 1) % SIDES;
        indices.extend_from_slice(&[a, apex, b]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_block_is_rewritten() {
        let out = replace_config_block(
            GRASS_WGSL,
            "// === WORLD CONFIG ===\nX\n// === END WORLD CONFIG ===",
        );
        assert!(out.contains("\nX\n"), "bloco novo ausente");
        // The *declarations* must be gone — the shader body legitimately keeps
        // referencing `CFG_WIND_*`, which the new block (re)defines.
        assert!(
            !out.contains("const CFG_WIND_X"),
            "declaração antiga sobreviveu"
        );
        assert!(!out.contains("= 0.94;"), "valor antigo sobreviveu");
        assert!(out.contains("@vertex"), "resto do shader perdido");
    }

    #[test]
    fn tiers_are_ordered_and_thin_outward() {
        for pair in TIERS.windows(2) {
            assert!(pair[0].radius < pair[1].radius, "raios fora de ordem");
            assert!(pair[0].density > pair[1].density, "densidade não decai");
        }
        assert_eq!(tier_for(1.0), Some(0));
        assert_eq!(tier_for(40.0), Some(1));
        assert_eq!(tier_for(60.0), Some(2));
        assert_eq!(tier_for(400.0), None);
    }

    #[test]
    fn coverage_opens_clearings_and_closes_meadows() {
        // Sampled over a grid the coverage field must do BOTH: leave bald
        // patches and close over. A constant would mean grass reads as a
        // texture again.
        let mut low = 0;
        let mut high = 0;
        for i in 0..64 {
            for j in 0..64 {
                let c = coverage_at(i as f32 * 7.0, j as f32 * 7.0);
                assert!((0.0..=1.0).contains(&c), "cobertura fora de gama: {c}");
                if c < 0.2 {
                    low += 1;
                }
                if c > 0.8 {
                    high += 1;
                }
            }
        }
        assert!(low > 0, "sem clareiras");
        assert!(high > 0, "sem adensamentos");
    }

    #[test]
    fn blade_geometry_is_closed_and_indexed_in_range() {
        let mut positions = Vec::new();
        let mut normals = Vec::new();
        let mut uvs = Vec::new();
        let mut colors = Vec::new();
        let mut indices = Vec::new();
        let profile = GrassBiome::Vale.profile();
        for segments in 1..=4u32 {
            positions.clear();
            normals.clear();
            uvs.clear();
            colors.clear();
            indices.clear();
            emit_blade(
                &mut positions,
                &mut normals,
                &mut uvs,
                &mut colors,
                &mut indices,
                Vec3::ZERO,
                Vec3::Y,
                0.7,
                0.5,
                0.03,
                0.3,
                1.0,
                0.5,
                &profile,
                segments,
            );
            let verts = (segments * 2 + 1) as usize;
            assert_eq!(positions.len(), verts);
            assert_eq!(normals.len(), verts);
            assert_eq!(uvs.len(), verts);
            assert_eq!(colors.len(), verts);
            assert_eq!(indices.len(), (segments as usize * 2 - 1) * 3);
            assert!(
                indices.iter().all(|i| (*i as usize) < verts),
                "índice fora do buffer"
            );
            // The wind contract: uv.x is the height fraction, uv.y the height.
            assert!(uvs.iter().all(|uv| (0.0..=1.0).contains(&uv[0])));
            assert!(uvs.iter().all(|uv| (uv[1] - 0.5).abs() < 1e-6));
        }
    }

    #[test]
    fn biome_falls_back_to_vale_without_regions() {
        assert_eq!(biome_at(None, 0.0, 0.0), GrassBiome::Vale);
    }

    #[test]
    fn day_tint_is_identity_at_noon_and_silhouette_at_night() {
        let noon = day_tint(1.0);
        assert!(
            (noon[0] - 1.0).abs() < 1e-5 && (noon[1] - 1.0).abs() < 1e-5,
            "meio-dia tem de ser exatamente o look aprovado no r1: {noon:?}"
        );
        let night = day_tint(0.0);
        assert!(
            night[0] < 0.2 && night[1] < 0.2 && night[2] < 0.25,
            "noite tem de silhuetar: {night:?}"
        );
        assert!(night[2] > night[0], "noite azulada, não cinza morta");
        // Golden hour: warmth peaks mid-ramp — warmer than neutral, subtle.
        let golden = day_tint(0.5);
        assert!(golden[0] > golden[2], "golden hour aquece: {golden:?}");
        assert!(golden[0] < 1.2, "golden hour é ligeiro: {golden:?}");
    }

    #[test]
    fn biome_reads_the_authored_wedges() {
        let regions = BiomeRegions {
            list: vec![
                crate::worldsys::BiomeRegionData {
                    id: "desert".into(),
                    display_name: "Deserto".into(),
                    polygon: vec![
                        [56.0, -56.0],
                        [56.0, 56.0],
                        [4040.0, 4040.0],
                        [4040.0, -4040.0],
                    ],
                    fog_density: 0.0,
                    tint: None,
                    pp_exposure: None,
                    pp_bloom_strength: None,
                },
                crate::worldsys::BiomeRegionData {
                    id: "dark-forest".into(),
                    display_name: "Floresta Sombria".into(),
                    polygon: vec![
                        [-56.0, 56.0],
                        [56.0, 56.0],
                        [4040.0, 4040.0],
                        [-4040.0, 4040.0],
                    ],
                    fog_density: 0.0,
                    tint: None,
                    pp_exposure: None,
                    pp_bloom_strength: None,
                },
            ],
        };
        assert_eq!(biome_at(Some(&regions), 190.0, 64.0), GrassBiome::Desert);
        assert_eq!(biome_at(Some(&regions), -92.0, 176.0), GrassBiome::Forest);
        assert_eq!(biome_at(Some(&regions), 0.0, -10.0), GrassBiome::Vale);
    }

    #[test]
    fn lattice_interpolates_between_samples() {
        let mut height = [0.0f32; LATTICE * LATTICE];
        for iz in 0..LATTICE {
            for ix in 0..LATTICE {
                height[iz * LATTICE + ix] = ix as f32;
            }
        }
        // A pure X ramp: mid-tile must read the middle sample.
        let mid = lattice_height(&height, 0.5, 0.5);
        assert!((mid - (LATTICE - 1) as f32 * 0.5).abs() < 1e-3, "mid={mid}");
        // …and the normal must tilt away from +X, never stay flat.
        let n = lattice_normal(&height, 0.5, 0.5, 1.0);
        assert!(n.x < -0.1, "normal não segue a rampa: {n:?}");
    }

    #[test]
    fn desert_and_forest_do_not_share_a_palette() {
        let desert = GrassBiome::Desert.profile();
        let forest = GrassBiome::Forest.profile();
        // Dry grass is warmer (more red than green at the tip); forest grass
        // is the reverse. If these ever converge the biomes read the same.
        assert!(desert.tip[0] > desert.tip[1], "deserto não é palha");
        assert!(forest.tip[1] > forest.tip[0], "floresta não é verde");
        assert!(forest.height > desert.height, "floresta não é mais alta");
        assert!(desert.density < forest.density, "deserto não é mais seco");
    }
}
