//! Recipe layer: expanded XML nodes → entity IR with Bevy naming.
//!
//! Tags and attributes follow Bevy component/field names (`translation`,
//! `euler`, `half-size`, `base-color`, `metallic`, …). Tag matching is
//! case-insensitive; unknown attributes become warnings and unknown elements
//! are skipped as no-ops (so worlds written against a bigger tag vocabulary
//! still run — `analyze` reports what was skipped).

pub mod spawn;
pub mod transform;

use crate::physics::{PhysicsSpec, parse_body, parse_collider};
use std::collections::BTreeMap;

use anyhow::{Result, bail};
use bevy::math::Vec2;

use crate::terrain::TerrainSpec;
use crate::terrain::roads::{RoadNetworkSpec, RoadProfile, RoadSpec, SegmentSpec, WaySpec};
use crate::terrain::spec::TerrainPadSpec;
use crate::terrain::water::{LakeSpec, RiverSpec};
use crate::xml::{XmlNode, values};

/// Tags accepted inside a `<world>` root (lowercase canonical spellings).
/// Anything else parses as a no-op skip; `analyze` reports the coverage.
pub const KNOWN_TAGS: &[&str] = &[
    "entity",
    "group",
    "cuboid",
    "sphere",
    "cylinder",
    "plane",
    "capsule",
    "pointlight",
    "directionallight",
    "ambientlight",
    "orbitcamera",
    "terrain",
    "terrainpad",
    "lake",
    "river",
    "road",
    "roadnetwork",
    "gltfscene",
    "staticspawner",
    "particlesystem",
    "spawnexclusion",
    "dynamicspawner",
    "vegetation",
    "playergltf",
    "thirdpersoncamera",
    "dialoguenpc",
    "resourcechip",
    "audiomixer",
    "musiclayer",
    "sky",
    "daycycle",
    "weather",
    "biomeregion",
    "worldborder",
    "sky",
    "navmesh",
    "spawngate",
    "projectiletemplate",
    "questtracker",
    "waypointarrow",
    "postfxdebugtoggle",
    "adaptivequality",
    "hudscreenlayer",
    "healthbar",
    "xpbar",
    "bossbar",
    "targetbar",
    "minimap",
    "compass",
    "interactionprompt",
    "dialogueballoon",
    "tabbedmodal",
];

/// A parsed world: clear color, entity tree and non-fatal warnings.
#[derive(Debug, Clone, Default)]
pub struct ParsedWorld {
    pub clear_color: Option<[f32; 3]>,
    pub entities: Vec<EntitySpec>,
    pub warnings: Vec<String>,
    /// Elements skipped because their tag is not implemented, by tag name.
    pub skipped_tags: BTreeMap<String, usize>,
}

/// Accumulates non-fatal parse findings.
#[derive(Debug, Default)]
struct ParseCtx {
    warnings: Vec<String>,
    skipped: BTreeMap<String, usize>,
}

/// Local transform of an entity (world transform comes from the hierarchy).
#[derive(Debug, Clone)]
pub struct TransformSpec {
    pub translation: [f32; 3],
    /// Degrees, XYZ order (`euler="x y z"`).
    pub euler_deg: Option<[f32; 3]>,
    /// Raw quaternion `x y z w` — wins over `euler` when both are present.
    pub rotation_quat: Option<[f32; 4]>,
    pub scale: [f32; 3],
}

impl Default for TransformSpec {
    fn default() -> Self {
        Self {
            translation: [0.0; 3],
            euler_deg: None,
            rotation_quat: None,
            scale: [1.0; 3],
        }
    }
}

/// Mesh primitive shapes, mirroring the `bevy::math::primitives` constructors.
#[derive(Debug, Clone)]
pub enum Shape {
    /// `Cuboid` — half extents per axis.
    Cuboid { half_size: [f32; 3] },
    /// `Sphere` — radius.
    Sphere { radius: f32 },
    /// `Cylinder` — half height + radius.
    Cylinder { half_height: f32, radius: f32 },
    /// `Plane3d` — half size on the XZ plane.
    Plane { half_size: [f32; 2] },
    /// `Capsule` — radius + half height (vertical).
    Capsule { radius: f32, half_height: f32 },
}

/// `StandardMaterial` overrides; unset fields use Bevy defaults.
#[derive(Debug, Clone, Default)]
pub struct MaterialSpec {
    pub base_color: Option<[f32; 3]>,
    pub metallic: Option<f32>,
    pub roughness: Option<f32>,
}

#[derive(Debug, Clone)]
pub enum EntityKind {
    /// Transform-only container (the `entity` and `group` tags).
    Group,
    Primitive {
        shape: Shape,
        material: MaterialSpec,
    },
    PointLight {
        color: Option<[f32; 3]>,
        intensity: Option<f32>,
        radius: Option<f32>,
        shadows: Option<bool>,
    },
    DirectionalLight {
        color: Option<[f32; 3]>,
        /// Lux; unset uses the Bevy default (10,000 — ambient daylight).
        illuminance: Option<f32>,
        /// Direction the light travels, world space (normalized on spawn).
        direction: [f32; 3],
        shadows: Option<bool>,
    },
    /// Applied as a world resource, not an entity.
    AmbientLight {
        color: Option<[f32; 3]>,
        brightness: Option<f32>,
    },
    OrbitCamera {
        /// Target entity resolved by `name` at runtime; `None` looks at the origin.
        target: Option<String>,
        distance: f32,
        height: f32,
        /// `Some` only when `pitch` was set explicitly (overrides `height`).
        pitch_deg: Option<f32>,
        /// Degrees per pixel of mouse drag; `None` = engine default.
        mouse_sensitivity: Option<f32>,
        /// Follow-point low-pass time constant (s); `None` = engine default.
        follow_lag: Option<f32>,
        /// Yaw trail low-pass time constant (s); `None` = engine default.
        turn_lag: Option<f32>,
        /// Camera clearance above terrain (m); `None` = engine default.
        min_terrain_distance: Option<f32>,
        /// Vertical field of view in degrees (Bevy default 45°).
        fov_deg: Option<f32>,
    },
    /// `<Terrain>` — declarative heightfield terrain (consumed by the terrain
    /// runtime; the element itself spawns the chunk hierarchy).
    Terrain { spec: TerrainSpec },
    /// `<TerrainPad>` — ground-flattening pad (world XZ, hierarchy-translated).
    TerrainPad { spec: TerrainPadSpec },
    /// `<Lake>` — carved bowl + water mirror.
    Lake { spec: LakeSpec },
    /// `<River>` — carved channel + water ribbon.
    River { spec: RiverSpec },
    /// `<Road>` — carved corridor + ribbon.
    Road { spec: RoadSpec },
    /// `<RoadNetwork>` — one road per `<Segment>` (expanded at carve time).
    RoadNetwork { spec: RoadNetworkSpec },
    /// glTF asset loaded async; its default scene spawns under the entity
    /// (the entity's transform applies). Paths starting with `/` are relative
    /// to the engine asset root.
    GltfScene { url: String },
    /// `<StaticSpawner>` — instancias `count` do template GLB sobre o terreno
    /// (posições determinísticas por `seed`; regras de água/declive/sobreposição).
    /// Consumido pelo runtime de spawner, não spawna entidade própria.
    StaticSpawner { spec: StaticSpawnerSpec },
    /// `<DynamicSpawner>` — mesmas regras de colocação do StaticSpawner; as
    /// entidades nascem marcadas como criaturas (IA/combat chegam com scripts).
    DynamicSpawner { spec: StaticSpawnerSpec },
    /// `<SpawnExclusion>` — círculo global onde spawners não colocam instâncias.
    /// Recolhido num recurso global, não spawna entidade.
    SpawnExclusion { center: [f32; 2], radius: f32 },
    /// `<Vegetation>` — erva/flores densas por densidade/km² (convertida num
    /// grupo de spawner com cap; instancing GPU é follow-up).
    Vegetation { spec: VegetationSpec },
    /// `<ParticleSystem>` — emissor de partículas (fogueiras, poeira, clima).
    /// Entity própria; emissor CPU billboard desenhado em `particles`.
    ParticleSystem { spec: ParticleSpec },
    /// `<PlayerGLTF model-url>` — the controllable hero: glTF scene plus the
    /// [`crate::player::Player`] movement component.
    PlayerGltf { url: String },
    /// `<DialogueNPC>` — marks the parent NPC as a dialogue target
    /// (`dialogue-id`), with a floating marker at `marker-height`.
    DialogueNpc {
        dialogue_id: String,
        marker_height: f32,
    },
    /// `<ResourceChip>` — HUD readout of one resource (visual HUD lands with
    /// the UI phase; the entity carries the parsed data).
    ResourceChip {
        resource: String,
        icon: String,
        target: String,
    },
    /// HUD screen elements (bars, minimap, compass, modal, prompt…): the
    /// original tag plus its raw attributes — `src/hud.rs` builds the UI.
    HudElement {
        tag: String,
        attrs: Vec<(String, String)>,
    },
    /// `<AudioMixer>` bus volumes (world resource).
    AudioMixer { master: f32, music: f32, sfx: f32 },
    /// `<MusicLayer layer sound base-volume>` — looped BGM bus layer; the
    /// driver crossfades layers by player zone (`src/music.rs`).
    MusicLayer { layer: String, base_volume: f32 },
    /// `<DayCycle>` — advances minute-of-day and drives the ambient light
    /// and the procedural sun.
    DayCycle {
        minute_of_day: f32,
        minutes_per_real_second: f32,
        dawn_minute: f32,
        dusk_minute: f32,
        ambient_day: f32,
        ambient_night: f32,
        drive_ambient: bool,
        max_sun_elevation: f32,
        sun_azimuth_base: f32,
    },
    /// `<Weather>` — wind/clouds/rain config (rain spawns a rain emitter).
    Weather {
        wind: [f32; 2],
        wind_strength: f32,
        clouds: f32,
        rain: f32,
        cycle: bool,
    },
    /// `<BiomeRegion>` — biome polygon with fog/tint data (fog rendering is
    /// a follow-up; the data drives BGM/hint systems).
    BiomeRegion {
        id: String,
        polygon: Vec<[f32; 2]>,
        fog_density: f32,
        tint: Option<[f32; 3]>,
    },
    /// `<WorldBorder radius>` — keeps the player inside the world disc.
    WorldBorder {
        radius: f32,
        warn_seconds: f32,
        margin: f32,
    },
    /// Engine config element kept as raw data (`Sky`, `NavMesh`,
    /// `SpawnGate`, `ProjectileTemplate`, `PostFxDebugToggle`,
    /// `AdaptiveQuality`) — data now, runtime hooks as phases land.
    EngineConfig {
        tag: String,
        attrs: Vec<(String, String)>,
    },
}

/// `<DayCycle>` clock config (ambient light driver).
#[derive(Debug, Clone)]
pub struct DayCycleConfig {
    pub minute_of_day: f32,
    pub minutes_per_real_second: f32,
    pub dawn_minute: f32,
    pub dusk_minute: f32,
    pub ambient_day: f32,
    pub ambient_night: f32,
    pub drive_ambient: bool,
    pub max_sun_elevation: f32,
    pub sun_azimuth_base: f32,
}

/// `<Weather>` config (wind/clouds/rain).
#[derive(Debug, Clone)]
pub struct WeatherConfig {
    pub wind: [f32; 2],
    pub wind_strength: f32,
    pub clouds: f32,
    pub rain: f32,
    pub cycle: bool,
}

/// Emitter config of a `<ParticleSystem>`: a preset plus the
/// `particle-emitter="…"` component-string overrides found in the world.
#[derive(Debug, Clone)]
pub struct ParticleSpec {
    pub preset: String,
    pub emission_rate: Option<f32>,
    pub life: Option<(f32, f32)>,
    pub speed: Option<(f32, f32)>,
    pub size: Option<(f32, f32)>,
    pub color: Option<[f32; 3]>,
    /// `shape-radius`: spawn spread around the emitter origin (meters).
    pub shape_radius: Option<f32>,
    pub looping: bool,
    /// Authored `world-space` flag (Viber emitters are always local — the
    /// emitters in this world are static, so behaviour is equivalent).
    pub world_space: bool,
}

/// Placement rules + template urls of a `<StaticSpawner>` (attribute names as
/// authored in the source worlds — the element migrates verbatim).
#[derive(Debug, Clone)]
pub struct StaticSpawnerSpec {
    pub seed: u64,
    pub count: u32,
    pub region_min: [f32; 3],
    pub region_max: [f32; 3],
    pub cluster_count: u32,
    pub cluster_radius: f32,
    pub footprint_radius: f32,
    pub avoid_overlaps: bool,
    pub max_slope_deg: f32,
    pub avoid_water: bool,
    /// Only place where the terrain is inside a water carve zone.
    pub in_water: bool,
    /// Only place on dry land within `near_water_radius` of water.
    pub near_water: bool,
    /// Shoreline width for `near_water` (meters).
    pub near_water_radius: f32,
    /// Never place on a carved road ribbon.
    pub avoid_road: bool,
    pub align_to_terrain: bool,
    pub scale_min: f32,
    pub scale_max: f32,
    pub scale_axis_min: f32,
    pub scale_axis_max: f32,
    pub random_yaw: bool,
    pub max_distance: f32,
    /// glTF urls found in the template subtree (GLTFLoader/GltfScene), in
    /// document order; one is picked per instance via the seeded RNG.
    pub template_urls: Vec<String>,
}

/// `<Vegetation>`: dense foliage spread by density per km². The original
/// engine GPU-instances ~100k quads; Viber spawns template scenes with a
/// per-tag safety cap instead.
#[derive(Debug, Clone)]
pub struct VegetationSpec {
    pub meshes: Vec<String>,
    pub density_per_km2: f32,
    pub seed: u64,
    pub region_min: [f32; 3],
    pub region_max: [f32; 3],
    pub scale_min: f32,
    pub scale_max: f32,
    pub scale_axis_min: f32,
    pub scale_axis_max: f32,
    pub max_slope_deg: f32,
    pub avoid_water: bool,
    /// Never place on a carved road ribbon.
    pub avoid_road: bool,
    /// Reject a candidate that lands within another instance's footprint.
    pub avoid_overlaps: bool,
    /// Give every instance a random heading, so a stand of identical trees
    /// does not read as a row of clones.
    pub random_yaw: bool,
    pub max_distance: f32,
    pub cluster_count: u32,
    pub cluster_radius: f32,
    /// Viber-specific instance cap (default 800 per tag).
    pub max_instances: u32,
}

impl VegetationSpec {
    /// Region area in km² (XZ extents).
    pub fn area_km2(&self) -> f32 {
        let dx = (self.region_max[0] - self.region_min[0]).abs();
        let dz = (self.region_max[2] - self.region_min[2]).abs();
        (dx * dz) / 1.0e6
    }

    /// Instance count: density × area, clamped to the safety cap.
    pub fn instance_count(&self) -> u32 {
        let target = (self.area_km2() * self.density_per_km2).ceil();
        target.min(self.max_instances as f32) as u32
    }

    /// Convert to a placement spec consumable by the spawner runtime.
    pub fn to_spawner_spec(&self) -> StaticSpawnerSpec {
        StaticSpawnerSpec {
            seed: self.seed,
            count: self.instance_count(),
            region_min: self.region_min,
            region_max: self.region_max,
            cluster_count: self.cluster_count,
            cluster_radius: self.cluster_radius,
            footprint_radius: 0.4,
            avoid_overlaps: self.avoid_overlaps,
            max_slope_deg: self.max_slope_deg,
            avoid_water: self.avoid_water,
            in_water: false,
            near_water: false,
            near_water_radius: DEFAULT_NEAR_WATER_RADIUS,
            avoid_road: self.avoid_road,
            align_to_terrain: true,
            scale_min: self.scale_min,
            scale_max: self.scale_max,
            scale_axis_min: self.scale_axis_min,
            scale_axis_max: self.scale_axis_max,
            random_yaw: self.random_yaw,
            max_distance: self.max_distance,
            template_urls: self.meshes.clone(),
        }
    }
}

/// Shoreline width for `near-water` placement (meters) — how far from a water
/// body a point still counts as bank.
pub const DEFAULT_NEAR_WATER_RADIUS: f32 = 4.0;

/// A resolved recipe: everything needed to spawn one Bevy entity.
#[derive(Debug, Clone)]
pub struct EntitySpec {
    pub name: Option<String>,
    /// Runtime element tag (parsed; consumed by later phases).
    #[allow(dead_code)]
    pub tag: Option<String>,
    /// Script file reserved for the Luau runtime (parsed, not yet executed).
    #[allow(dead_code)]
    pub script: Option<String>,
    pub transform: TransformSpec,
    /// Colliders / rigid bodies for the physics runtime.
    pub physics: PhysicsSpec,
    pub kind: EntityKind,
    pub children: Vec<EntitySpec>,
}

/// Parse the root of an expanded world.
pub fn parse_world(root_attrs: &[(String, String)], nodes: &[XmlNode]) -> Result<ParsedWorld> {
    let mut ctx = ParseCtx::default();
    let mut clear_color = None;
    for (key, value) in root_attrs {
        match key.as_str() {
            "clear-color" => {
                clear_color = Some(values::parse_color(value, "<world clear-color>")?);
            }
            other => ctx
                .warnings
                .push(format!("<world>: ignored attribute `{other}`")),
        }
    }
    let entities = parse_entities(nodes, &mut ctx)?;
    let ambient_count = count_ambient_lights(&entities);
    if ambient_count > 1 {
        ctx.warnings.push(format!(
            "multiple <AmbientLight> elements ({ambient_count}) — the last one wins"
        ));
    }
    let entities = demote_extra_cameras(entities, &mut ctx.warnings);
    let mut warnings = ctx.warnings;
    if !ctx.skipped.is_empty() {
        let summary: Vec<String> = ctx
            .skipped
            .iter()
            .map(|(tag, count)| format!("<{tag}>×{count}"))
            .collect();
        warnings.push(format!(
            "not implemented, skipped as no-op: {}",
            summary.join(", ")
        ));
    }
    Ok(ParsedWorld {
        clear_color,
        entities,
        warnings,
        skipped_tags: ctx.skipped,
    })
}

/// Ambient lights are applied as a world resource, so more than one is
/// almost always an authoring mistake — counted for the parse warning.
fn count_ambient_lights(entities: &[EntitySpec]) -> usize {
    entities
        .iter()
        .map(|spec| {
            let own = usize::from(matches!(spec.kind, EntityKind::AmbientLight { .. }));
            own + count_ambient_lights(&spec.children)
        })
        .sum()
}

fn parse_entities(nodes: &[XmlNode], ctx: &mut ParseCtx) -> Result<Vec<EntitySpec>> {
    nodes
        .iter()
        .map(|n| parse_entity(n, ctx))
        .collect::<Result<Vec<Option<_>>>>()
        .map(|specs| specs.into_iter().flatten().collect())
}

/// `Ok(None)` = element skipped as a no-op (unknown tag), subtree included.
fn parse_entity(node: &XmlNode, ctx: &mut ParseCtx) -> Result<Option<EntitySpec>> {
    let lower = node.tag.to_ascii_lowercase();
    match lower.as_str() {
        "entity" | "group" => finish_group(node, ctx).map(Some),
        "cuboid" | "sphere" | "cylinder" | "plane" | "capsule" => {
            finish_primitive(node, ctx).map(Some)
        }
        "pointlight" => finish_point_light(node, ctx).map(Some),
        "directionallight" => finish_directional_light(node, ctx).map(Some),
        "ambientlight" => finish_ambient_light(node, ctx).map(Some),
        "orbitcamera" => finish_orbit_camera(node, ctx, false).map(Some),
        "gltfscene" => match node.attr("url").map(str::trim).filter(|s| !s.is_empty()) {
            Some(url) => finish_gltf_scene(node, url.to_string(), ctx).map(Some),
            None => {
                ctx.warnings
                    .push(format!("<{}>: missing url — skipped", node.tag));
                Ok(None)
            }
        },
        "staticspawner" => finish_static_spawner(node, false, ctx).map(Some),
        "dynamicspawner" => finish_static_spawner(node, true, ctx).map(Some),
        "spawnexclusion" => finish_spawn_exclusion(node, ctx).map(Some),
        "vegetation" => finish_vegetation(node, ctx).map(Some),
        "playergltf" => match node
            .attr("model-url")
            .map(str::trim)
            .filter(|s| !s.is_empty())
        {
            Some(url) => finish_player_gltf(node, url.to_string(), ctx).map(Some),
            None => {
                ctx.warnings
                    .push(format!("<{}>: missing model-url — skipped", node.tag));
                Ok(None)
            }
        },
        "thirdpersoncamera" => finish_orbit_camera(node, ctx, true).map(Some),
        "audiomixer" => finish_audio_mixer(node, ctx).map(Some),
        "sky" => finish_sky(node, ctx).map(Some),
        "navmesh" | "spawngate" | "projectiletemplate" | "postfxdebugtoggle"
        | "adaptivequality" => finish_engine_config(node, ctx).map(Some),
        "daycycle" => finish_daycycle(node, ctx).map(Some),
        "weather" => finish_weather(node, ctx).map(Some),
        "biomeregion" => finish_biome_region(node, ctx).map(Some),
        "worldborder" => finish_world_border(node, ctx).map(Some),
        "questtracker" | "waypointarrow" => finish_hud_element(node, ctx).map(Some),
        "musiclayer" => finish_music_layer(node, ctx).map(Some),
        "dialoguenpc" => match node
            .attr("dialogue-id")
            .map(str::trim)
            .filter(|s| !s.is_empty())
        {
            Some(_) => finish_dialogue_npc(node, ctx).map(Some),
            None => {
                ctx.warnings
                    .push(format!("<{}>: missing dialogue-id — skipped", node.tag));
                Ok(None)
            }
        },
        "resourcechip" => finish_resource_chip(node, ctx).map(Some),
        "hudscreenlayer" | "healthbar" | "xpbar" | "bossbar" | "targetbar" | "minimap"
        | "compass" | "interactionprompt" | "dialogueballoon" | "tabbedmodal" => {
            finish_hud_element(node, ctx).map(Some)
        }
        "particlesystem" => finish_particle_system(node, ctx).map(Some),
        "terrain" => finish_terrain(node, ctx).map(Some),
        "terrainpad" => finish_terrain_pad(node, ctx).map(Some),
        "lake" => finish_lake(node, ctx).map(Some),
        "river" => finish_river(node, ctx).map(Some),
        "road" => finish_road(node, ctx).map(Some),
        "roadnetwork" => finish_road_network(node, ctx).map(Some),
        "include" => bail!(
            "<{}>: <include> must be expanded before parsing (use xml::include::load_world)",
            node.tag
        ),
        "world" | "scene" => bail!("<{}>: world roots cannot be nested", node.tag),
        _ => {
            *ctx.skipped.entry(node.tag.clone()).or_insert(0) += 1;
            Ok(None)
        }
    }
}

/// Attributes shared by every entity tag.
struct Common {
    name: Option<String>,
    tag: Option<String>,
    script: Option<String>,
    transform: TransformSpec,
    /// Colliders / rigid bodies (`collider`, `rigidbody`, `body`).
    physics: PhysicsSpec,
}

/// Parse the universal attributes, returning the ones left for the kind parser.
fn parse_common(node: &XmlNode, ctx: &mut ParseCtx) -> Result<(Common, Vec<(String, String)>)> {
    let ctx_tag = format!("<{}>", node.tag);
    let mut common = Common {
        name: None,
        tag: None,
        script: None,
        transform: TransformSpec::default(),
        physics: PhysicsSpec::default(),
    };
    let mut rest = Vec::new();
    for (key, value) in &node.attrs {
        match key.as_str() {
            "name" => common.name = Some(value.clone()),
            "tag" => common.tag = Some(value.clone()),
            "script" => common.script = Some(value.clone()),
            "collider" => {
                let (shape, warning) = parse_collider(value);
                common.physics.collider = shape;
                if let Some(warning) = warning {
                    ctx.warnings.push(format!("{ctx_tag}: {warning}"));
                }
            }
            // `rigidbody` is the full component string; `body` is the bare
            // shorthand `<Group>` uses.
            "rigidbody" | "body" => {
                let (kind, mass, gravity_scale) = parse_body(value);
                common.physics.body = kind;
                common.physics.mass = mass;
                common.physics.gravity_scale = gravity_scale;
            }
            // `pos` is the original-format spelling (verbatim tags keep it)
            "translation" | "pos" => {
                common.transform.translation =
                    values::parse_vec3(value, &format!("{ctx_tag} translation"))?;
            }
            "euler" => {
                common.transform.euler_deg =
                    Some(values::parse_vec3(value, &format!("{ctx_tag} euler"))?);
            }
            "rotation" => {
                common.transform.rotation_quat =
                    Some(values::parse_vec4(value, &format!("{ctx_tag} rotation"))?);
            }
            "scale" => {
                common.transform.scale = values::parse_vec3(value, &format!("{ctx_tag} scale"))?;
            }
            "transform" => {
                // Component-string syntax: `transform="pos: x y z; euler: …"`.
                let tctx = format!("{ctx_tag} transform");
                for (key, value) in parse_component_string(value) {
                    match key.as_str() {
                        "pos" | "position" => {
                            common.transform.translation =
                                values::parse_vec3(&value, &format!("{tctx} pos"))?;
                        }
                        "euler" | "rotation" => {
                            common.transform.euler_deg =
                                Some(values::parse_vec3(&value, &format!("{tctx} euler"))?);
                        }
                        "scale" => {
                            common.transform.scale =
                                values::parse_vec3(&value, &format!("{tctx} scale"))?;
                        }
                        // unknown transform components (component-string extras)
                        _ => {}
                    }
                }
            }
            other => rest.push((other.to_string(), value.clone())),
        }
    }
    Ok((common, rest))
}

/// Parse a VibeGame component string (`"pos: 0 1 0; preset: fire"`) into
/// key/value pairs; keys are lowercased and trimmed.
pub fn parse_component_string(value: &str) -> Vec<(String, String)> {
    value
        .split(';')
        .filter_map(|part| {
            let part = part.trim();
            if part.is_empty() {
                return None;
            }
            let (key, val) = part.split_once(':')?;
            Some((key.trim().to_ascii_lowercase(), val.trim().to_string()))
        })
        .collect()
}

fn warn_ignored(node: &XmlNode, rest: Vec<(String, String)>, ctx: &mut ParseCtx) {
    for (key, _) in rest {
        ctx.warnings
            .push(format!("<{}>: ignored attribute `{key}`", node.tag));
    }
}

fn finish_group(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_ignored(node, rest, ctx);
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::Group,
        children: parse_entities(&node.children, ctx)?,
    })
}

fn finish_primitive(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let lower = node.tag.to_ascii_lowercase();
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut shape = match lower.as_str() {
        "cuboid" => Shape::Cuboid {
            half_size: [0.5; 3],
        },
        "sphere" => Shape::Sphere { radius: 0.5 },
        "cylinder" => Shape::Cylinder {
            half_height: 0.5,
            radius: 0.5,
        },
        "plane" => Shape::Plane {
            half_size: [1.0; 2],
        },
        "capsule" => Shape::Capsule {
            radius: 0.5,
            half_height: 0.5,
        },
        _ => unreachable!("finish_primitive called for non-primitive"),
    };
    let mut material = MaterialSpec::default();
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "half-size" => match &mut shape {
                Shape::Cuboid { half_size } => {
                    *half_size = values::parse_vec3(&value, &kctx)?;
                }
                Shape::Plane { half_size } => {
                    *half_size = values::parse_vec2(&value, &kctx)?;
                }
                _ => ctx
                    .warnings
                    .push(format!("{ctx_tag}: `{key}` does not apply to this shape")),
            },
            "radius" => match &mut shape {
                Shape::Sphere { radius }
                | Shape::Cylinder { radius, .. }
                | Shape::Capsule { radius, .. } => *radius = values::parse_f32(&value, &kctx)?,
                _ => ctx
                    .warnings
                    .push(format!("{ctx_tag}: `{key}` does not apply to this shape")),
            },
            "half-height" => match &mut shape {
                Shape::Cylinder { half_height, .. } | Shape::Capsule { half_height, .. } => {
                    *half_height = values::parse_f32(&value, &kctx)?;
                }
                _ => ctx
                    .warnings
                    .push(format!("{ctx_tag}: `{key}` does not apply to this shape")),
            },
            "base-color" => material.base_color = Some(values::parse_color(&value, &kctx)?),
            "metallic" => material.metallic = Some(values::parse_f32(&value, &kctx)?),
            "roughness" => material.roughness = Some(values::parse_f32(&value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::Primitive { shape, material },
        children: parse_entities(&node.children, ctx)?,
    })
}

fn finish_point_light(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut color = None;
    let mut intensity = None;
    let mut radius = None;
    let mut shadows = None;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "color" => color = Some(values::parse_color(&value, &kctx)?),
            "intensity" => intensity = Some(values::parse_f32(&value, &kctx)?),
            "radius" => radius = Some(values::parse_f32(&value, &kctx)?),
            "shadows" => shadows = Some(values::parse_bool(&value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::PointLight {
            color,
            intensity,
            radius,
            shadows,
        },
        children: parse_entities(&node.children, ctx)?,
    })
}

fn finish_directional_light(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut color = None;
    let mut illuminance = None;
    let mut shadows = None;
    let mut direction = [-1.0, -1.0, -1.0];
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "color" => color = Some(values::parse_color(&value, &kctx)?),
            "illuminance" => illuminance = Some(values::parse_f32(&value, &kctx)?),
            "direction" => direction = values::parse_vec3(&value, &kctx)?,
            "shadows" => shadows = Some(values::parse_bool(&value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::DirectionalLight {
            color,
            illuminance,
            direction,
            shadows,
        },
        children: parse_entities(&node.children, ctx)?,
    })
}

fn finish_ambient_light(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut color = None;
    let mut brightness = None;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "color" => color = Some(values::parse_color(&value, &kctx)?),
            "brightness" => brightness = Some(values::parse_f32(&value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::AmbientLight { color, brightness },
        children: parse_entities(&node.children, ctx)?,
    })
}

fn finish_orbit_camera(
    node: &XmlNode,
    ctx: &mut ParseCtx,
    third_person: bool,
) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let (default_target, default_distance, default_height) = if third_person {
        (Some("player".to_string()), 4.0, 1.6)
    } else {
        (None, 12.0, 4.0)
    };
    let mut kind = EntityKind::OrbitCamera {
        target: default_target,
        distance: default_distance,
        height: default_height,
        pitch_deg: None,
        mouse_sensitivity: None,
        follow_lag: None,
        turn_lag: None,
        min_terrain_distance: None,
        fov_deg: None,
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        let EntityKind::OrbitCamera {
            target,
            distance,
            height,
            pitch_deg,
            mouse_sensitivity,
            follow_lag,
            turn_lag,
            min_terrain_distance,
            fov_deg,
        } = &mut kind
        else {
            unreachable!("kind is OrbitCamera here");
        };
        match key.as_str() {
            "target" => *target = Some(value.trim().to_string()),
            "distance" => *distance = values::parse_f32(&value, &kctx)?,
            "height" => *height = values::parse_f32(&value, &kctx)?,
            "pitch" => *pitch_deg = Some(values::parse_f32(&value, &kctx)?),
            "mouse-sensitivity" => *mouse_sensitivity = Some(values::parse_f32(&value, &kctx)?),
            "follow-lag" => *follow_lag = Some(values::parse_f32(&value, &kctx)?),
            "turn-lag" => *turn_lag = Some(values::parse_f32(&value, &kctx)?),
            "min-terrain-distance" => {
                *min_terrain_distance = Some(values::parse_f32(&value, &kctx)?)
            }
            "fov" => *fov_deg = Some(values::parse_f32(&value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind,
        children: parse_entities(&node.children, ctx)?,
    })
}

/// glTF urls in a spawner's template subtree (original `GLTFLoader` names and
/// native `GltfScene` both accepted), in document order.
fn collect_template_urls(node: &XmlNode, out: &mut Vec<String>) {
    let lower = node.tag.to_ascii_lowercase();
    if matches!(lower.as_str(), "gltfloader" | "gltfscene") {
        if let Some(url) = node.attr("url").map(str::trim).filter(|s| !s.is_empty()) {
            out.push(url.to_string());
        }
    }
    for child in &node.children {
        collect_template_urls(child, out);
    }
}

fn finish_static_spawner(node: &XmlNode, dynamic: bool, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut spec = StaticSpawnerSpec {
        seed: 0,
        count: 1,
        region_min: [0.0; 3],
        region_max: [0.0; 3],
        cluster_count: 0,
        cluster_radius: 0.0,
        footprint_radius: 0.0,
        avoid_overlaps: false,
        max_slope_deg: 45.0,
        avoid_water: false,
        in_water: false,
        near_water: false,
        near_water_radius: DEFAULT_NEAR_WATER_RADIUS,
        avoid_road: false,
        align_to_terrain: true,
        scale_min: 1.0,
        scale_max: 1.0,
        scale_axis_min: 1.0,
        scale_axis_max: 1.0,
        random_yaw: false,
        max_distance: 0.0,
        template_urls: Vec::new(),
    };
    collect_template_urls(node, &mut spec.template_urls);
    if spec.template_urls.is_empty() {
        ctx.warnings
            .push(format!("{ctx_tag}: no template glTF url found — skipped"));
        return Ok(EntitySpec {
            name: common.name,
            tag: common.tag,
            script: common.script,
            transform: common.transform,
            physics: common.physics,
            kind: if dynamic {
                EntityKind::DynamicSpawner { spec }
            } else {
                EntityKind::StaticSpawner { spec }
            },
            children: Vec::new(),
        });
    }
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "seed" => spec.seed = values::parse_f32(&value, &kctx)? as u64,
            "count" => spec.count = values::parse_f32(&value, &kctx)? as u32,
            "region-min" => spec.region_min = values::parse_vec3(&value, &kctx)?,
            "region-max" => spec.region_max = values::parse_vec3(&value, &kctx)?,
            "cluster-count" => spec.cluster_count = values::parse_f32(&value, &kctx)? as u32,
            "cluster-radius" => spec.cluster_radius = values::parse_f32(&value, &kctx)?,
            "footprint-radius" => spec.footprint_radius = values::parse_f32(&value, &kctx)?,
            "avoid-overlaps" => spec.avoid_overlaps = values::parse_bool(&value, &kctx)?,
            "max-slope-deg" => spec.max_slope_deg = values::parse_f32(&value, &kctx)?,
            "avoid-water" => spec.avoid_water = values::parse_bool(&value, &kctx)?,
            "in-water" => spec.in_water = values::parse_bool(&value, &kctx)?,
            "near-water" => spec.near_water = values::parse_bool(&value, &kctx)?,
            "near-water-radius" => spec.near_water_radius = values::parse_f32(&value, &kctx)?,
            "avoid-road" => spec.avoid_road = values::parse_bool(&value, &kctx)?,
            "align-to-terrain" => spec.align_to_terrain = values::parse_bool(&value, &kctx)?,
            "scale-min" => spec.scale_min = values::parse_f32(&value, &kctx)?,
            "scale-max" => spec.scale_max = values::parse_f32(&value, &kctx)?,
            "scale-axis-min" => spec.scale_axis_min = values::parse_f32(&value, &kctx)?,
            "scale-axis-max" => spec.scale_axis_max = values::parse_f32(&value, &kctx)?,
            "random-yaw" => spec.random_yaw = values::parse_bool(&value, &kctx)?,
            "max-distance" => spec.max_distance = values::parse_f32(&value, &kctx)?,
            // accepted no-ops: profile metadata / placement details ported later
            "profile" | "variation" | "ground-align" | "max-slope-attempts" | "pick-strategy"
            | "base-y-offset" => {}
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: if dynamic {
            EntityKind::DynamicSpawner { spec }
        } else {
            EntityKind::StaticSpawner { spec }
        },
        children: Vec::new(),
    })
}

fn finish_particle_system(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut spec = ParticleSpec {
        preset: "fire".to_string(),
        emission_rate: None,
        life: None,
        speed: None,
        size: None,
        color: None,
        shape_radius: None,
        looping: true,
        world_space: false,
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "preset" => spec.preset = value.trim().to_ascii_lowercase(),
            "looping" => spec.looping = values::parse_bool(&value, &kctx)?,
            "world-space" => spec.world_space = values::parse_bool(&value, &kctx)?,
            "particle-emitter" => {
                for (ckey, cvalue) in parse_component_string(&value) {
                    let cctx = format!("{kctx} {ckey}");
                    match ckey.as_str() {
                        "preset" => spec.preset = cvalue.to_ascii_lowercase(),
                        "emission-rate" | "emissionovertime" => {
                            spec.emission_rate = Some(values::parse_f32(&cvalue, &cctx)?)
                        }
                        "start-life-min" => {
                            spec.life = Some((
                                values::parse_f32(&cvalue, &cctx)?,
                                spec.life.map(|l| l.1).unwrap_or(1.0),
                            ));
                        }
                        "start-life-max" => {
                            spec.life = Some((
                                spec.life.map(|l| l.0).unwrap_or(1.0),
                                values::parse_f32(&cvalue, &cctx)?,
                            ));
                        }
                        "start-speed-min" => {
                            spec.speed = Some((
                                values::parse_f32(&cvalue, &cctx)?,
                                spec.speed.map(|s| s.1).unwrap_or(1.0),
                            ));
                        }
                        "start-speed-max" => {
                            spec.speed = Some((
                                spec.speed.map(|s| s.0).unwrap_or(1.0),
                                values::parse_f32(&cvalue, &cctx)?,
                            ));
                        }
                        "start-size-min" => {
                            spec.size = Some((
                                values::parse_f32(&cvalue, &cctx)?,
                                spec.size.map(|s| s.1).unwrap_or(1.0),
                            ));
                        }
                        "start-size-max" => {
                            spec.size = Some((
                                spec.size.map(|s| s.0).unwrap_or(1.0),
                                values::parse_f32(&cvalue, &cctx)?,
                            ));
                        }
                        "start-color" => spec.color = Some(values::parse_color(&cvalue, &cctx)?),
                        // The emitter shape is a disc/sphere of this radius;
                        // the campfire and torches use it to spread the flame
                        // across the pit instead of a single point jet.
                        "shape-radius" => {
                            spec.shape_radius = Some(values::parse_f32(&cvalue, &cctx)?);
                        }
                        "looping" => spec.looping = values::parse_bool(&cvalue, &cctx)?,
                        "world-space" => spec.world_space = values::parse_bool(&cvalue, &cctx)?,
                        other => ctx
                            .warnings
                            .push(format!("{kctx}: ignored emitter prop `{other}`")),
                    }
                }
            }
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::ParticleSystem { spec },
        children: Vec::new(),
    })
}

/// `<SpawnExclusion at="x z" radius="N">` — global no-spawn circle.
fn finish_spawn_exclusion(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut center = [0.0_f32, 0.0];
    let mut radius = 0.0_f32;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "at" => {
                let v = values::parse_vec2(&value, &kctx)?;
                center = [v[0], v[1]];
            }
            "radius" => radius = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::SpawnExclusion { center, radius },
        children: Vec::new(),
    })
}

/// `<Vegetation meshes="… …" density-per-km2="…">` — dense foliage.
fn finish_vegetation(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut spec = VegetationSpec {
        meshes: Vec::new(),
        density_per_km2: 0.0,
        seed: 0,
        region_min: [0.0; 3],
        region_max: [0.0; 3],
        scale_min: 1.0,
        scale_max: 1.0,
        scale_axis_min: 0.9,
        scale_axis_max: 1.1,
        max_slope_deg: 26.0,
        avoid_water: true,
        // VibeGame vegetation is randomly oriented and non-overlapping by
        // default; a stand of clones all facing the same way is the tell.
        avoid_road: false,
        avoid_overlaps: true,
        random_yaw: true,
        max_distance: 0.0,
        cluster_count: 0,
        cluster_radius: 0.0,
        max_instances: 800,
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "meshes" => {
                spec.meshes = value.split_whitespace().map(str::to_string).collect();
            }
            "density-per-km2" => spec.density_per_km2 = values::parse_f32(&value, &kctx)?,
            "seed" => spec.seed = values::parse_f32(&value, &kctx)? as u64,
            "region-min" => spec.region_min = values::parse_vec3(&value, &kctx)?,
            "region-max" => spec.region_max = values::parse_vec3(&value, &kctx)?,
            "scale-min" => spec.scale_min = values::parse_f32(&value, &kctx)?,
            "scale-max" => spec.scale_max = values::parse_f32(&value, &kctx)?,
            "scale-axis-min" => spec.scale_axis_min = values::parse_f32(&value, &kctx)?,
            "scale-axis-max" => spec.scale_axis_max = values::parse_f32(&value, &kctx)?,
            "max-slope-deg" => spec.max_slope_deg = values::parse_f32(&value, &kctx)?,
            "avoid-water" => spec.avoid_water = values::parse_bool(&value, &kctx)?,
            "avoid-road" => spec.avoid_road = values::parse_bool(&value, &kctx)?,
            "avoid-overlaps" => spec.avoid_overlaps = values::parse_bool(&value, &kctx)?,
            "random-yaw" => spec.random_yaw = values::parse_bool(&value, &kctx)?,
            "max-distance" => spec.max_distance = values::parse_f32(&value, &kctx)?,
            "cluster-count" => spec.cluster_count = values::parse_f32(&value, &kctx)? as u32,
            "cluster-radius" => spec.cluster_radius = values::parse_f32(&value, &kctx)?,
            "max-instances" => spec.max_instances = values::parse_f32(&value, &kctx)? as u32,
            // accepted no-ops: VibeGame instancing/appearance details
            "smart"
            | "wind"
            | "flower-near-radius"
            | "flower-density-ratio"
            | "plant-density-ratio"
            | "variation"
            | "profile"
            | "ground-align" => {}
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if spec.meshes.is_empty() {
        ctx.warnings
            .push(format!("{ctx_tag}: no meshes listed — skipped"));
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::Vegetation { spec },
        children: Vec::new(),
    })
}

fn finish_player_gltf(node: &XmlNode, url: String, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_ignored(
        node,
        rest.into_iter()
            .filter(|(key, _)| key != "model-url")
            .collect(),
        ctx,
    );
    Ok(EntitySpec {
        name: common.name.or_else(|| Some("player".to_string())),
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::PlayerGltf { url },
        children: Vec::new(),
    })
}

fn finish_dialogue_npc(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let dialogue_id = node
        .attr("dialogue-id")
        .map(str::trim)
        .expect("caller checked dialogue-id")
        .to_string();
    let mut marker_height = 2.5;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            // already consumed from `node.attr` above
            "dialogue-id" => {}
            "marker-height" => marker_height = values::parse_f32(&value, &kctx)?,
            // accepted no-ops (visual/audio dialogue UI lands with the HUD phase)
            "portrait-url" | "voice-sfx" => {}
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::DialogueNpc {
            dialogue_id,
            marker_height,
        },
        children: Vec::new(),
    })
}

fn finish_resource_chip(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut resource = String::new();
    let mut icon = String::new();
    let mut target = "player".to_string();
    for (key, value) in rest {
        match key.as_str() {
            "resource" => resource = value.trim().to_string(),
            "icon" => icon = value.trim().to_string(),
            "target-entity" => target = value.trim().to_string(),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if resource.is_empty() {
        ctx.warnings
            .push(format!("{ctx_tag}: missing resource — skipped"));
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::ResourceChip {
            resource,
            icon,
            target,
        },
        children: Vec::new(),
    })
}

/// HUD elements keep their raw attributes — `src/hud.rs` interprets them.
fn finish_audio_mixer(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut master = 1.0;
    let mut music = 1.0;
    let mut sfx = 1.0;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "master" => master = values::parse_f32(&value, &kctx)?,
            "music" => music = values::parse_f32(&value, &kctx)?,
            "sfx" => sfx = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::AudioMixer { master, music, sfx },
        children: Vec::new(),
    })
}

fn finish_music_layer(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let layer = node
        .attr("layer")
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let sound = node.attr("sound").map(str::trim).unwrap_or("");
    let mut base_volume = 0.2;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "base-volume" | "volume" => base_volume = values::parse_f32(&value, &kctx)?,
            // consumed via node.attr above
            "layer" | "sound" => {}
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    let Some(layer) = layer else {
        ctx.warnings
            .push(format!("{ctx_tag}: missing layer — skipped"));
        return Ok(EntitySpec {
            name: common.name,
            tag: common.tag,
            script: common.script,
            transform: common.transform,
            physics: common.physics,
            kind: EntityKind::MusicLayer {
                layer: String::new(),
                base_volume,
            },
            children: Vec::new(),
        });
    };
    let _ = sound;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::MusicLayer { layer, base_volume },
        children: Vec::new(),
    })
}

fn finish_daycycle(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut c = DayCycleConfig {
        minute_of_day: 480.0,
        minutes_per_real_second: 1.2,
        dawn_minute: 330.0,
        dusk_minute: 1170.0,
        ambient_day: 0.26,
        ambient_night: 0.07,
        drive_ambient: true,
        max_sun_elevation: 62.0,
        sun_azimuth_base: 205.0,
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "minute-of-day" => c.minute_of_day = values::parse_f32(&value, &kctx)?,
            "minutes-per-real-second" => {
                c.minutes_per_real_second = values::parse_f32(&value, &kctx)?
            }
            "dawn-minute" => c.dawn_minute = values::parse_f32(&value, &kctx)?,
            "dusk-minute" => c.dusk_minute = values::parse_f32(&value, &kctx)?,
            "ambient-day-intensity" => c.ambient_day = values::parse_f32(&value, &kctx)?,
            "ambient-night-intensity" => c.ambient_night = values::parse_f32(&value, &kctx)?,
            "drive-ambient" => c.drive_ambient = values::parse_bool(&value, &kctx)?,
            "max-sun-elevation" => c.max_sun_elevation = values::parse_f32(&value, &kctx)?,
            "sun-azimuth-base" => c.sun_azimuth_base = values::parse_f32(&value, &kctx)?,
            _ => {}
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::DayCycle {
            minute_of_day: c.minute_of_day,
            minutes_per_real_second: c.minutes_per_real_second,
            dawn_minute: c.dawn_minute,
            dusk_minute: c.dusk_minute,
            ambient_day: c.ambient_day,
            ambient_night: c.ambient_night,
            drive_ambient: c.drive_ambient,
            max_sun_elevation: c.max_sun_elevation,
            sun_azimuth_base: c.sun_azimuth_base,
        },
        children: Vec::new(),
    })
}

fn finish_weather(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut w = WeatherConfig {
        wind: [0.7, 0.25],
        wind_strength: 1.5,
        clouds: 0.25,
        rain: 0.0,
        cycle: true,
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "wind" => {
                let v = values::parse_vec2(&value, &kctx)?;
                w.wind = v;
            }
            "wind-strength" => w.wind_strength = values::parse_f32(&value, &kctx)?,
            "clouds" => w.clouds = values::parse_f32(&value, &kctx)?,
            "rain" => w.rain = values::parse_f32(&value, &kctx)?,
            "cycle" => w.cycle = values::parse_bool(&value, &kctx)?,
            _ => {}
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::Weather {
            wind: w.wind,
            wind_strength: w.wind_strength,
            clouds: w.clouds,
            rain: w.rain,
            cycle: w.cycle,
        },
        children: Vec::new(),
    })
}

/// Parse a polygon attribute: `"[-56,56;56,56;…]"` → Vec<[f32;2]>.
pub fn parse_polygon(value: &str) -> Vec<[f32; 2]> {
    value
        .trim_start_matches('[')
        .trim_end_matches(']')
        .split(';')
        .filter_map(|pair| {
            let (x, y) = pair.trim().split_once(',')?;
            Some([x.trim().parse::<f32>().ok()?, y.trim().parse::<f32>().ok()?])
        })
        .collect()
}

fn finish_biome_region(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut id = String::new();
    let mut polygon = Vec::new();
    let mut fog_density = 0.0;
    let mut tint = None;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "id" => id = value.trim().to_string(),
            "polygon" => polygon = parse_polygon(&value),
            "fog-density" => fog_density = values::parse_f32(&value, &kctx)?,
            "tint" => tint = Some(values::parse_color(&value, &kctx)?),
            _ => {}
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::BiomeRegion {
            id,
            polygon,
            fog_density,
            tint,
        },
        children: Vec::new(),
    })
}

fn finish_world_border(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut radius = 3800.0;
    let mut warn_seconds = 5.0;
    let mut margin = 80.0;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "radius" => radius = values::parse_f32(&value, &kctx)?,
            "warn-seconds" => warn_seconds = values::parse_f32(&value, &kctx)?,
            "margin" => margin = values::parse_f32(&value, &kctx)?,
            _ => {}
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::WorldBorder {
            radius,
            warn_seconds,
            margin,
        },
        children: Vec::new(),
    })
}

/// Generic engine-config element: tag + raw attrs preserved as data
/// (Sky, NavMesh, SpawnGate, ProjectileTemplate, PostFxDebugToggle,
/// AdaptiveQuality).
fn finish_engine_config(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, _rest) = parse_common(node, ctx)?;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::EngineConfig {
            tag: node.tag.to_ascii_lowercase(),
            attrs: node
                .attrs
                .iter()
                .map(|(k, v)| (k.to_ascii_lowercase(), v.clone()))
                .collect(),
        },
        children: Vec::new(),
    })
}

/// `<Sky>` — procedural sky parameters kept as raw attrs; `src/hud.rs`
/// (`spawn_hud`) builds the shader dome from them.
fn finish_sky(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let _ = rest;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::HudElement {
            tag: "sky".to_string(),
            attrs: node
                .attrs
                .iter()
                .map(|(k, v)| (k.to_ascii_lowercase(), v.clone()))
                .collect(),
        },
        children: Vec::new(),
    })
}

fn finish_hud_element(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, _rest) = parse_common(node, ctx)?;
    // Raw attrs are the data — hud.rs interprets them, nothing to warn about.
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::HudElement {
            tag: node.tag.to_ascii_lowercase(),
            attrs: node
                .attrs
                .iter()
                .map(|(k, v)| (k.to_ascii_lowercase(), v.clone()))
                .collect(),
        },
        children: Vec::new(),
    })
}

fn finish_gltf_scene(node: &XmlNode, url: String, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    // `url` is this tag's own attribute — already consumed by the caller.
    warn_ignored(
        node,
        rest.into_iter().filter(|(key, _)| key != "url").collect(),
        ctx,
    );
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::GltfScene { url },
        children: parse_entities(&node.children, ctx)?,
    })
}

/// Hierarchy offset for world-anchored terrain features: groups are
/// transform-only containers, so a nested `<TerrainPad translation="…">`
/// inherits the accumulated XZ translation (rotation/scale are ignored for
/// ground features and warn when non-default).
fn terrain_offset(common: &Common, node: &XmlNode, ctx: &mut ParseCtx) -> Vec2 {
    let t = &common.transform;
    if t.scale != [1.0, 1.0, 1.0] || t.euler_deg.is_some() || t.rotation_quat.is_some() {
        ctx.warnings.push(format!(
            "<{}>: rotation/scale do not apply to ground features — only the XZ translation is inherited",
            node.tag
        ));
    }
    Vec2::new(t.translation[0], t.translation[2])
}

fn offset_point(p: [f32; 2], off: Vec2) -> Vec2 {
    Vec2::new(p[0], p[1]) + off
}

fn offset_path(points: Vec<[f32; 2]>, off: Vec2) -> Vec<Vec2> {
    points
        .into_iter()
        .map(|p| Vec2::new(p[0], p[1]) + off)
        .collect()
}

/// Ground features must stay leaf elements: their children would never be
/// spawned (the feature consumes the element), so any child is a mistake.
fn warn_children(node: &XmlNode, ctx: &mut ParseCtx) {
    if !node.children.is_empty() {
        ctx.warnings.push(format!(
            "<{}>: children are ignored (ground features are leaf elements)",
            node.tag
        ));
    }
}

fn finish_terrain(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let mut spec = TerrainSpec::default();
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "heightmap" => spec.heightmap = Some(value.trim().to_string()),
            "world-size" => {
                spec.world_size = values::parse_f32(&value, &kctx)?;
                if spec.world_size <= 0.0 {
                    bail!("{kctx}: must be positive");
                }
            }
            "max-height" => {
                spec.max_height = values::parse_f32(&value, &kctx)?;
                if spec.max_height <= 0.0 {
                    bail!("{kctx}: must be positive");
                }
            }
            "chunk-size" => {
                spec.chunk_size = values::parse_f32(&value, &kctx)?;
                if spec.chunk_size <= 0.0 {
                    bail!("{kctx}: must be positive");
                }
            }
            "levels" => spec.levels = values::parse_u32(&value, &kctx)?.clamp(1, 8) as u8,
            "lod-distance-ratio" => {
                spec.lod_distance_ratio = values::parse_f32(&value, &kctx)?;
            }
            "lod-hysteresis" => spec.lod_hysteresis = values::parse_f32(&value, &kctx)?,
            "render-distance" => spec.render_distance = Some(values::parse_f32(&value, &kctx)?),
            "skirt-width" => spec.skirt_width = values::parse_f32(&value, &kctx)?,
            "skirt-depth" => spec.skirt_depth = values::parse_f32(&value, &kctx)?,
            "height-smoothing" => spec.height_smoothing = values::parse_f32(&value, &kctx)?,
            "collision-resolution" => {
                spec.collision_resolution = values::parse_u32(&value, &kctx)?;
            }
            "resolution" => spec.resolution = values::parse_u32(&value, &kctx)?.max(1),
            "texture" | "texture-url" => spec.texture = Some(value.trim().to_string()),
            "texture-tile-size" => spec.texture_tile_size = values::parse_f32(&value, &kctx)?,
            "seed" => spec.seed = values::parse_u64(&value, &kctx)?,
            "base-color" => spec.tint.base_color = tint_color(&value, &kctx)?,
            "color-low" => spec.tint.color_low = tint_color(&value, &kctx)?,
            "color-mid" => spec.tint.color_mid = tint_color(&value, &kctx)?,
            "color-high" => spec.tint.color_high = tint_color(&value, &kctx)?,
            "color-rock" => spec.tint.color_rock = tint_color(&value, &kctx)?,
            "snow-height" => spec.tint.snow_height = values::parse_f32(&value, &kctx)?,
            "slope-threshold" => spec.tint.slope_threshold = values::parse_f32(&value, &kctx)?,
            "slope-softness" => spec.tint.slope_softness = values::parse_f32(&value, &kctx)?,
            "height-blend-strength" => {
                spec.tint.height_blend_strength = values::parse_f32(&value, &kctx)?;
            }
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::Terrain { spec },
        children: Vec::new(),
    })
}

fn tint_color(value: &str, ctx: &str) -> Result<bevy::color::Color> {
    let [r, g, b] = values::parse_color(value, ctx)?;
    Ok(bevy::color::Color::srgb(r, g, b))
}

fn finish_terrain_pad(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = TerrainPadSpec::default();
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "at" => spec.at = offset_point(values::parse_vec2(&value, &kctx)?, off),
            "size" => spec.size = Vec2::from(values::parse_vec2(&value, &kctx)?),
            "falloff" => spec.falloff = values::parse_f32(&value, &kctx)?,
            "corner-radius" => spec.corner_radius = values::parse_f32(&value, &kctx)?,
            "height" => spec.height = Some(values::parse_f32(&value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::TerrainPad { spec },
        children: Vec::new(),
    })
}

fn finish_lake(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = LakeSpec::default();
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "at" => spec.at = offset_point(values::parse_vec2(&value, &kctx)?, off),
            "radius" => spec.radius = values::parse_f32(&value, &kctx)?,
            "depth" => spec.depth = values::parse_f32(&value, &kctx)?,
            "water-offset" => spec.water_offset = values::parse_f32(&value, &kctx)?,
            "color" => spec.color = values::parse_color(&value, &kctx)?,
            "opacity" => spec.opacity = values::parse_f32(&value, &kctx)?,
            "ripple" => spec.ripple = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::Lake { spec },
        children: Vec::new(),
    })
}

fn finish_river(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = RiverSpec::default();
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "path" => {
                spec.path = offset_path(values::parse_vec2_list(&value, &kctx)?, off);
                if spec.path.len() < 2 {
                    bail!("{kctx}: a river needs at least 2 points (x z pairs)");
                }
            }
            "width" => spec.width = values::parse_f32(&value, &kctx)?,
            "depth" => spec.depth = values::parse_f32(&value, &kctx)?,
            "water-offset" => spec.water_offset = values::parse_f32(&value, &kctx)?,
            "bank-width" => spec.bank_width = values::parse_f32(&value, &kctx)?,
            "bank-height" => spec.bank_height = values::parse_f32(&value, &kctx)?,
            "color" => spec.color = values::parse_color(&value, &kctx)?,
            "opacity" => spec.opacity = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::River { spec },
        children: Vec::new(),
    })
}

/// Attributes shared by `<Road>` and `<RoadNetwork>` (the network carries the
/// defaults; standalone roads their own values). Returns `true` when the key
/// was consumed.
#[allow(clippy::too_many_arguments)]
fn parse_road_attrs(
    key: &str,
    value: &str,
    kctx: &str,
    profile: &mut Option<RoadProfile>,
    flatten: &mut Option<bool>,
    falloff: &mut Option<f32>,
    window: &mut Option<f32>,
    max_grade: &mut Option<f32>,
    texture: &mut Option<String>,
    texture_scale: &mut Option<f32>,
) -> Result<bool> {
    match key {
        "profile" | "default-profile" => {
            *profile = Some(RoadProfile::parse(value).ok_or_else(|| {
                anyhow::anyhow!("{kctx}: unknown profile `{value}` (artery|spur|plaza|bridge)")
            })?);
        }
        "flatten" => *flatten = Some(values::parse_bool(value, kctx)?),
        "flatten-falloff" => *falloff = Some(values::parse_f32(value, kctx)?),
        "flatten-window" => *window = Some(values::parse_f32(value, kctx)?),
        "flatten-max-grade" => *max_grade = Some(values::parse_f32(value, kctx)?),
        "texture-url" | "texture" => *texture = Some(value.trim().to_string()),
        "texture-scale" => *texture_scale = Some(values::parse_f32(value, kctx)?),
        _ => return Ok(false),
    }
    Ok(true)
}

fn finish_road(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = RoadSpec {
        name: common.name.clone(),
        ..RoadSpec::default()
    };
    let mut profile: Option<RoadProfile> = None;
    let mut flatten: Option<bool> = None;
    let mut falloff: Option<f32> = None;
    let mut window: Option<f32> = None;
    let mut max_grade: Option<f32> = None;
    let mut texture_scale: Option<f32> = None;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        let handled = parse_road_attrs(
            &key,
            &value,
            &kctx,
            &mut profile,
            &mut flatten,
            &mut falloff,
            &mut window,
            &mut max_grade,
            &mut spec.texture,
            &mut texture_scale,
        )?;
        if handled {
            continue;
        }
        match key.as_str() {
            "path" => {
                spec.path = offset_path(values::parse_vec2_list(&value, &kctx)?, off);
                if spec.path.len() < 2 {
                    bail!("{kctx}: a road needs at least 2 points (x z pairs)");
                }
            }
            "width" => spec.width = values::parse_f32(&value, &kctx)?,
            "flatten-shoulder" => spec.flatten_shoulder = values::parse_f32(&value, &kctx)?,
            "platform-sink" => spec.platform_sink = values::parse_f32(&value, &kctx)?,
            "smoothing" => spec.smoothing = values::parse_u32(&value, &kctx)?.min(6),
            "closed" => spec.closed = values::parse_bool(&value, &kctx)?,
            "edge-feather" => spec.edge_feather = values::parse_f32(&value, &kctx)?.clamp(0.0, 1.0),
            // Parsed-for-compat visuals (decal trails) — no native effect yet.
            "edge-noise" | "end-feather-start" | "end-feather-end" | "normal-map-url" => ctx
                .warnings
                .push(format!("{kctx}: accepted, no native effect yet")),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if let Some(p) = profile {
        spec.profile = p;
    }
    if let Some(f) = flatten {
        spec.flatten = f;
    }
    if let Some(f) = falloff {
        spec.flatten_falloff = f;
    }
    if let Some(w) = window {
        spec.flatten_window = w;
    }
    if let Some(g) = max_grade {
        spec.flatten_max_grade = g;
    }
    if let Some(s) = texture_scale {
        spec.texture_scale = s;
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::Road { spec },
        children: Vec::new(),
    })
}

fn finish_road_network(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = RoadNetworkSpec {
        name: common.name.clone(),
        ..RoadNetworkSpec::default()
    };
    let mut profile: Option<RoadProfile> = None;
    let mut flatten: Option<bool> = None;
    let mut falloff: Option<f32> = None;
    let mut window: Option<f32> = None;
    let mut max_grade: Option<f32> = None;
    let mut texture_scale: Option<f32> = None;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        let handled = parse_road_attrs(
            &key,
            &value,
            &kctx,
            &mut profile,
            &mut flatten,
            &mut falloff,
            &mut window,
            &mut max_grade,
            &mut spec.texture,
            &mut texture_scale,
        )?;
        if handled {
            continue;
        }
        match key.as_str() {
            "default-width" => spec.default_width = values::parse_f32(&value, &kctx)?,
            "crossing-flare" => spec.crossing_flare = values::parse_bool(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if let Some(p) = profile {
        spec.default_profile = p;
    }
    if let Some(f) = flatten {
        spec.flatten = f;
    }
    if let Some(f) = falloff {
        spec.flatten_falloff = f;
    }
    if let Some(w) = window {
        spec.flatten_window = w;
    }
    if let Some(g) = max_grade {
        spec.flatten_max_grade = g;
    }
    if let Some(s) = texture_scale {
        spec.texture_scale = s;
    }
    // Children: `<Way>` + `<Segment>` (case-insensitive); anything else is a
    // parse error — networks have exactly two child kinds.
    for child in &node.children {
        let lower = child.tag.to_ascii_lowercase();
        match lower.as_str() {
            "way" => {
                let mut way = WaySpec {
                    id: String::new(),
                    at: Vec2::ZERO,
                    width: None,
                };
                for (key, value) in &child.attrs {
                    let kctx = format!("<{} {key}>", child.tag);
                    match key.as_str() {
                        "id" => way.id = value.trim().to_string(),
                        "xz" => way.at = offset_point(values::parse_vec2(value, &kctx)?, off),
                        "width" => way.width = Some(values::parse_f32(value, &kctx)?),
                        other => ctx
                            .warnings
                            .push(format!("<{}>: ignored attribute `{other}`", child.tag)),
                    }
                }
                if way.id.is_empty() {
                    bail!("<{}>: way needs an `id`", child.tag);
                }
                spec.ways.push(way);
            }
            "segment" => {
                let mut seg = SegmentSpec {
                    a: String::new(),
                    b: String::new(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                };
                for (key, value) in &child.attrs {
                    let kctx = format!("<{} {key}>", child.tag);
                    match key.as_str() {
                        "a" => seg.a = value.trim().to_string(),
                        "b" => seg.b = value.trim().to_string(),
                        "via" => seg.via = offset_path(values::parse_vec2_list(value, &kctx)?, off),
                        "width" => seg.width = Some(values::parse_f32(value, &kctx)?),
                        "profile" => {
                            seg.profile = Some(RoadProfile::parse(value).ok_or_else(|| {
                                anyhow::anyhow!(
                                    "{kctx}: unknown profile `{value}` (artery|spur|plaza|bridge)"
                                )
                            })?)
                        }
                        // Bridge GLB decks need glTF (Phase 1 handoff).
                        "bridge-url"
                        | "bridge-collision-url"
                        | "bridge-lod1-url"
                        | "bridge-lod2-url"
                        | "bridge-native-span" => ctx.warnings.push(format!(
                            "<{}>: `{key}` accepted, bridge decks need glTF (not yet native)",
                            child.tag
                        )),
                        other => ctx
                            .warnings
                            .push(format!("<{}>: ignored attribute `{other}`", child.tag)),
                    }
                }
                if seg.a.is_empty() || seg.b.is_empty() {
                    bail!("<{}>: segment needs `a` and `b` way ids", child.tag);
                }
                spec.segments.push(seg);
            }
            _other => bail!(
                "<{}>: unknown element inside <{}> — only <Way> and <Segment> are allowed",
                child.tag,
                node.tag
            ),
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        kind: EntityKind::RoadNetwork { spec },
        children: Vec::new(),
    })
}

/// Headless summary used by `viber analyze` and tests.
#[derive(Debug, Default, PartialEq)]
pub struct WorldSummary {
    pub groups: usize,
    pub primitives: usize,
    pub point_lights: usize,
    pub directional_lights: usize,
    pub cameras: usize,
    pub has_ambient: bool,
    /// Declarative terrain features (consumed by the terrain runtime).
    pub terrain: usize,
    pub terrain_pads: usize,
    pub lakes: usize,
    pub rivers: usize,
    pub roads: usize,
    pub road_networks: usize,
    /// glTF scenes referenced (spawned async at runtime).
    pub gltf_scenes: usize,
    /// `<StaticSpawner>` groups (expanded into instances at runtime).
    pub static_spawners: usize,
    /// `<ParticleSystem>` emitters.
    pub particle_systems: usize,
    /// `<DynamicSpawner>` groups (creature spawns).
    pub dynamic_spawners: usize,
    /// `<Vegetation>` groups (converted into spawner groups).
    pub vegetation: usize,
    /// `<SpawnExclusion>` no-spawn circles (global constraint).
    pub spawn_exclusions: usize,
    /// `<PlayerGLTF>` heroes (controllable).
    pub players: usize,
    /// `<DialogueNPC>` dialogue targets.
    pub dialogue_npcs: usize,
    /// `<ResourceChip>` HUD chips.
    pub resource_chips: usize,
    /// HUD screen elements (bars, minimap, compass, modal…).
    pub hud_elements: usize,
    /// `<AudioMixer>` buses (world resource).
    pub audio_mixer: usize,
    /// `<MusicLayer>` BGM layers.
    pub music_layers: usize,
    /// Engine config / world-rule elements (DayCycle, Weather, border…).
    pub world_systems: usize,
}

impl WorldSummary {
    /// Total spawned entities (ambient lights are resources, not entities).
    pub fn entities(&self) -> usize {
        self.groups
            + self.primitives
            + self.point_lights
            + self.directional_lights
            + self.cameras
            + self.gltf_scenes
            + self.particle_systems
            + self.players
    }

    /// Total ground-feature elements (terrain element excluded — it is the
    /// heightfield itself, counted in `terrain`).
    pub fn ground_features(&self) -> usize {
        self.terrain_pads + self.lakes + self.rivers + self.roads + self.road_networks
    }
}

/// Keep the first camera in the tree; deeper cameras become plain groups
/// (two `Camera3d` entities would double-render and fight over the window).
fn demote_extra_cameras(specs: Vec<EntitySpec>, warnings: &mut Vec<String>) -> Vec<EntitySpec> {
    fn has_target(spec: &EntitySpec) -> bool {
        matches!(
            &spec.kind,
            EntityKind::OrbitCamera {
                target: Some(_),
                ..
            }
        )
    }
    fn find_targeted(specs: &[EntitySpec]) -> bool {
        specs
            .iter()
            .any(|s| has_target(s) || find_targeted(&s.children))
    }
    // Prefer a camera with an explicit target (e.g. ThirdPersonCamera →
    // player): when one exists, every other camera is demoted.
    let prefer_target = find_targeted(&specs);
    let mut seen_camera = false;
    let mut demoted = 0usize;
    fn walk(specs: &mut [EntitySpec], seen: &mut bool, demoted: &mut usize, prefer_target: bool) {
        for spec in specs.iter_mut() {
            if matches!(spec.kind, EntityKind::OrbitCamera { .. }) {
                if *seen || (prefer_target && !has_target(spec)) {
                    spec.kind = EntityKind::Group;
                    *demoted += 1;
                } else {
                    *seen = true;
                }
            }
            walk(&mut spec.children, seen, demoted, prefer_target);
        }
    }
    let mut specs = specs;
    walk(&mut specs, &mut seen_camera, &mut demoted, prefer_target);
    if demoted > 0 {
        warnings.push(format!(
            "{demoted} extra camera(s) demoted to groups — only the first one renders"
        ));
    }
    specs
}

/// Walk the entity tree and count each kind.
pub fn summarize(world: &ParsedWorld) -> WorldSummary {
    fn walk(specs: &[EntitySpec], out: &mut WorldSummary) {
        for spec in specs {
            match &spec.kind {
                EntityKind::Group => out.groups += 1,
                EntityKind::Primitive { .. } => out.primitives += 1,
                EntityKind::PointLight { .. } => out.point_lights += 1,
                EntityKind::DirectionalLight { .. } => out.directional_lights += 1,
                EntityKind::AmbientLight { .. } => out.has_ambient = true,
                EntityKind::OrbitCamera { .. } => out.cameras += 1,
                EntityKind::Terrain { .. } => out.terrain += 1,
                EntityKind::TerrainPad { .. } => out.terrain_pads += 1,
                EntityKind::Lake { .. } => out.lakes += 1,
                EntityKind::River { .. } => out.rivers += 1,
                EntityKind::Road { .. } => out.roads += 1,
                EntityKind::RoadNetwork { .. } => out.road_networks += 1,
                EntityKind::GltfScene { .. } => out.gltf_scenes += 1,
                EntityKind::StaticSpawner { .. } => out.static_spawners += 1,
                EntityKind::ParticleSystem { .. } => out.particle_systems += 1,
                EntityKind::DynamicSpawner { .. } => out.dynamic_spawners += 1,
                EntityKind::SpawnExclusion { .. } => out.spawn_exclusions += 1,
                EntityKind::Vegetation { .. } => out.vegetation += 1,
                EntityKind::PlayerGltf { .. } => out.players += 1,
                EntityKind::DialogueNpc { .. } => out.dialogue_npcs += 1,
                EntityKind::ResourceChip { .. } => out.resource_chips += 1,
                EntityKind::HudElement { .. } => out.hud_elements += 1,
                EntityKind::AudioMixer { .. } => out.audio_mixer += 1,
                EntityKind::MusicLayer { .. } => out.music_layers += 1,
                EntityKind::DayCycle { .. }
                | EntityKind::Weather { .. }
                | EntityKind::BiomeRegion { .. }
                | EntityKind::WorldBorder { .. }
                | EntityKind::EngineConfig { .. } => out.world_systems += 1,
            }
            walk(&spec.children, out);
        }
    }
    let mut out = WorldSummary::default();
    walk(&world.entities, &mut out);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(tag: &str, attrs: &[(&str, &str)]) -> XmlNode {
        XmlNode {
            tag: tag.to_string(),
            attrs: attrs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            children: vec![],
        }
    }

    fn parse_one(n: &XmlNode) -> Result<(EntitySpec, Vec<String>)> {
        let mut ctx = ParseCtx::default();
        let spec =
            parse_entity(n, &mut ctx)?.ok_or_else(|| anyhow::anyhow!("element was skipped"))?;
        Ok((spec, ctx.warnings))
    }

    #[test]
    fn test_entity_defaults() {
        let (spec, w) = parse_one(&node("Entity", &[])).unwrap();
        assert!(w.is_empty());
        assert!(matches!(spec.kind, EntityKind::Group));
        assert_eq!(spec.transform.scale, [1.0; 3]);
        assert_eq!(spec.transform.translation, [0.0; 3]);
    }

    #[test]
    fn test_group_tag_is_alias_of_entity() {
        let (a, _) = parse_one(&node("group", &[])).unwrap();
        let (b, _) = parse_one(&node("Entity", &[])).unwrap();
        assert!(matches!(a.kind, EntityKind::Group));
        assert!(matches!(b.kind, EntityKind::Group));
    }

    #[test]
    fn test_tag_matching_is_case_insensitive() {
        let (spec, _) = parse_one(&node("POINTLIGHT", &[])).unwrap();
        assert!(matches!(spec.kind, EntityKind::PointLight { .. }));
    }

    #[test]
    fn test_universal_attributes() {
        let (spec, _) = parse_one(&node(
            "Entity",
            &[("name", "hero"), ("tag", "player"), ("script", "hero.lua")],
        ))
        .unwrap();
        assert_eq!(spec.name.as_deref(), Some("hero"));
        assert_eq!(spec.tag.as_deref(), Some("player"));
        assert_eq!(spec.script.as_deref(), Some("hero.lua"));
    }

    #[test]
    fn test_translation_and_scale() {
        let (spec, _) =
            parse_one(&node("Entity", &[("translation", "1 2 3"), ("scale", "2")])).unwrap();
        assert_eq!(spec.transform.translation, [1.0, 2.0, 3.0]);
        assert_eq!(spec.transform.scale, [2.0; 3]);
    }

    #[test]
    fn test_bad_translation_is_error() {
        assert!(parse_one(&node("Entity", &[("translation", "1 2")])).is_err());
    }

    #[test]
    fn test_pos_is_alias_of_translation() {
        let (spec, w) = parse_one(&node("Entity", &[("pos", "1 2 3")])).unwrap();
        assert!(w.is_empty(), "{w:?}");
        assert_eq!(spec.transform.translation, [1.0, 2.0, 3.0]);
    }

    #[test]
    fn test_unknown_attribute_is_warning() {
        let (spec, w) = parse_one(&node("Entity", &[("pos-x", "0 0 0")])).unwrap();
        assert!(matches!(spec.kind, EntityKind::Group));
        assert_eq!(w, vec!["<Entity>: ignored attribute `pos-x`".to_string()]);
    }

    #[test]
    fn test_unknown_element_is_skipped_as_noop() {
        let mut ctx = ParseCtx::default();
        let spec = parse_entity(&node("GameObject", &[]), &mut ctx).unwrap();
        assert!(spec.is_none());
        assert_eq!(ctx.skipped.get("GameObject"), Some(&1));
    }

    #[test]
    fn test_unknown_subtree_is_dropped_wholesale() {
        let mut parent = node("GLTFLoader", &[("url", "x.glb")]);
        parent.children = vec![node("Entity", &[("name", "inside")])];
        let mut ctx = ParseCtx::default();
        let spec = parse_entity(&parent, &mut ctx).unwrap();
        assert!(spec.is_none());
        // The child is skipped as part of the subtree, not parsed.
        assert_eq!(ctx.skipped.get("GLTFLoader"), Some(&1));
        assert_eq!(ctx.skipped.get("Entity"), None);
    }

    #[test]
    fn test_parse_world_aggregates_skipped_tags() {
        let world = parse_world(
            &[],
            &[
                node("GameObject", &[]),
                node("GameObject", &[]),
                node("NoSuchThing", &[]),
            ],
        )
        .unwrap();
        assert_eq!(world.entities.len(), 0);
        assert_eq!(world.skipped_tags.get("GameObject"), Some(&2));
        assert_eq!(world.skipped_tags.get("NoSuchThing"), Some(&1));
        assert!(
            world.warnings.iter().any(|w| w.contains("<GameObject>×2")),
            "{:?}",
            world.warnings
        );
    }

    #[test]
    fn test_nested_world_and_include_are_errors() {
        assert!(parse_one(&node("world", &[])).is_err());
        assert!(parse_one(&node("Include", &[("src", "x.xml")])).is_err());
    }

    #[test]
    fn test_cuboid_half_size() {
        let (spec, _) = parse_one(&node("Cuboid", &[("half-size", "1 0.5 2")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Cuboid { half_size } if half_size == [1.0, 0.5, 2.0]));
    }

    #[test]
    fn test_cuboid_default_is_unit_cube() {
        let (spec, _) = parse_one(&node("Cuboid", &[])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Cuboid { half_size } if half_size == [0.5; 3]));
    }

    #[test]
    fn test_sphere_radius_broadcasts_single() {
        let (spec, _) = parse_one(&node("Sphere", &[("radius", "0.75")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Sphere { radius } if radius == 0.75));
    }

    #[test]
    fn test_cylinder_attributes() {
        let (spec, _) =
            parse_one(&node("Cylinder", &[("radius", "2"), ("half-height", "1")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        let Shape::Cylinder {
            half_height,
            radius,
        } = shape
        else {
            panic!("expected cylinder");
        };
        assert_eq!(half_height, 1.0);
        assert_eq!(radius, 2.0);
    }

    #[test]
    fn test_plane_half_size_two_components() {
        let (spec, _) = parse_one(&node("Plane", &[("half-size", "10 5")])).unwrap();
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Plane { half_size } if half_size == [10.0, 5.0]));
    }

    #[test]
    fn test_material_attributes() {
        let (spec, _) = parse_one(&node(
            "Cuboid",
            &[
                ("base-color", "#c0764a"),
                ("metallic", "0.8"),
                ("roughness", "0.2"),
            ],
        ))
        .unwrap();
        let EntityKind::Primitive { material, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(material.base_color.is_some());
        assert_eq!(material.metallic, Some(0.8));
        assert_eq!(material.roughness, Some(0.2));
    }

    #[test]
    fn test_shape_specific_attr_mismatch_warns() {
        let (_, w) = parse_one(&node("Sphere", &[("half-height", "1")])).unwrap();
        assert!(w.iter().any(|m| m.contains("does not apply")), "{w:?}");
    }

    #[test]
    fn test_point_light_overrides() {
        let (spec, _) = parse_one(&node(
            "PointLight",
            &[
                ("color", "#ffddaa"),
                ("intensity", "1200"),
                ("radius", "0.2"),
                ("shadows", "true"),
            ],
        ))
        .unwrap();
        let EntityKind::PointLight {
            color,
            intensity,
            radius,
            shadows,
        } = spec.kind
        else {
            panic!("expected point light");
        };
        assert!(color.is_some() && intensity == Some(1200.0));
        assert_eq!(radius, Some(0.2));
        assert_eq!(shadows, Some(true));
    }

    #[test]
    fn test_directional_light_overrides() {
        let (spec, _) = parse_one(&node(
            "DirectionalLight",
            &[
                ("color", "#ffeecc"),
                ("illuminance", "20000"),
                ("direction", "-1 -2 -1"),
                ("shadows", "true"),
            ],
        ))
        .unwrap();
        let EntityKind::DirectionalLight {
            color,
            illuminance,
            direction,
            shadows,
        } = spec.kind
        else {
            panic!("expected directional light");
        };
        assert!(color.is_some() && illuminance == Some(20000.0));
        assert_eq!(direction, [-1.0, -2.0, -1.0]);
        assert_eq!(shadows, Some(true));
    }

    #[test]
    fn test_directional_light_defaults() {
        let (spec, _) = parse_one(&node("DirectionalLight", &[])).unwrap();
        let EntityKind::DirectionalLight {
            illuminance,
            direction,
            ..
        } = spec.kind
        else {
            panic!("expected directional light");
        };
        assert_eq!(illuminance, None);
        assert_eq!(direction, [-1.0, -1.0, -1.0]);
    }

    #[test]
    fn test_gltf_scene_parses_url_and_transform() {
        let (spec, w) = parse_one(&node(
            "GltfScene",
            &[
                ("url", "/assets/meshes/tree.glb"),
                ("translation", "2 0 4"),
                ("scale", "1.5"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::GltfScene { url } = spec.kind else {
            panic!("expected gltf scene");
        };
        assert_eq!(url, "/assets/meshes/tree.glb");
        assert_eq!(spec.transform.translation, [2.0, 0.0, 4.0]);
        assert_eq!(spec.transform.scale, [1.5; 3]);
    }

    #[test]
    fn test_gltf_scene_unknown_attr_warns() {
        let (_, w) = parse_one(&node(
            "GltfScene",
            &[("url", "x.glb"), ("nonsense-attr", "1")],
        ))
        .unwrap();
        assert!(w.iter().any(|m| m.contains("`nonsense-attr`")), "{w:?}");
    }

    #[test]
    fn test_physics_attributes_are_parsed_not_warned() {
        use crate::physics::{BodyKind, ColliderShape};
        let (entity, w) = parse_one(&node(
            "GltfScene",
            &[
                ("url", "x.glb"),
                ("collider", "shape: box; size: 1 2 3"),
                ("rigidbody", "type: fixed; mass: 0; gravity-scale: 0"),
            ],
        ))
        .unwrap();
        assert!(
            !w.iter()
                .any(|m| m.contains("collider") || m.contains("rigidbody")),
            "physics attributes are consumed, not warned: {w:?}"
        );
        assert_eq!(
            entity.physics.collider,
            ColliderShape::Box {
                size: bevy::math::Vec3::new(1.0, 2.0, 3.0),
                offset: bevy::math::Vec3::ZERO,
            }
        );
        assert_eq!(entity.physics.body, BodyKind::Fixed);
        assert_eq!(entity.physics.gravity_scale, Some(0.0));
    }

    #[test]
    fn test_group_body_shorthand_is_parsed() {
        use crate::physics::BodyKind;
        let (entity, _) = parse_one(&node("Group", &[("body", "fixed")])).unwrap();
        assert_eq!(entity.physics.body, BodyKind::Fixed);
    }

    #[test]
    fn test_gltf_scene_missing_url_is_skipped() {
        let mut ctx = ParseCtx::default();
        let spec = parse_entity(&node("GltfScene", &[]), &mut ctx).unwrap();
        assert!(spec.is_none());
        assert!(ctx.warnings.iter().any(|m| m.contains("missing url")));
    }

    #[test]
    fn test_particle_system_transform_component_string() {
        let (entity, w) = parse_one(&node(
            "ParticleSystem",
            &[("preset", "fire"), ("transform", "pos: 0 1.2 3")],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        assert_eq!(entity.transform.translation, [0.0, 1.2, 3.0]);
        let EntityKind::ParticleSystem { spec } = entity.kind else {
            panic!("expected particle system");
        };
        assert_eq!(spec.preset, "fire");
        assert!(spec.looping);
    }

    #[test]
    fn test_particle_system_full_emitter_string() {
        let (entity, w) = parse_one(&node(
            "ParticleSystem",
            &[
                ("preset", "leaves"),
                ("transform", "pos: 0 6.5 0"),
                (
                    "particle-emitter",
                    "preset: leaves; emission-rate: 5; start-life-min: 4; start-life-max: 7; \
                     start-speed-min: 0.2; start-speed-max: 0.6; start-size-min: 0.1; \
                     start-size-max: 0.22; looping: 1; world-space: 1",
                ),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        assert_eq!(entity.transform.translation, [0.0, 6.5, 0.0]);
        let EntityKind::ParticleSystem { spec } = entity.kind else {
            panic!("expected particle system");
        };
        assert_eq!(spec.preset, "leaves");
        assert_eq!(spec.emission_rate, Some(5.0));
        assert_eq!(spec.life, Some((4.0, 7.0)));
        assert_eq!(spec.speed, Some((0.2, 0.6)));
        assert_eq!(spec.size, Some((0.1, 0.22)));
        assert!(spec.looping && spec.world_space);
    }

    #[test]
    fn test_particle_system_color_override() {
        let (spec, w) = parse_one(&node(
            "ParticleSystem",
            &[(
                "particle-emitter",
                "preset: smoke; start-color: #888888; emission-rate: 8",
            )],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::ParticleSystem { spec } = spec.kind else {
            panic!("expected particle system");
        };
        assert_eq!(spec.preset, "smoke");
        assert_eq!(spec.color, Some([136.0 / 255.0; 3]));
        assert_eq!(spec.emission_rate, Some(8.0));
    }

    #[test]
    fn test_dialogue_npc_parses() {
        let (spec, w) = parse_one(&node(
            "DialogueNPC",
            &[
                ("dialogue-id", "city_stone"),
                ("marker-height", "3"),
                ("portrait-url", ""),
                ("voice-sfx", ""),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::DialogueNpc {
            dialogue_id,
            marker_height,
        } = spec.kind
        else {
            panic!("expected dialogue npc");
        };
        assert_eq!(dialogue_id, "city_stone");
        assert_eq!(marker_height, 3.0);
    }

    #[test]
    fn test_dialogue_npc_without_id_is_skipped() {
        let mut ctx = ParseCtx::default();
        let spec = parse_entity(&node("DialogueNPC", &[]), &mut ctx).unwrap();
        assert!(spec.is_none());
        assert!(
            ctx.warnings
                .iter()
                .any(|m| m.contains("missing dialogue-id"))
        );
    }

    #[test]
    fn test_resource_chip_parses() {
        let (spec, w) = parse_one(&node(
            "ResourceChip",
            &[
                ("resource", "gold"),
                ("icon", "/assets/icons/hud_gold.png"),
                ("target-entity", "player"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::ResourceChip {
            resource,
            icon,
            target,
        } = spec.kind
        else {
            panic!("expected resource chip");
        };
        assert_eq!(resource, "gold");
        assert_eq!(icon, "/assets/icons/hud_gold.png");
        assert_eq!(target, "player");
    }

    #[test]
    fn test_audio_mixer_parses_buses() {
        let (spec, w) = parse_one(&node(
            "AudioMixer",
            &[("master", "1"), ("music", "0.7"), ("sfx", "0.8")],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::AudioMixer { master, music, sfx } = spec.kind else {
            panic!("expected audio mixer");
        };
        assert_eq!(master, 1.0);
        assert_eq!(music, 0.7);
        assert_eq!(sfx, 0.8);
    }

    #[test]
    fn test_music_layer_parses_layer_and_volume() {
        let (spec, w) = parse_one(&node(
            "MusicLayer",
            &[
                ("layer", "explore"),
                ("sound", "bgm-explore"),
                ("base-volume", "0.18"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::MusicLayer { layer, base_volume } = spec.kind else {
            panic!("expected music layer");
        };
        assert_eq!(layer, "explore");
        assert_eq!(base_volume, 0.18);
    }

    #[test]
    fn test_hud_element_keeps_raw_attrs() {
        let (spec, w) = parse_one(&node(
            "InteractionPrompt",
            &[
                ("key", "E"),
                ("range", "4.5"),
                ("position", "bottom-center"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::HudElement { tag, attrs } = spec.kind else {
            panic!("expected hud element");
        };
        assert_eq!(tag, "interactionprompt");
        assert_eq!(
            attrs,
            vec![
                ("key".to_string(), "E".to_string()),
                ("range".to_string(), "4.5".to_string()),
                ("position".to_string(), "bottom-center".to_string()),
            ]
        );
    }

    #[test]
    fn test_static_spawner_spec_and_template_urls() {
        let mut template = node("GameObject", &[("role", "static")]);
        template.children = vec![node(
            "GLTFLoader",
            &[("url", "/assets/meshes/forest/pine_dark_lod0.glb")],
        )];
        let mut spawner = node(
            "StaticSpawner",
            &[
                ("count", "380"),
                ("seed", "6101"),
                ("region-min", "-184 0 116"),
                ("region-max", "184 0 356"),
                ("avoid-water", "1"),
                ("max-slope-deg", "40"),
                ("footprint-radius", "3.8"),
                ("avoid-overlaps", "1"),
                ("cluster-count", "14"),
                ("cluster-radius", "52"),
                ("random-yaw", "true"),
                ("profile", "tree"),
                ("variation", "tree"),
            ],
        );
        spawner.children = vec![template];
        let (spec, w) = parse_one(&spawner).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::StaticSpawner { spec } = spec.kind else {
            panic!("expected static spawner");
        };
        assert_eq!(spec.count, 380);
        assert_eq!(spec.seed, 6101);
        assert_eq!(spec.region_min, [-184.0, 0.0, 116.0]);
        assert_eq!(spec.region_max, [184.0, 0.0, 356.0]);
        assert_eq!(
            spec.template_urls,
            vec!["/assets/meshes/forest/pine_dark_lod0.glb"]
        );
        assert!(spec.avoid_water && spec.avoid_overlaps && spec.random_yaw);
        assert_eq!(spec.max_slope_deg, 40.0);
        assert_eq!(spec.cluster_count, 14);
    }

    #[test]
    fn test_static_spawner_without_template_warns_but_keeps_spec() {
        let (spec, w) = parse_one(&node("StaticSpawner", &[("count", "5")])).unwrap();
        let EntityKind::StaticSpawner { spec } = spec.kind else {
            panic!("expected static spawner");
        };
        assert!(spec.template_urls.is_empty());
        assert!(w.iter().any(|m| m.contains("no template glTF url")));
    }

    #[test]
    fn test_ambient_light_is_resource_kind() {
        let (spec, _) = parse_one(&node("AmbientLight", &[("brightness", "300")])).unwrap();
        assert!(matches!(spec.kind, EntityKind::AmbientLight { .. }));
    }

    #[test]
    fn test_multiple_ambient_lights_warn() {
        let world =
            parse_world(&[], &[node("AmbientLight", &[]), node("AmbientLight", &[])]).unwrap();
        assert!(
            world
                .warnings
                .iter()
                .any(|w| w.contains("multiple <AmbientLight>")),
            "{:?}",
            world.warnings
        );
    }

    #[test]
    fn test_single_ambient_light_does_not_warn() {
        let world = parse_world(&[], &[node("AmbientLight", &[])]).unwrap();
        assert!(world.warnings.is_empty(), "{:?}", world.warnings);
    }

    #[test]
    fn test_orbit_camera_defaults_and_overrides() {
        let (spec, _) = parse_one(&node(
            "OrbitCamera",
            &[("target", "props"), ("pitch", "30")],
        ))
        .unwrap();
        let EntityKind::OrbitCamera {
            target,
            distance,
            height,
            pitch_deg,
            ..
        } = spec.kind
        else {
            panic!("expected camera");
        };
        assert_eq!(target.as_deref(), Some("props"));
        assert_eq!(distance, 12.0);
        assert_eq!(height, 4.0);
        assert_eq!(pitch_deg, Some(30.0));
    }

    #[test]
    fn test_orbit_camera_pitch_defaults_to_none() {
        let (spec, _) = parse_one(&node("OrbitCamera", &[])).unwrap();
        let EntityKind::OrbitCamera { pitch_deg, .. } = spec.kind else {
            panic!("expected camera");
        };
        assert_eq!(pitch_deg, None);
    }

    #[test]
    fn test_group_nesting_children() {
        let mut parent = node("Group", &[("name", "props")]);
        parent.children = vec![node("Cuboid", &[]), node("Entity", &[("name", "kid")])];
        let (spec, _) = parse_one(&parent).unwrap();
        assert_eq!(spec.children.len(), 2);
        assert!(matches!(
            spec.children[0].kind,
            EntityKind::Primitive { .. }
        ));
    }

    #[test]
    fn test_parse_world_root_attrs_and_warnings() {
        let world = parse_world(
            &[
                ("clear-color".into(), "#87ceeb".into()),
                ("sky".into(), "#fff".into()),
            ],
            &[],
        )
        .unwrap();
        assert_eq!(
            world.clear_color,
            Some([135.0 / 255.0, 206.0 / 255.0, 235.0 / 255.0])
        );
        assert!(world.warnings.iter().any(|w| w.contains("`sky`")));
    }

    #[test]
    fn test_summarize_counts_kinds() {
        let mut group = node("Group", &[]);
        group.children = vec![
            node("Cuboid", &[]),
            node("PointLight", &[]),
            node("DirectionalLight", &[]),
            node("AmbientLight", &[]),
        ];
        let world = parse_world(
            &[],
            &[
                group,
                node("OrbitCamera", &[]),
                node("Terrain", &[]),
                node("TerrainPad", &[]),
                node("Lake", &[]),
                node("River", &[]),
                node("Road", &[]),
                node("RoadNetwork", &[]),
                node("GltfScene", &[("url", "/assets/meshes/x.glb")]),
                node("StaticSpawner", &[("count", "10"), ("seed", "7")]),
                node(
                    "ParticleSystem",
                    &[("preset", "fire"), ("transform", "pos: 0 1 2")],
                ),
                node("DynamicSpawner", &[("count", "3"), ("seed", "9")]),
                node("SpawnExclusion", &[("at", "0 0"), ("radius", "52")]),
                node(
                    "Vegetation",
                    &[
                        ("meshes", "/assets/meshes/vegetation/grass.glb"),
                        ("density-per-km2", "5000"),
                    ],
                ),
                node(
                    "PlayerGLTF",
                    &[("model-url", "/assets/meshes/characters/hero_lod0.glb")],
                ),
                node("DialogueNPC", &[("dialogue-id", "city_stone")]),
                node(
                    "ResourceChip",
                    &[("resource", "gold"), ("target-entity", "player")],
                ),
                node("HealthBar", &[("target-entity", "player")]),
                node("WorldBorder", &[("radius", "3800")]),
                node("AudioMixer", &[("master", "1"), ("music", "0.7")]),
                node(
                    "MusicLayer",
                    &[
                        ("layer", "explore"),
                        ("sound", "bgm-explore"),
                        ("base-volume", "0.18"),
                    ],
                ),
            ],
        )
        .unwrap();
        let summary = summarize(&world);
        assert_eq!(
            summary,
            WorldSummary {
                groups: 1,
                primitives: 1,
                point_lights: 1,
                directional_lights: 1,
                cameras: 1,
                has_ambient: true,
                terrain: 1,
                terrain_pads: 1,
                lakes: 1,
                rivers: 1,
                roads: 1,
                road_networks: 1,
                gltf_scenes: 1,
                static_spawners: 1,
                particle_systems: 1,
                dynamic_spawners: 1,
                vegetation: 1,
                spawn_exclusions: 1,
                players: 1,
                dialogue_npcs: 1,
                resource_chips: 1,
                hud_elements: 1,
                audio_mixer: 1,
                music_layers: 1,
                world_systems: 1,
            }
        );
        assert_eq!(summary.entities(), 8);
        assert_eq!(summary.ground_features(), 5);
    }

    // ----- terrain feature parsing -----

    #[test]
    fn test_terrain_defaults_and_attrs() {
        let (spec, w) = parse_one(&node(
            "Terrain",
            &[
                ("world-size", "4000"),
                ("max-height", "200"),
                ("levels", "5"),
                ("resolution", "128"),
                ("collision-resolution", "64"),
                ("heightmap", "terrain/terrain.ahgt.png"),
                ("base-color", "#c9c5ba"),
                ("color-rock", "#6b6560"),
                ("snow-height", "0.92"),
                ("seed", "7"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Terrain { spec } = spec.kind else {
            panic!("expected terrain");
        };
        assert_eq!(spec.world_size, 4000.0);
        assert_eq!(spec.max_height, 200.0);
        assert_eq!(spec.levels, 5);
        assert_eq!(spec.resolution, 128);
        assert_eq!(spec.collision_resolution, 64);
        assert_eq!(spec.heightmap.as_deref(), Some("terrain/terrain.ahgt.png"));
        assert_eq!(spec.seed, 7);
        assert_eq!(
            spec.tint.base_color,
            bevy::color::Color::srgb(201.0 / 255.0, 197.0 / 255.0, 186.0 / 255.0)
        );
    }

    #[test]
    fn test_terrain_bad_levels_is_error() {
        assert!(parse_one(&node("Terrain", &[("levels", "x")])).is_err());
        assert!(parse_one(&node("Terrain", &[("world-size", "0")])).is_err());
    }

    #[test]
    fn test_terrain_pad_at_size_and_height() {
        let (spec, w) = parse_one(&node(
            "TerrainPad",
            &[
                ("at", "0 0"),
                ("size", "120 120"),
                ("falloff", "20"),
                ("corner-radius", "18"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::TerrainPad { spec } = spec.kind else {
            panic!("expected pad");
        };
        assert_eq!(spec.at, Vec2::ZERO);
        assert_eq!(spec.size, Vec2::splat(120.0));
        assert_eq!(spec.falloff, 20.0);
        assert_eq!(spec.height, None, "absent height = auto mode");
        // height="0" activates absolute mode.
        let (spec, _) = parse_one(&node("TerrainPad", &[("at", "1 2"), ("height", "0")])).unwrap();
        let EntityKind::TerrainPad { spec } = spec.kind else {
            panic!("expected pad");
        };
        assert_eq!(spec.height, Some(0.0));
    }

    #[test]
    fn test_terrain_pad_at_is_parent_local() {
        // `at` is local to the parent (VibeGame semantics); the group
        // translation is accumulated by the spawn collector.
        let mut group = node("Group", &[("translation", "859 0 281")]);
        group.children = vec![node(
            "TerrainPad",
            &[("at", "5 7"), ("size", "280 260"), ("falloff", "24")],
        )];
        let world = parse_world(&[], &[group]).unwrap();
        let EntityKind::TerrainPad { spec } = &world.entities[0].children[0].kind else {
            panic!("expected pad");
        };
        assert_eq!(spec.at, Vec2::new(5.0, 7.0), "at stays parent-local");
    }

    #[test]
    fn test_lake_and_river_parse() {
        let (spec, w) = parse_one(&node(
            "Lake",
            &[
                ("at", "-190 -16"),
                ("radius", "24"),
                ("depth", "2.6"),
                ("water-offset", "0.5"),
                ("color", "#2f5a4a"),
                ("opacity", "0.8"),
                ("ripple", "0.5"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Lake { spec } = spec.kind else {
            panic!("expected lake");
        };
        assert_eq!(spec.at, Vec2::new(-190.0, -16.0));
        assert_eq!(spec.radius, 24.0);
        assert_eq!(spec.depth, 2.6);
        assert_eq!(spec.color, [47.0 / 255.0, 90.0 / 255.0, 74.0 / 255.0]);

        let (spec, w) = parse_one(&node(
            "River",
            &[
                ("path", "4 215 30 210 60 216"),
                ("width", "16"),
                ("depth", "3.7"),
                ("water-offset", "1.2"),
                ("bank-width", "6.4"),
                ("bank-height", "0.85"),
                ("color", "#2a6685"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::River { spec } = spec.kind else {
            panic!("expected river");
        };
        assert_eq!(spec.path.len(), 3);
        assert_eq!(spec.path[0], Vec2::new(4.0, 215.0));
        assert_eq!(spec.width, 16.0);
        assert_eq!(spec.bank_width, 6.4);
    }

    #[test]
    fn test_river_needs_two_points() {
        assert!(parse_one(&node("River", &[("path", "4 215")])).is_err());
    }

    #[test]
    fn test_road_parse_with_flatten_attrs() {
        let (spec, w) = parse_one(&node(
            "Road",
            &[
                ("path", "0 0 100 10 200 0"),
                ("width", "4"),
                ("flatten", "0"),
                ("texture-url", "assets/road.png"),
                ("texture-scale", "6"),
                ("edge-feather", "1.0"),
                ("edge-noise", "0.6"),
            ],
        ))
        .unwrap();
        // edge-noise is accepted-for-compat but warned.
        assert!(w.iter().any(|m| m.contains("edge-noise")), "{w:?}");
        let EntityKind::Road { spec } = spec.kind else {
            panic!("expected road");
        };
        assert_eq!(spec.path.len(), 3);
        assert_eq!(spec.width, 4.0);
        assert!(!spec.flatten, "flatten=0 is a decal trail");
        assert_eq!(spec.texture.as_deref(), Some("assets/road.png"));
    }

    #[test]
    fn test_road_network_ways_segments_and_profiles() {
        let mut net = node(
            "RoadNetwork",
            &[
                ("name", "paths"),
                ("default-profile", "artery"),
                ("default-width", "4"),
                ("crossing-flare", "true"),
                ("flatten", "true"),
                ("flatten-falloff", "26"),
                ("flatten-window", "72"),
                ("flatten-max-grade", "0.34"),
                ("texture-url", "assets/cobble.png"),
                ("texture-scale", "9"),
            ],
        );
        net.children = vec![
            node("Way", &[("id", "plaza"), ("xz", "0 0"), ("width", "4.8")]),
            node("Way", &[("id", "n_gate"), ("xz", "-3 128")]),
            node(
                "Segment",
                &[
                    ("a", "plaza"),
                    ("b", "n_gate"),
                    ("profile", "bridge"),
                    ("bridge-url", "assets/bridge.glb"),
                ],
            ),
            node("Segment", &[("a", "plaza"), ("b", "ghost")]),
        ];
        let (spec, w) = parse_one(&net).unwrap();
        assert!(
            w.iter()
                .any(|m| m.contains("bridge-url") && m.contains("glTF")),
            "{w:?}"
        );
        let EntityKind::RoadNetwork { spec } = spec.kind else {
            panic!("expected network");
        };
        assert_eq!(spec.ways.len(), 2);
        assert_eq!(spec.segments.len(), 2);
        assert_eq!(spec.flatten_falloff, 26.0);
        assert_eq!(spec.flatten_window, 72.0);
        assert!((spec.flatten_max_grade - 0.34).abs() < 1e-6);
        assert_eq!(spec.ways[0].width, Some(4.8));
        assert_eq!(spec.segments[0].profile, Some(RoadProfile::Bridge));
        let expanded = spec.expand();
        assert_eq!(expanded.len(), 1, "unknown way ids skip at expansion");
        assert!(!expanded[0].flatten, "bridge expansion disables flatten");
    }

    #[test]
    fn test_road_network_rejects_unknown_children() {
        let mut net = node("RoadNetwork", &[]);
        net.children = vec![node("Cuboid", &[])];
        assert!(parse_one(&net).is_err());
    }

    #[test]
    fn test_road_network_way_needs_id() {
        let mut net = node("RoadNetwork", &[]);
        net.children = vec![node("Way", &[("xz", "0 0")])];
        assert!(parse_one(&net).is_err());
    }

    #[test]
    fn test_ground_feature_children_warn() {
        let mut lake = node("Lake", &[("at", "0 0")]);
        lake.children = vec![node("Cuboid", &[])];
        let (_, w) = parse_one(&lake).unwrap();
        assert!(
            w.iter().any(|m| m.contains("children are ignored")),
            "{w:?}"
        );
    }

    #[test]
    fn test_via_waypoints_parse_into_segments() {
        let mut net = node("RoadNetwork", &[]);
        net.children = vec![
            node("Way", &[("id", "a"), ("xz", "0 0")]),
            node("Way", &[("id", "b"), ("xz", "10 10")]),
            node("Segment", &[("a", "a"), ("b", "b"), ("via", "4 2 6 8")]),
        ];
        let (spec, w) = parse_one(&net).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::RoadNetwork { spec } = spec.kind else {
            panic!("expected network");
        };
        assert_eq!(
            spec.segments[0].via,
            vec![Vec2::new(4.0, 2.0), Vec2::new(6.0, 8.0)]
        );
    }
}
