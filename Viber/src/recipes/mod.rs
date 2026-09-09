//! Recipe layer: expanded XML nodes → entity IR with Bevy naming.
//!
//! Tags and attributes follow Bevy component/field names (`translation`,
//! `euler`, `half-size`, `base-color`, `metallic`, …). Tag matching is
//! case-insensitive; unknown attributes become warnings and unknown elements
//! are skipped as no-ops (so worlds written against a bigger tag vocabulary
//! still run — `analyze` reports what was skipped).

pub mod spawn;
pub mod transform;

use crate::physics::{BodyKind, PhysicsSpec, parse_body, parse_collider};
use std::collections::BTreeMap;

use anyhow::{Result, anyhow, bail};
use bevy::math::Vec2;

use crate::terrain::TerrainSpec;
use crate::terrain::cliffs::{CliffProfile, CliffSide, CliffSpec};
use crate::terrain::decal::GroundDecalSpec;
use crate::terrain::roads::{RoadNetworkSpec, RoadProfile, RoadSpec, SegmentSpec, WaySpec};
use crate::terrain::spec::TerrainPadSpec;
use crate::terrain::voxel::ArchSpec;
use crate::terrain::voxel::BridgeSpec;
use crate::terrain::voxel::BridgeStyle;
use crate::terrain::voxel::CaveSpec;
use crate::terrain::voxel::RockFeaturesSpec;
use crate::terrain::voxel::arch::ArchProfile;
use crate::terrain::water::{LakeSpec, RiverSpec};
use crate::xml::{XmlNode, values};

/// Tags accepted inside a `<world>` root (lowercase canonical spellings).
/// Anything else parses as a no-op skip; `analyze` reports the coverage.
pub const KNOWN_TAGS: &[&str] = &[
    "entity",
    "group",
    "cuboid",
    // VibeGame spelling — inside `<Composition>` (and anywhere else) a
    // `<Box>` is a cuboid.
    "box",
    "sphere",
    "cylinder",
    "plane",
    "capsule",
    // Composed objects (`<Composition>`) + prototypes (`<Prototype>`/`<Use>`).
    "composition",
    "prototype",
    "use",
    "pointlight",
    "directionallight",
    "ambientlight",
    "orbitcamera",
    "terrain",
    "terrainpad",
    "lake",
    "river",
    "cliff",
    "cave",
    "arch",
    "bridge",
    "rockfeatures",
    "grounddecal",
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
    // Declarative UI (src/ui): stylesheet + element tree.
    "uistyle",
    "uiroot",
];

/// A parsed world: clear color, entity tree and non-fatal warnings.
#[derive(Debug, Clone, Default)]
pub struct ParsedWorld {
    pub clear_color: Option<[f32; 3]>,
    pub entities: Vec<EntitySpec>,
    pub warnings: Vec<String>,
    /// Elements skipped because their tag is not implemented, by tag name.
    pub skipped_tags: BTreeMap<String, usize>,
    /// `<Prototype id>` definitions (collected from the whole expanded tree
    /// BEFORE the entity pass, so definition order and `<Include>` position
    /// never matter). Never spawned — só a fonte das expansões de `<Use>`.
    pub prototypes: BTreeMap<String, EntitySpec>,
    /// Instâncias por protótipo após a expansão (`analyze`/telemetria).
    pub prototype_instances: BTreeMap<String, usize>,
    /// Ids referenced by `<Use>` sem definição (warning; `--strict` = erro).
    pub unknown_prototypes: Vec<String>,
}

/// Accumulates non-fatal parse findings.
#[derive(Debug, Default)]
struct ParseCtx {
    warnings: Vec<String>,
    skipped: BTreeMap<String, usize>,
    /// Profundidade de `<Composition>` em que o parser está: primitivas a
    /// depth > 0 sem física autoral ganham `ColliderShape::Auto` (colisor
    /// exato derivado da forma no spawn). Grupos são transparentes ao
    /// contexto (sub-pivots não o desligam).
    composition_depth: usize,
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
///
/// A chave do cache de materiais do spawn é uma forma canonizada em bits
/// (`spawn::MaterialKey`) — f32 não é `Eq`/`Hash`, mas duas specs com os
/// mesmos bits são o mesmo material.
#[derive(Debug, Clone, Default)]
pub struct MaterialSpec {
    pub base_color: Option<[f32; 3]>,
    pub metallic: Option<f32>,
    pub roughness: Option<f32>,
    /// `opacity` (0..1) — abaixo de 1 a parte fica translúcida
    /// (`AlphaMode::Blend`, `depthWrite` off no renderer); 0 = invisível,
    /// útil para paredes de colisor puro (o truque `opacity="0"` do
    /// VibeGame).
    pub opacity: Option<f32>,
    /// `emissive` (#hex) — brilho próprio da parte (vidro de lanterna,
    /// brasa); independente da luz da cena.
    pub emissive: Option<[f32; 3]>,
    /// `texture="/assets/…"`` (ou `texture-url`) — base color map. Os
    /// fragmentos migrados do VibeGame tinham `texture-url` nas decal
    /// planes (praça de cobblestone) e o migrador deixou cair o atributo;
    /// suportar aqui devolve o visual sem tocar no conteúdo.
    pub texture: Option<String>,
    /// `texture-tile-size` — metros por repetição da textura (UVs da
    /// primitiva são 0..1; sem isso a textura estica pela malha toda).
    pub texture_tile: Option<f32>,
}

/// `place="at: x z; …"` de uma `<Composition>` — colocação explícita no
/// terreno, resolvida no arranque por [`crate::worldsys::resolve_pending_place`].
#[derive(Debug, Clone, PartialEq)]
pub struct PlaceSpec {
    /// XZ mundo (soma dos XZ dos ancestrais, como as ground features).
    pub at: [f32; 2],
    /// `align-to-terrain` — orienta o +Y do root à normal do terreno
    /// (yaw autoral preservado). Default off.
    pub align_to_terrain: bool,
    /// `base-y-offset` — somado em Y após a amostragem do solo.
    pub base_y_offset: f32,
}

/// Parseia `place="at: x z; align-to-terrain: 0|1; base-y-offset: y"`.
/// Component-string; `at` é obrigatório (composição sem `at` devolve None
/// com warning).
fn parse_place(value: &str, ctx_tag: &str, ctx: &mut ParseCtx) -> Option<PlaceSpec> {
    let mut place = PlaceSpec {
        at: [0.0, 0.0],
        align_to_terrain: false,
        base_y_offset: 0.0,
    };
    let mut has_at = false;
    for (key, val) in parse_component_string(value) {
        match key.as_str() {
            "at" => match values::parse_vec2(&val, &format!("{ctx_tag} place at")) {
                Ok([x, z]) => {
                    place.at = [x, z];
                    has_at = true;
                }
                Err(error) => ctx.warnings.push(format!("{ctx_tag}: {error}")),
            },
            "align-to-terrain" => {
                place.align_to_terrain =
                    values::parse_bool(&val, &format!("{ctx_tag} place align-to-terrain"))
                        .unwrap_or(false);
            }
            "base-y-offset" | "y-offset" => {
                place.base_y_offset =
                    values::parse_f32(&val, &format!("{ctx_tag} place base-y-offset"))
                        .unwrap_or(0.0);
            }
            // surface-epsilon / max-slope-deg do VibeGame: aceites sem
            // efeito (a amostragem Viber já resolve o solo pelo span).
            "surface-epsilon" | "max-slope-deg" => {}
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored place key `{other}`")),
        }
    }
    if !has_at {
        ctx.warnings.push(format!(
            "{ctx_tag}: place sem `at: x z` — colocação explícita ignorada"
        ));
        return None;
    }
    Some(place)
}

#[derive(Debug, Clone)]
pub enum EntityKind {
    /// Transform-only container (the `entity` and `group` tags).
    Group,
    /// `<Composition>` — um objeto inteiro definido por primitivas: o root
    /// recebe um corpo ([`BodyKind`], default `fixed`) e cada primitiva
    /// descendente ganha um colisor EXATO derivado da própria forma
    /// (cuboid/ball/cylinder/capsule) — colisor composto do Rapier, não o
    /// AABB-união dos grupos. Luzes/partículas como filhos continuam a ser
    /// entidades normais.
    Composition {
        /// `body` (default `fixed`; `none` desliga o corpo).
        body: BodyKind,
        /// `collider="auto"` (default) liga os colisores por parte;
        /// `collider="none"` deixa a composition só visual.
        collider: bool,
        /// `place="at: x z; …"` — colocação explícita (substitui o seating).
        place: Option<PlaceSpec>,
        /// `seat="none"` — objeto flutuante: não assenta no terreno.
        seat: bool,
    },
    /// `<Use prototype="id">` — instância de um `<Prototype>`. Expandida em
    /// parse-time (deep-clone + overrides); nunca chega ao spawn.
    Use {
        prototype: String,
        /// Attrs crus do `<Use>` para aplicar sobre o root do protótipo.
        overrides: Vec<(String, String)>,
    },
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
    Terrain {
        spec: TerrainSpec,
    },
    /// `<TerrainPad>` — ground-flattening pad (world XZ, hierarchy-translated).
    TerrainPad {
        spec: TerrainPadSpec,
    },
    /// `<Lake>` — carved bowl + water mirror.
    Lake {
        spec: LakeSpec,
    },
    GroundDecal {
        spec: GroundDecalSpec,
    },
    /// `<River>` — carved channel + water ribbon.
    River {
        spec: RiverSpec,
    },
    /// `<Cliff>` — carved wall face along a crest polyline (procedural
    /// penhasco 2.5D, `src/terrain/cliffs.rs`).
    Cliff {
        spec: CliffSpec,
    },
    /// `<Cave>` — tunnel bored through the terrain (voxel, not a carve:
    /// `src/terrain/voxel/cave.rs`). The first feature the heightfield cannot
    /// represent at all — inside one there is rock over your head.
    Cave {
        spec: CaveSpec,
    },
    /// `<Arch>` — free-standing rock portal (voxel union solid,
    /// `src/terrain/voxel/arch.rs`): walk UNDER it and the same column has
    /// two solid spans.
    Arch {
        spec: ArchSpec,
    },
    /// `<Bridge>` — a crossing built as union solids in the voxel field
    /// (`src/terrain/voxel/bridge.rs`), so the deck the mesher draws is the
    /// deck the collider bakes. Unlike a `<Road profile="bridge">` ribbon,
    /// you can stand on it.
    Bridge {
        spec: BridgeSpec,
    },
    /// `<RockFeatures>` — arches, caves and rock spans seeded over a region
    /// (`src/terrain/voxel/scatter.rs`). Resolves into ordinary specs at
    /// bootstrap, so nothing downstream sees a new kind of feature.
    RockFeatures {
        spec: RockFeaturesSpec,
    },
    /// `<Road>` — carved corridor + ribbon.
    Road {
        spec: RoadSpec,
    },
    /// `<RoadNetwork>` — one road per `<Segment>` (expanded at carve time).
    RoadNetwork {
        spec: RoadNetworkSpec,
    },
    /// glTF asset loaded async; its default scene spawns under the entity
    /// (the entity's transform applies). Paths starting with `/` are relative
    /// to the engine asset root.
    GltfScene {
        url: String,
    },
    /// `<StaticSpawner>` — instancias `count` do template GLB sobre o terreno
    /// (posições determinísticas por `seed`; regras de água/declive/sobreposição).
    /// Consumido pelo runtime de spawner, não spawna entidade própria.
    StaticSpawner {
        spec: StaticSpawnerSpec,
    },
    /// `<DynamicSpawner>` — mesmas regras de colocação do StaticSpawner; as
    /// entidades nascem marcadas como criaturas (IA/combat chegam com scripts).
    DynamicSpawner {
        spec: StaticSpawnerSpec,
    },
    /// `<SpawnExclusion>` — círculo global onde spawners não colocam instâncias.
    /// Recolhido num recurso global, não spawna entidade.
    SpawnExclusion {
        center: [f32; 2],
        radius: f32,
    },
    /// `<Vegetation>` — erva/flores densas por densidade/km² (convertida num
    /// grupo de spawner com cap; instancing GPU é follow-up).
    Vegetation {
        spec: VegetationSpec,
    },
    /// `<ParticleSystem>` — emissor de partículas (fogueiras, poeira, clima).
    /// Entity própria; emissor CPU billboard desenhado em `particles`.
    ParticleSystem {
        spec: ParticleSpec,
    },
    /// `<PlayerGLTF model-url>` — the controllable hero: glTF scene plus the
    /// [`crate::player::Player`] movement component.
    PlayerGltf {
        url: String,
    },
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
    /// `<UiStyle>` — a CSS-like stylesheet; the text content is the source.
    UiStyle {
        source: String,
    },
    /// `<UiRoot>` — the declarative UI tree, kept whole (children included)
    /// because `src/ui/tree.rs` builds bevy_ui nodes straight from the XML.
    UiTree {
        node: Box<XmlNode>,
    },
    /// `<AudioMixer>` bus volumes (world resource).
    AudioMixer {
        master: f32,
        music: f32,
        sfx: f32,
    },
    /// `<MusicLayer layer sound base-volume>` — looped BGM bus layer; the
    /// driver crossfades layers by player zone (`src/music.rs`).
    MusicLayer {
        layer: String,
        base_volume: f32,
    },
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
        /// Floor for the directional-light elevation (`min-sun-elevation`).
        min_sun_elevation: f32,
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
        /// `display-name`: nome de exposição da zona no HUD (`zone.name`).
        /// Vazio = a engine usa a sua tabela de fallback.
        display_name: String,
        polygon: Vec<[f32; 2]>,
        fog_density: f32,
        tint: Option<[f32; 3]>,
        /// `pp-exposure`: linear exposure multiplier inside the region
        /// (`1.0` = the world's base exposure). Consumed by `crate::postfx`.
        pp_exposure: Option<f32>,
        /// `pp-bloom-strength`: bloom intensity inside the region.
        pp_bloom_strength: Option<f32>,
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
    pub min_sun_elevation: f32,
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

/// Estilo de quebra de um prop destrutível (`break-style` no component-string
/// `destructible`) — port do enum da referência VibeGame (`destructible`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BreakStyleSpec {
    /// Só o burst de partículas (default).
    #[default]
    Burst,
    /// Árvore tomba (toco persiste; a malha `Top` pré-dividida cai).
    Fall,
    /// Rocha estilhaça em pedaços balísticos.
    Shatter,
}

/// `destructible="…"` (attr universal, component-string do VibeGame) — os
/// valores crus do XML; a resolução para o componente runtime
/// [`crate::harvest::Destructible`] aplica os defaults da referência.
///
/// Chaves aceites sem efeito funcional: `spark-on-hit` (sparks de hit são
/// sempre ligadas), `crack-on-hit`/`crack-style` (o darken por hit cobre o
/// mesmo papel), `cut-height` (os GLBs do mundo já vêm pré-divididos em
/// `Stump`/`Top`), `impact-fraction`/`face-on-hit` (a engine usa os valores
/// do melee). Chaves desconhecidas são ignoradas em silêncio — o attr migra
/// verbatim e o `analyze` não ganha warnings novos.
#[derive(Debug, Clone, PartialEq)]
pub struct DestructibleSpec {
    /// `popup-text` — texto flutuante no break.
    pub popup_text: Option<String>,
    /// `popup-color` (#hex) — cor do popup.
    pub popup_color: Option<[f32; 3]>,
    /// `preset` — preset de partículas do burst de break.
    pub preset: Option<String>,
    /// `burst-count` — partículas do burst de break.
    pub burst_count: Option<u32>,
    /// `hits` — golpes até quebrar (default 3, aplicado na conversão).
    pub hits: Option<u32>,
    /// `hit-preset` — preset do burst por golpe (mapeado na conversão:
    /// rockshards→sparks, woodchips→leaves, dust→ground-dust).
    pub hit_preset: Option<String>,
    /// `hit-burst-count` — partículas do burst por golpe.
    pub hit_burst_count: Option<u32>,
    /// `shake-on-hit` — wobble do visual por golpe.
    pub shake_on_hit: bool,
    /// `break-style` — burst/fall/shatter (default burst).
    pub break_style: BreakStyleSpec,
    /// `range` — alcance do golpe em metros (default 3.5 na conversão).
    pub range: Option<f32>,
    /// `(kind, yield)` do `<ResourceNode>` do template — preenchido pelo
    /// parse do spawner (o loot não vem do attr `destructible`).
    pub resource: Option<(String, u32)>,
}

impl Default for DestructibleSpec {
    fn default() -> Self {
        Self {
            popup_text: None,
            popup_color: None,
            preset: None,
            burst_count: None,
            hits: None,
            hit_preset: None,
            hit_burst_count: None,
            shake_on_hit: false,
            break_style: BreakStyleSpec::Burst,
            range: None,
            resource: None,
        }
    }
}

impl DestructibleSpec {
    /// Parser tolerante do component-string (`"hits: 3; break-style: fall"`).
    /// Nunca falha: valores inválidos caem no default da chave.
    pub fn parse(value: &str) -> Self {
        let mut spec = Self::default();
        for (key, val) in parse_component_string(value) {
            match key.as_str() {
                "popup-text" => {
                    let text = val.trim().to_string();
                    if !text.is_empty() {
                        spec.popup_text = Some(text);
                    }
                }
                "popup-color" => {
                    spec.popup_color = values::parse_color(&val, "destructible popup-color").ok();
                }
                "preset" => spec.preset = non_empty(&val),
                "burst-count" => spec.burst_count = parse_u32_tolerant(&val),
                "hits" => spec.hits = parse_u32_tolerant(&val),
                "hit-preset" => spec.hit_preset = non_empty(&val),
                "hit-burst-count" => spec.hit_burst_count = parse_u32_tolerant(&val),
                "shake-on-hit" => {
                    spec.shake_on_hit =
                        values::parse_bool(&val, "destructible shake-on-hit").unwrap_or(false);
                }
                "break-style" => {
                    spec.break_style = match val.trim().to_ascii_lowercase().as_str() {
                        "fall" => BreakStyleSpec::Fall,
                        "shatter" => BreakStyleSpec::Shatter,
                        _ => BreakStyleSpec::Burst,
                    };
                }
                "range" => {
                    spec.range = parse_u32_tolerant(&val)
                        .map(|v| v as f32)
                        .or_else(|| val.trim().parse::<f32>().ok().filter(|v| v.is_finite()));
                }
                // aceites sem efeito (ver doc do struct)
                "spark-on-hit" | "crack-on-hit" | "crack-style" | "cut-height"
                | "impact-fraction" | "face-on-hit" => {}
                // desconhecidas: ignoradas em silêncio (sem warnings novos)
                _ => {}
            }
        }
        spec
    }
}

fn non_empty(value: &str) -> Option<String> {
    let trimmed = value.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_string())
}

/// u32 tolerante (o `values::parse_u32` erra; aqui o default manda).
fn parse_u32_tolerant(value: &str) -> Option<u32> {
    value.trim().parse::<u32>().ok()
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
    /// Never place ON a cliff face/verge or within `cliff_margin` of one —
    /// gates against the [`crate::terrain::cliffs::CliffMask`] dilated layer.
    /// `avoid-cliff`, default ON (`"0"` restaura).
    pub avoid_cliff: bool,
    /// Extra clearance beyond the cliff mask, in meters — `cliff-margin`.
    pub cliff_margin: f32,
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
    /// Script Luau declarado no template (`<Creature script="…">`) — cada
    /// instância spawna com [`LuaScriptRef`] e roda o seu próprio FSM.
    pub template_script: Option<String>,
    /// Raio de ativação (congelamento) das instâncias — `activation-radius`.
    pub activation_radius: f32,
    /// Collider declarado no template (`<GameObject collider="…">`) — cada
    /// instância spawna com colisão (árvores/rochas/props de spawner).
    pub template_collider: Option<crate::physics::ColliderShape>,
    /// `destructible` do template — cada instância spawna colhível
    /// (componente [`crate::harvest::Destructible`]).
    pub template_destructible: Option<DestructibleSpec>,
    /// Ladder de LOD por template (mesmo índice de `template_urls`).
    pub template_lods: Vec<TemplateLod>,
    /// Raio (metros) além do qual a instância deixa de renderizar
    /// (`cull-distance`). Ver [`crate::render_lod::CullDistance`].
    pub cull_distance: f32,
    /// `cast-shadows`: `false` tira as malhas da instância das cascatas de
    /// sombra (erva e folhagem não pagam quatro passes de sombra).
    pub cast_shadows: bool,
    /// `base-y-offset`: somado em Y mundo após o assentamento no solo —
    /// releva GLBs cujo pivô não coincide com a sola.
    pub base_y_offset: f32,
    /// `max-slope-attempts`: tentativas de amostragem POR instância — um
    /// candidato rejeitado (água/estrada/declive/sobreposição) queima uma
    /// tentativa; esgotadas, a instância é omitida (regiões impossíveis
    /// devolvem menos do que `count`).
    pub max_slope_attempts: u32,
    /// `density-per-km2`: modo alternativo de contagem — count =
    /// arredondar(densidade × área km² da região XZ). 0 = desligado (usa
    /// `count`).
    pub density_per_km2: f32,
    /// `max-instances`: teto absoluto quando o count vem de
    /// `density-per-km2` (evita excesso de objetos por área num mundo
    /// grande); 0 = sem teto.
    pub max_instances: u32,
    /// Candidatos XZ explícitos (engine-interna, sem attr XML — as pedras de
    /// margem de `<Lake>`/`<River>`): cada entrada é avaliada UMA vez pelos
    /// gates normais (água/estrada/declive/ocupação); rejeitada = omitida.
    /// Vazio = o modo aleatório habitual (região/clusters).
    pub fixed_candidates: Vec<bevy::math::Vec2>,
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
    /// Same cliff exclusion as the spawners (`avoid-cliff`, default ON).
    pub avoid_cliff: bool,
    /// Extra clearance beyond the cliff mask, in meters (`cliff-margin`).
    pub cliff_margin: f32,
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
    /// Raio de render (metros) — erva desaparece muito antes dos props.
    pub cull_distance: f32,
    /// Erva a projetar sombra em 4 cascatas não se lê e custa o mundo.
    pub cast_shadows: bool,
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
            avoid_cliff: self.avoid_cliff,
            cliff_margin: self.cliff_margin,
            align_to_terrain: true,
            scale_min: self.scale_min,
            scale_max: self.scale_max,
            scale_axis_min: self.scale_axis_min,
            scale_axis_max: self.scale_axis_max,
            random_yaw: self.random_yaw,
            max_distance: self.max_distance,
            template_urls: self.meshes.clone(),
            template_script: None,
            activation_radius: crate::luau::DEFAULT_ACTIVATION_RADIUS,
            template_collider: None,
            template_destructible: None,
            template_lods: Vec::new(),
            cull_distance: self.cull_distance,
            cast_shadows: self.cast_shadows,
            // count já vem resolvido por `instance_count` (com cap).
            base_y_offset: 0.0,
            max_slope_attempts: 32,
            density_per_km2: 0.0,
            max_instances: 0,
            fixed_candidates: Vec::new(),
        }
    }
}

/// Shoreline width for `near-water` placement (meters) — how far from a water
/// body a point still counts as bank.
pub const DEFAULT_NEAR_WATER_RADIUS: f32 = 4.0;

/// Cliff clearance for spawn placement (meters) — how far from the cliff mask
/// (face + vergem dilatada) um prop pode nascer (`cliff-margin`).
pub const DEFAULT_CLIFF_MARGIN: f32 = 2.0;

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
    /// `destructible="…"` — prop colhível/destrutível (`crate::harvest`).
    pub destructible: Option<DestructibleSpec>,
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
    // Passagem 0: protótipos. Recolhidos do raw tree ANTES de parsear
    // entidades — um `<Use>` num ficheiro incluído antes da definição (ou
    // dentro do próprio ficheiro de definição) funciona na mesma.
    let mut prototypes = BTreeMap::new();
    collect_prototypes(nodes, &mut prototypes, &mut ctx)?;
    let entities = parse_entities(nodes, &mut ctx)?;
    let mut instances = BTreeMap::new();
    let mut unknown = Vec::new();
    let entities = expand_uses(
        entities,
        &prototypes,
        &mut Vec::new(),
        &mut instances,
        &mut unknown,
        &mut ctx,
    );
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
        prototypes,
        prototype_instances: instances,
        unknown_prototypes: unknown,
    })
}

/// Recolhe `<Prototype id>` de TODO o raw tree (qualquer profundidade —
/// incluídos spliceiam os seus filhos ao mundo, por isso uma definição
/// dentro de um include chega aqui na mesma). Última definição de um id
/// ganha (ordem de documento). Só o attr `id` é significativo.
fn collect_prototypes(
    nodes: &[XmlNode],
    out: &mut BTreeMap<String, EntitySpec>,
    ctx: &mut ParseCtx,
) -> Result<()> {
    for node in nodes {
        if node.tag.eq_ignore_ascii_case("prototype") {
            match node.attr("id").map(str::trim).filter(|s| !s.is_empty()) {
                Some(id) => {
                    let ctx_tag = format!("<Prototype id=\"{id}\">");
                    for (key, _) in &node.attrs {
                        if !key.eq_ignore_ascii_case("id") {
                            ctx.warnings
                                .push(format!("{ctx_tag}: ignored attribute `{key}`"));
                        }
                    }
                    let children = parse_entities(&node.children, ctx)?;
                    if children.len() != 1 {
                        ctx.warnings.push(format!(
                            "{ctx_tag}: expected exactly 1 root element, got {} — using the first",
                            children.len()
                        ));
                    }
                    if let Some(root) = children.into_iter().next() {
                        out.insert(id.to_string(), root);
                    }
                }
                None => ctx
                    .warnings
                    .push(format!("<{}>: Prototype sem `id` — ignorado", node.tag)),
            }
        }
        collect_prototypes(&node.children, out, ctx)?;
    }
    Ok(())
}

/// Limite de aninhamento de `<Use>` (um protótipo pode instanciar outro).
const MAX_USE_DEPTH: usize = 16;

/// Expande `<Use>` (deep-clone do protótipo + overrides no root), em toda a
/// árvore. `stack` guarda os ids em expansão para cortar ciclos; ids sem
/// definição ficam registados em `unknown` (uma entrada por id, para o
/// `analyze`/`--strict`).
fn expand_uses(
    specs: Vec<EntitySpec>,
    prototypes: &BTreeMap<String, EntitySpec>,
    stack: &mut Vec<String>,
    instances: &mut BTreeMap<String, usize>,
    unknown: &mut Vec<String>,
    ctx: &mut ParseCtx,
) -> Vec<EntitySpec> {
    let mut out = Vec::with_capacity(specs.len());
    for mut spec in specs {
        if let EntityKind::Use {
            prototype,
            overrides,
        } = spec.kind
        {
            if stack.iter().any(|id| id == &prototype) {
                ctx.warnings.push(format!(
                    "<Use prototype=\"{prototype}\">: ciclo de protótipos — instância ignorada"
                ));
                continue;
            }
            if stack.len() >= MAX_USE_DEPTH {
                ctx.warnings.push(format!(
                    "<Use prototype=\"{prototype}\">: aninhamento > {MAX_USE_DEPTH} — instância ignorada"
                ));
                continue;
            }
            let Some(prototype_spec) = prototypes.get(&prototype) else {
                if !unknown.iter().any(|u| u == &prototype) {
                    unknown.push(prototype.clone());
                }
                continue;
            };
            // Expande primeiro a subárvore (para os `<Use>` internos), e só
            // depois aplica os overrides deste `<Use>` no root FINAL — um
            // protótipo cujo root é outro `<Use>` mantém assim o transform
            // pedido em vez de o perder na substituição.
            let clone = prototype_spec.clone();
            stack.push(prototype.clone());
            let mut expanded = expand_uses(vec![clone], prototypes, stack, instances, unknown, ctx);
            stack.pop();
            if let Some(mut inst) = expanded.pop() {
                apply_use_overrides(&mut inst, &overrides, &prototype, ctx);
                *instances.entry(prototype).or_insert(0) += 1;
                out.push(inst);
            }
        } else {
            spec.children = expand_uses(
                std::mem::take(&mut spec.children),
                prototypes,
                stack,
                instances,
                unknown,
                ctx,
            );
            out.push(spec);
        }
    }
    out
}

/// Aplica os attrs do `<Use>` sobre o root clonado do protótipo. Campos
/// presentes SUBSTITUEM os do protótipo (transform: só os componentes
/// indicados; o resto mantém o valor autoral). Valores inválidos avisam e
/// mantêm o do protótipo — um `<Use>` mal escrito nunca falha o mundo.
fn apply_use_overrides(
    spec: &mut EntitySpec,
    overrides: &[(String, String)],
    id: &str,
    ctx: &mut ParseCtx,
) {
    let ctx_tag = format!("<Use prototype=\"{id}\">");
    for (key, value) in overrides {
        let warning: Option<String> = match key.as_str() {
            "name" => {
                spec.name = Some(value.clone());
                None
            }
            "tag" => {
                spec.tag = Some(value.clone());
                None
            }
            "script" => {
                spec.script = Some(value.clone());
                None
            }
            "destructible" => {
                spec.destructible = Some(DestructibleSpec::parse(value));
                None
            }
            "collider" => {
                let (shape, warning) = parse_collider(value);
                spec.physics.collider = shape;
                warning.map(|w| format!("{ctx_tag}: {w}"))
            }
            "rigidbody" | "body" => {
                let (kind, mass, gravity_scale) = parse_body(value);
                spec.physics.body = kind;
                spec.physics.mass = mass;
                spec.physics.gravity_scale = gravity_scale;
                None
            }
            "translation" | "pos" => values::parse_vec3(value, &format!("{ctx_tag} pos"))
                .map(|v| spec.transform.translation = v)
                .map_err(|e| e.to_string())
                .err(),
            "euler" => values::parse_vec3(value, &format!("{ctx_tag} euler"))
                .map(|v| spec.transform.euler_deg = Some(v))
                .map_err(|e| e.to_string())
                .err(),
            "rotation" => parse_rotation_attr(value, &format!("{ctx_tag} rotation"))
                .map(|q| spec.transform.rotation_quat = Some(q))
                .map_err(|e| e.to_string())
                .err(),
            "scale" => values::parse_vec3(value, &format!("{ctx_tag} scale"))
                .map(|v| spec.transform.scale = v)
                .map_err(|e| e.to_string())
                .err(),
            "transform" => {
                for (tkey, tval) in parse_component_string(value) {
                    match tkey.as_str() {
                        "pos" | "position" => {
                            if let Ok(v) =
                                values::parse_vec3(&tval, &format!("{ctx_tag} transform pos"))
                            {
                                spec.transform.translation = v;
                            }
                        }
                        "euler" | "rotation" => {
                            if let Ok(v) =
                                values::parse_vec3(&tval, &format!("{ctx_tag} transform euler"))
                            {
                                spec.transform.euler_deg = Some(v);
                            }
                        }
                        "scale" => {
                            if let Ok(v) =
                                values::parse_vec3(&tval, &format!("{ctx_tag} transform scale"))
                            {
                                spec.transform.scale = v;
                            }
                        }
                        _ => {}
                    }
                }
                None
            }
            other => Some(format!("{ctx_tag}: ignored attribute `{other}`")),
        };
        if let Some(warning) = warning {
            ctx.warnings.push(warning);
        }
    }
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
        "cuboid" | "box" | "sphere" | "cylinder" | "plane" | "capsule" => {
            finish_primitive(node, ctx).map(Some)
        }
        "composition" => finish_composition(node, ctx).map(Some),
        // Definições são recolhidas pela passagem 0 (`collect_prototypes`);
        // aqui são no-op para não caírem no aviso de tag desconhecida.
        "prototype" => Ok(None),
        "use" => finish_use(node, ctx).map(Some),
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
        "musiclayer" => match node.attr("layer").map(str::trim).filter(|s| !s.is_empty()) {
            Some(layer) => finish_music_layer(node, layer, ctx).map(Some),
            None => {
                ctx.warnings
                    .push(format!("<{}>: missing layer — skipped", node.tag));
                Ok(None)
            }
        },
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
        "uistyle" => finish_ui_style(node, ctx).map(Some),
        "uiroot" => finish_ui_tree(node, ctx).map(Some),
        "particlesystem" => finish_particle_system(node, ctx).map(Some),
        "terrain" => finish_terrain(node, ctx).map(Some),
        "terrainpad" => finish_terrain_pad(node, ctx).map(Some),
        "lake" => finish_lake(node, ctx).map(Some),
        "grounddecal" => finish_ground_decal(node, ctx).map(Some),
        "river" => finish_river(node, ctx).map(Some),
        "cliff" => finish_cliff(node, ctx).map(Some),
        "cave" => finish_cave(node, ctx).map(Some),
        "arch" => finish_arch(node, ctx).map(Some),
        "bridge" => finish_bridge(node, ctx).map(Some),
        "rockfeatures" => finish_rock_features(node, ctx).map(Some),
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
    destructible: Option<DestructibleSpec>,
    transform: TransformSpec,
    /// Colliders / rigid bodies (`collider`, `rigidbody`, `body`).
    physics: PhysicsSpec,
}

/// Parseia o attr `rotation`: 4 componentes = quaternion cru `x y z w`
/// (comportamento histórico); 3 componentes = euler em RADIANOS — a
/// convenção das partes de `<Composition>` do VibeGame, aceite em qualquer
/// entidade para os mundos migrarem sem reescrever rotações.
fn parse_rotation_attr(value: &str, ctx: &str) -> Result<[f32; 4]> {
    if value.split_whitespace().count() == 3 {
        let e = values::parse_vec3(value, ctx)?;
        return Ok(transform::euler_rad_to_quat(e));
    }
    values::parse_vec4(value, ctx)
}

/// Parse the universal attributes, returning the ones left for the kind parser.
fn parse_common(node: &XmlNode, ctx: &mut ParseCtx) -> Result<(Common, Vec<(String, String)>)> {
    let ctx_tag = format!("<{}>", node.tag);
    let mut common = Common {
        name: None,
        tag: None,
        script: None,
        destructible: None,
        transform: TransformSpec::default(),
        physics: PhysicsSpec::default(),
    };
    let mut rest = Vec::new();
    for (key, value) in &node.attrs {
        match key.as_str() {
            "name" => common.name = Some(value.clone()),
            "tag" => common.tag = Some(value.clone()),
            "script" => common.script = Some(value.clone()),
            // Component-string do plugin `destructible` do VibeGame — o
            // prop nasce colhível (árvores/rochas dos spawners).
            "destructible" => common.destructible = Some(DestructibleSpec::parse(value)),
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
                // 3 valores = euler radianos (compat VibeGame); 4 = quat xyzw.
                common.transform.rotation_quat =
                    Some(parse_rotation_attr(value, &format!("{ctx_tag} rotation"))?);
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
        destructible: common.destructible,
        kind: EntityKind::Group,
        children: parse_entities(&node.children, ctx)?,
    })
}

/// `<Composition>` — objeto inteiro em primitivas. Defaults pensados para o
/// caso comum: `body="fixed"` (cenário imóvel) e colisores por parte
/// (`collider="auto"`); `body="none"`, `collider="none"`, `seat="none"` e
/// `place="…"` desligam cada default.
fn finish_composition(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    // Corpo: a partir do physics parseado; sem attr → fixed (um objeto
    // composto é quase sempre cenário e o colisor composto precisa de um).
    let body = if node.attr("rigidbody").is_none() && node.attr("body").is_none() {
        crate::physics::BodyKind::Fixed
    } else {
        common.physics.body
    };
    // Colisor: raw attr distingue "auto" (default) de "none"; qualquer
    // component-string explícita não se aplica a uma composition inteira.
    let mut collider = true;
    if let Some(raw) = node.attr("collider") {
        match raw.trim().to_ascii_lowercase().as_str() {
            "" | "auto" | "true" | "1" => {}
            "none" | "false" | "0" => collider = false,
            _ => ctx.warnings.push(format!(
                "{ctx_tag}: collider `{raw}` — numa composition só `auto` (default) ou `none`; a usar por-parte"
            )),
        }
    }
    let mut place = None;
    let mut seat = true;
    let mut unknown = Vec::new();
    for (key, value) in rest {
        match key.as_str() {
            "place" => place = parse_place(&value, &ctx_tag, ctx),
            "seat" => {
                if value.trim().eq_ignore_ascii_case("none") {
                    seat = false;
                } else {
                    unknown.push((key, value));
                }
            }
            // VibeGame: tolerância de análise do CLI, sem efeito em runtime.
            "overlap-max" => {}
            _ => unknown.push((key, value)),
        }
    }
    warn_ignored(node, unknown, ctx);
    // `place` explícito substitui o seating automático por completo.
    if place.is_some() {
        seat = false;
    }
    // As primitivas filhas entram em modo "parte": sem física autoral
    // ganham `ColliderShape::Auto` (colisor exato no spawn) — mas só se a
    // composition estiver com colisores ligados (`collider="none"` põe a
    // profundidade a 0, desligando o modo parte na subárvore).
    let outer_depth = ctx.composition_depth;
    ctx.composition_depth = if collider { outer_depth + 1 } else { 0 };
    let parsed_children = parse_entities(&node.children, ctx);
    ctx.composition_depth = outer_depth;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: crate::physics::PhysicsSpec {
            collider: crate::physics::ColliderShape::None,
            body,
            mass: common.physics.mass,
            gravity_scale: common.physics.gravity_scale,
        },
        destructible: common.destructible,
        kind: EntityKind::Composition {
            body,
            collider,
            place,
            seat,
        },
        children: parsed_children?,
    })
}

/// `<Use prototype="id">` — placeholder de instância; `expand_uses` troca-o
/// pelo protótipo clonado antes do spawn.
fn finish_use(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let ctx_tag = format!("<{}>", node.tag);
    let Some(prototype) = node
        .attr("prototype")
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
    else {
        ctx.warnings
            .push(format!("{ctx_tag}: missing prototype — skipped"));
        return Ok(EntitySpec {
            name: None,
            tag: None,
            script: None,
            destructible: None,
            transform: TransformSpec::default(),
            physics: PhysicsSpec::default(),
            kind: EntityKind::Group,
            children: Vec::new(),
        });
    };
    let overrides: Vec<(String, String)> = node
        .attrs
        .iter()
        .filter(|(key, _)| !key.eq_ignore_ascii_case("prototype"))
        .cloned()
        .collect();
    Ok(EntitySpec {
        name: None,
        tag: None,
        script: None,
        destructible: None,
        transform: TransformSpec::default(),
        physics: PhysicsSpec::default(),
        kind: EntityKind::Use {
            prototype,
            overrides,
        },
        children: Vec::new(),
    })
}

fn finish_primitive(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let lower = node.tag.to_ascii_lowercase();
    let (mut common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut shape = match lower.as_str() {
        "cuboid" | "box" => Shape::Cuboid {
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
            "opacity" => {
                let v = values::parse_f32(&value, &kctx)?;
                material.opacity = Some(v.clamp(0.0, 1.0));
            }
            "emissive" | "emissive-color" => {
                material.emissive = Some(values::parse_color(&value, &kctx)?);
            }
            "texture" | "texture-url" => material.texture = Some(value),
            "texture-tile-size" => material.texture_tile = Some(values::parse_f32(&value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    // Parte de uma `<Composition>` (depth > 0) sem física autoral ganha
    // `ColliderShape::Auto` — colisor EXATO derivado da própria forma no
    // spawn. Um `collider="none"` ou `shape: …` autoral na parte é honrado.
    if ctx.composition_depth > 0
        && node.attr("collider").is_none()
        && node.attr("rigidbody").is_none()
        && node.attr("body").is_none()
    {
        common.physics.collider = crate::physics::ColliderShape::Auto;
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
        kind,
        children: parse_entities(&node.children, ctx)?,
    })
}

/// glTF urls in a spawner's template subtree (original `GLTFLoader` names and
/// native `GltfScene` both accepted), in document order.
/// Ladder de LOD autorada num `<GLTFLoader>` do template.
///
/// Os mundos migrados do VibeGame já trazem `lod1-url` / `lod2-url` e os
/// cortes `lod-threshold-near` / `lod-threshold-mid` — o Viber ignorava-os e
/// desenhava a malha *hero* de cada árvore a 200 m. Ver
/// [`crate::render_lod::MeshLod`].
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TemplateLod {
    /// Malha de LOD 1 (média distância), se autorada.
    pub lod1_url: Option<String>,
    /// Malha de LOD 2 (silhueta), se autorada.
    pub lod2_url: Option<String>,
    /// Distância (metros) em que LOD 0 cede a LOD 1.
    pub near: f32,
    /// Distância em que LOD 1 cede a LOD 2.
    pub mid: f32,
}

/// Cortes de LOD usados quando o template só autora as urls.
pub const DEFAULT_LOD_NEAR: f32 = 45.0;
/// Corte LOD1 → LOD2 por omissão.
pub const DEFAULT_LOD_MID: f32 = 110.0;

impl TemplateLod {
    /// `true` quando o template não autora nenhuma malha alternativa — a
    /// instância não precisa de componente de LOD nenhum.
    pub fn is_empty(&self) -> bool {
        self.lod1_url.is_none() && self.lod2_url.is_none()
    }
}

/// Recolhe urls de template e, em paralelo (mesmo índice), a ladder de LOD.
fn collect_template_meshes(node: &XmlNode, out: &mut Vec<String>, lods: &mut Vec<TemplateLod>) {
    let lower = node.tag.to_ascii_lowercase();
    if matches!(lower.as_str(), "gltfloader" | "gltfscene") {
        if let Some(url) = node.attr("url").map(str::trim).filter(|s| !s.is_empty()) {
            out.push(url.to_string());
            let attr = |name: &str| {
                node.attr(name)
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
            };
            let number = |name: &str, fallback: f32| {
                node.attr(name)
                    .and_then(|v| v.trim().parse::<f32>().ok())
                    .filter(|v| *v > 0.0)
                    .unwrap_or(fallback)
            };
            lods.push(TemplateLod {
                lod1_url: attr("lod1-url"),
                lod2_url: attr("lod2-url"),
                near: number("lod-threshold-near", DEFAULT_LOD_NEAR),
                mid: number("lod-threshold-mid", DEFAULT_LOD_MID),
            });
        }
    }
    for child in &node.children {
        collect_template_meshes(child, out, lods);
    }
}

/// Primeiro attr `script=` na subárvore do template (`<Creature script>`).
fn collect_template_script(node: &XmlNode) -> Option<String> {
    if let Some(script) = node.attr("script").map(str::trim).filter(|s| !s.is_empty()) {
        return Some(script.to_string());
    }
    for child in &node.children {
        if let Some(found) = collect_template_script(child) {
            return Some(found);
        }
    }
    None
}

/// Primeiro attr `destructible=` na subárvore do template
/// (`<GameObject destructible="…">`) — o props nascem colhíveis.
fn collect_template_destructible(node: &XmlNode) -> Option<DestructibleSpec> {
    if let Some(value) = node
        .attr("destructible")
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        return Some(DestructibleSpec::parse(value));
    }
    node.children.iter().find_map(collect_template_destructible)
}

/// Primeiro `<ResourceNode kind="…" yield="…">` na subárvore do template
/// (qualquer profundidade) — o loot nativo da colheita.
fn collect_resource_node(node: &XmlNode) -> Option<(String, u32)> {
    if node.tag.eq_ignore_ascii_case("resourcenode") {
        if let Some(kind) = node.attr("kind").map(str::trim).filter(|s| !s.is_empty()) {
            let amount = node
                .attr("yield")
                .and_then(|v| v.trim().parse::<u32>().ok())
                .unwrap_or(1)
                .max(1);
            return Some((kind.to_string(), amount));
        }
    }
    node.children.iter().find_map(collect_resource_node)
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
        // Default ON (perfis do VibeGame): árvores/pedras/props nunca nascem
        // no leito das estradas — `avoid-road="0"` devolve o comportamento
        // antigo a quem o quiser.
        avoid_road: true,
        // Default ON: nada nasce em parede de cliff nem na sua vergem —
        // `avoid-cliff="0"` restaura o comportamento antigo.
        avoid_cliff: true,
        cliff_margin: DEFAULT_CLIFF_MARGIN,
        align_to_terrain: true,
        scale_min: 1.0,
        scale_max: 1.0,
        scale_axis_min: 1.0,
        scale_axis_max: 1.0,
        random_yaw: false,
        max_distance: 0.0,
        template_urls: Vec::new(),
        template_script: None,
        activation_radius: crate::luau::DEFAULT_ACTIVATION_RADIUS,
        template_collider: None,
        template_destructible: None,
        template_lods: Vec::new(),
        cull_distance: crate::render_lod::DEFAULT_STATIC_CULL,
        cast_shadows: true,
        base_y_offset: 0.0,
        max_slope_attempts: 32,
        density_per_km2: 0.0,
        max_instances: 0,
        fixed_candidates: Vec::new(),
    };
    collect_template_meshes(node, &mut spec.template_urls, &mut spec.template_lods);
    spec.template_script = collect_template_script(node);
    // Destrutível do template: o attr `destructible` + o loot do
    // `<ResourceNode kind yield>` filho (árvores→wood, rochas→stone).
    spec.template_destructible = collect_template_destructible(node);
    if let Some(template) = spec.template_destructible.as_mut() {
        if template.resource.is_none() {
            template.resource = collect_resource_node(node);
        }
    }
    // Collider do template: primeiro filho com attr `collider`.
    for child in &node.children {
        if let Some(value) = child
            .attr("collider")
            .map(str::trim)
            .filter(|c| !c.is_empty())
        {
            let (shape, warning) = crate::physics::parse_collider(value);
            if let Some(warning) = warning {
                ctx.warnings
                    .push(format!("<{}> template: {warning}", node.tag));
            }
            if !matches!(shape, crate::physics::ColliderShape::None) {
                spec.template_collider = Some(shape);
            }
            break;
        }
    }
    if spec.template_urls.is_empty() {
        ctx.warnings
            .push(format!("{ctx_tag}: no template glTF url found — skipped"));
        return Ok(EntitySpec {
            name: common.name,
            tag: common.tag,
            script: common.script,
            transform: common.transform,
            physics: common.physics,
            destructible: common.destructible,
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
            // Inteiros via parsers validados (f32 `as` satura u32::MAX e
            // perde precisão acima de 2^24); count com cap — o with_capacity
            // do spawner e as tentativas count*8+64 escalam com ele.
            "seed" => spec.seed = values::parse_u64(&value, &kctx)?,
            "count" => spec.count = values::parse_u32(&value, &kctx)?.min(100_000),
            "region-min" => spec.region_min = values::parse_vec3(&value, &kctx)?,
            "region-max" => spec.region_max = values::parse_vec3(&value, &kctx)?,
            "cluster-count" => spec.cluster_count = values::parse_u32(&value, &kctx)?.min(10_000),
            "cluster-radius" => spec.cluster_radius = values::parse_f32(&value, &kctx)?,
            "footprint-radius" => spec.footprint_radius = values::parse_f32(&value, &kctx)?,
            "avoid-overlaps" => spec.avoid_overlaps = values::parse_bool(&value, &kctx)?,
            "max-slope-deg" => spec.max_slope_deg = values::parse_f32(&value, &kctx)?,
            "avoid-water" => spec.avoid_water = values::parse_bool(&value, &kctx)?,
            "in-water" => spec.in_water = values::parse_bool(&value, &kctx)?,
            "near-water" => spec.near_water = values::parse_bool(&value, &kctx)?,
            "near-water-radius" => spec.near_water_radius = values::parse_f32(&value, &kctx)?,
            "avoid-road" => spec.avoid_road = values::parse_bool(&value, &kctx)?,
            "avoid-cliff" => spec.avoid_cliff = values::parse_bool(&value, &kctx)?,
            "cliff-margin" => spec.cliff_margin = values::parse_f32(&value, &kctx)?.max(0.0),
            "align-to-terrain" => spec.align_to_terrain = values::parse_bool(&value, &kctx)?,
            "scale-min" => spec.scale_min = values::parse_f32(&value, &kctx)?,
            "scale-max" => spec.scale_max = values::parse_f32(&value, &kctx)?,
            "scale-axis-min" => spec.scale_axis_min = values::parse_f32(&value, &kctx)?,
            "scale-axis-max" => spec.scale_axis_max = values::parse_f32(&value, &kctx)?,
            "random-yaw" => spec.random_yaw = values::parse_bool(&value, &kctx)?,
            "max-distance" => spec.max_distance = values::parse_f32(&value, &kctx)?,
            "cull-distance" => spec.cull_distance = values::parse_f32(&value, &kctx)?.max(0.0),
            "cast-shadows" => spec.cast_shadows = values::parse_bool(&value, &kctx)?,
            "base-y-offset" => spec.base_y_offset = values::parse_f32(&value, &kctx)?,
            "max-slope-attempts" => {
                spec.max_slope_attempts = values::parse_u32(&value, &kctx)?.clamp(1, 4096)
            }
            "density-per-km2" => spec.density_per_km2 = values::parse_f32(&value, &kctx)?.max(0.0),
            "max-instances" => spec.max_instances = values::parse_u32(&value, &kctx)?,
            // accepted no-ops: profile metadata / placement details ported later
            "activation-radius" => {
                spec.activation_radius = values::parse_f32(&value, &kctx)?.max(0.0)
            }
            "profile" | "variation" | "ground-align" | "pick-strategy" => {}
            // A ladder vive no `<GLTFLoader>` do template, não no spawner;
            // `collect_template_meshes` lê-a lá.
            "lod1-url" | "lod2-url" | "lod-threshold-near" | "lod-threshold-mid" => {}
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        avoid_overlaps: true,
        // Erva fora das fitas de estrada por omissão — tapetes a atravessar
        // asfalto lia-se como textura desaparecida (`avoid-road="0"` liga).
        avoid_road: true,
        // Idem cliffs: folhagem nunca em parede/vergem de falésia.
        avoid_cliff: true,
        cliff_margin: DEFAULT_CLIFF_MARGIN,
        random_yaw: true,
        max_distance: 0.0,
        cluster_count: 0,
        cluster_radius: 0.0,
        max_instances: 800,
        cull_distance: crate::render_lod::DEFAULT_VEGETATION_CULL,
        cast_shadows: false,
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "meshes" => {
                spec.meshes = value.split_whitespace().map(str::to_string).collect();
            }
            "density-per-km2" => spec.density_per_km2 = values::parse_f32(&value, &kctx)?,
            "seed" => spec.seed = values::parse_u64(&value, &kctx)?,
            "region-min" => spec.region_min = values::parse_vec3(&value, &kctx)?,
            "region-max" => spec.region_max = values::parse_vec3(&value, &kctx)?,
            "scale-min" => spec.scale_min = values::parse_f32(&value, &kctx)?,
            "scale-max" => spec.scale_max = values::parse_f32(&value, &kctx)?,
            "scale-axis-min" => spec.scale_axis_min = values::parse_f32(&value, &kctx)?,
            "scale-axis-max" => spec.scale_axis_max = values::parse_f32(&value, &kctx)?,
            "max-slope-deg" => spec.max_slope_deg = values::parse_f32(&value, &kctx)?,
            "avoid-water" => spec.avoid_water = values::parse_bool(&value, &kctx)?,
            "avoid-road" => spec.avoid_road = values::parse_bool(&value, &kctx)?,
            "avoid-cliff" => spec.avoid_cliff = values::parse_bool(&value, &kctx)?,
            "cliff-margin" => spec.cliff_margin = values::parse_f32(&value, &kctx)?.max(0.0),
            "avoid-overlaps" => spec.avoid_overlaps = values::parse_bool(&value, &kctx)?,
            "random-yaw" => spec.random_yaw = values::parse_bool(&value, &kctx)?,
            "max-distance" => spec.max_distance = values::parse_f32(&value, &kctx)?,
            "cluster-count" => spec.cluster_count = values::parse_u32(&value, &kctx)?.min(10_000),
            "cluster-radius" => spec.cluster_radius = values::parse_f32(&value, &kctx)?,
            "max-instances" => spec.max_instances = values::parse_u32(&value, &kctx)?.min(200_000),
            "cull-distance" => spec.cull_distance = values::parse_f32(&value, &kctx)?.max(0.0),
            "cast-shadows" => spec.cast_shadows = values::parse_bool(&value, &kctx)?,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
        kind: EntityKind::AudioMixer { master, music, sfx },
        children: Vec::new(),
    })
}

fn finish_music_layer(node: &XmlNode, layer: &str, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let layer = layer.to_string();
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
    let _ = sound;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
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
        min_sun_elevation: crate::worldsys::MIN_LIGHT_ELEVATION_DEG,
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
            "min-sun-elevation" => c.min_sun_elevation = values::parse_f32(&value, &kctx)?,
            "sun-azimuth-base" => c.sun_azimuth_base = values::parse_f32(&value, &kctx)?,
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
        destructible: common.destructible,
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
            min_sun_elevation: c.min_sun_elevation,
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
        destructible: common.destructible,
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
        .filter(|pair| !pair.trim().is_empty())
        .filter_map(|pair| {
            let (x, y) = pair.trim().split_once(',')?;
            // Mesma regra dos demais números: não-finitos rejeitados; um
            // par malformado não reduz o polígono em silêncio — o chamador
            // avisa quando sobram vértices a menos.
            let x = x.trim().parse::<f32>().ok()?;
            let y = y.trim().parse::<f32>().ok()?;
            if x.is_finite() && y.is_finite() {
                Some([x, y])
            } else {
                None
            }
        })
        .collect()
}

fn finish_biome_region(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let mut id = String::new();
    let mut display_name = String::new();
    let mut polygon = Vec::new();
    let mut fog_density = 0.0;
    let mut tint = None;
    let mut pp_exposure = None;
    let mut pp_bloom_strength = None;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "id" => id = value.trim().to_string(),
            // Nome de exposição no HUD (`zone.name`); texto livre, só aparado.
            "display-name" => display_name = value.trim().to_string(),
            "polygon" => {
                let pairs = value.split(';').filter(|p| !p.trim().is_empty()).count();
                polygon = parse_polygon(&value);
                if polygon.len() < pairs {
                    ctx.warnings.push(format!(
                        "{ctx_tag}: polygon dropped {} malformed pair(s)",
                        pairs - polygon.len()
                    ));
                }
            }
            "fog-density" => fog_density = values::parse_f32(&value, &kctx)?,
            "tint" => tint = Some(values::parse_color(&value, &kctx)?),
            "pp-exposure" => pp_exposure = Some(values::parse_f32(&value, &kctx)?),
            "pp-bloom-strength" => {
                pp_bloom_strength = Some(values::parse_f32(&value, &kctx)?);
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
        destructible: common.destructible,
        kind: EntityKind::BiomeRegion {
            id,
            display_name,
            polygon,
            fog_density,
            tint,
            pp_exposure,
            pp_bloom_strength,
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
        destructible: common.destructible,
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
        destructible: common.destructible,
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

/// `<Sky>` — procedural sky parameters kept as raw attrs; o domo é
/// construído no fim do startup (`spawn.rs` → `sky::build_sky`) e o shader
/// WGSL especializado por mundo é escrito pelo `run()` em `main.rs`.
fn finish_sky(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let _ = rest;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
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
        destructible: common.destructible,
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

/// `<UiStyle>`: the stylesheet source is the element's text, or the file named
/// by `src` (resolved at spawn time against the world dir).
fn finish_ui_style(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_ignored(
        node,
        rest.into_iter().filter(|(key, _)| key != "src").collect(),
        ctx,
    );
    let source = match node.attr("src") {
        Some(src) => format!("@{}", src.trim()),
        None => node.text.clone(),
    };
    if source.trim().is_empty() {
        ctx.warnings
            .push(format!("<{}>: empty stylesheet", node.tag));
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::UiStyle { source },
        children: Vec::new(),
    })
}

/// `<UiRoot>`: unlike every other tag, the whole subtree is carried through —
/// the UI builder needs the XML, not a flattened `EntitySpec` list.
fn finish_ui_tree(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, _rest) = parse_common(node, ctx)?;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::UiTree {
            node: Box::new(node.clone()),
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
        destructible: common.destructible,
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
                // Authored extent: the heightmap file no longer overrides it.
                spec.extent_authored = true;
            }
            "max-height" => {
                spec.max_height = values::parse_f32(&value, &kctx)?;
                if spec.max_height <= 0.0 {
                    bail!("{kctx}: must be positive");
                }
                spec.extent_authored = true;
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
            "height-smoothing" => spec.height_smoothing = values::parse_f32(&value, &kctx)?,
            "collision-resolution" => {
                spec.collision_resolution = values::parse_u32(&value, &kctx)?;
            }
            "resolution" => spec.resolution = values::parse_u32(&value, &kctx)?.max(1),
            "texture" | "texture-url" => spec.texture = Some(value.trim().to_string()),
            "texture-tile-size" => spec.texture_tile_size = values::parse_f32(&value, &kctx)?,
            "layers" => {
                let entries: Vec<String> = value
                    .split(|c: char| c == ',' || c.is_whitespace())
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .collect();
                if entries.len() > crate::terrain::splat::LAYER_COUNT {
                    bail!(
                        "{kctx}: at most {} layers, got {}",
                        crate::terrain::splat::LAYER_COUNT,
                        entries.len()
                    );
                }
                // Canónica (posição = slot, `canonicalize_layers`): pesos do
                // splat e materiais são indexados por slot DEFAULT — a ordem
                // escrita como índice trocava texturas em subconjuntos.
                spec.layers = crate::terrain::splat::canonicalize_layers(&entries);
            }
            "shore-width" => spec.shore_width = values::parse_f32(&value, &kctx)?,
            "splat-texel" => spec.splat_texel = values::parse_f32(&value, &kctx)?,
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
            // Cliff system: trigger angle for the peak-preserving LOD / wall
            // shading, and the opt-in sharpen pass (`src/terrain/cliffs.rs`).
            "cliff-angle" => spec.cliff_angle = values::parse_f32(&value, &kctx)?,
            "sharpen" => spec.sharpen = values::parse_bool(&value, &kctx)?,
            "sharpen-angle" => spec.sharpen_angle = values::parse_f32(&value, &kctx)?,
            "sharpen-seed" => spec.sharpen_seed = values::parse_f32(&value, &kctx)?.max(0.0) as u64,
            "cliff-min-area" => spec.cliff_min_area = values::parse_f32(&value, &kctx)?,
            "cliff-min-drop" => spec.cliff_min_drop = values::parse_f32(&value, &kctx)?,
            "cliff-min-extent" => spec.cliff_min_extent = values::parse_f32(&value, &kctx)?,
            // Pele da parede de cliff (`src/terrain/chunk.wgsl` CFG_*).
            "cliff-streaks" => spec.cliff_streaks = values::parse_f32(&value, &kctx)?,
            "cliff-moss" => spec.cliff_moss = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    spec.validate()
        .map_err(|e| anyhow::anyhow!("{ctx_tag}: {e}"))?;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
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
        destructible: common.destructible,
        kind: EntityKind::TerrainPad { spec },
        children: Vec::new(),
    })
}

fn finish_lake(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    // Filhos `<Island at="x z" radius height/>` — ilhas na bacia (repetível).
    // Outros filhos continuam a ser avisados e ignorados. O `at` da ilha
    // segue o MESMO offset de grupo do lago.
    let mut islands = Vec::new();
    for child in &node.children {
        if child.tag.eq_ignore_ascii_case("island") {
            let kctx = format!("{ctx_tag} <Island>");
            let mut island = crate::terrain::water::IslandSpec::default();
            for (key, value) in &child.attrs {
                match key.as_str() {
                    "at" => island.at = offset_point(values::parse_vec2(value, &kctx)?, off),
                    "radius" => island.radius = values::parse_f32(value, &kctx)?,
                    "height" => island.height = values::parse_f32(value, &kctx)?,
                    other => ctx
                        .warnings
                        .push(format!("{kctx}: ignored attribute `{other}`")),
                }
            }
            if island.radius <= 0.0 {
                bail!("{kctx}: radius must be > 0 (got {})", island.radius);
            }
            islands.push(island);
        } else {
            ctx.warnings.push(format!(
                "{ctx_tag}: child <{}> ignored (only <Island> is supported)",
                child.tag
            ));
        }
    }
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
            "bank" => {
                spec.bank =
                    crate::terrain::water::BankStyle::from_name(&value).ok_or_else(|| {
                        anyhow::anyhow!(
                            "{kctx}: unknown bank style `{value}` \
                             (soft|beach|cliff|terraced|gorge|overhang)"
                        )
                    })?;
            }
            "rocks" => spec.rocks = values::parse_bool(&value, &kctx)?,
            "rocks-density" => {
                spec.rocks_spec.density = values::parse_f32(&value, &kctx)?.clamp(0.01, 1.0)
            }
            "rocks-scale-max" => {
                spec.rocks_spec.scale_max = values::parse_f32(&value, &kctx)?.clamp(0.5, 4.0)
            }
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    // radius/depth ≤ 0 falham o carve silenciosamente e desalinhavam os
    // corpos de água seguintes — rejeita no parse (contrato do path).
    if spec.radius <= 0.0 {
        bail!("{ctx_tag}: radius must be > 0 (got {})", spec.radius);
    }
    if spec.depth <= 0.0 {
        bail!("{ctx_tag}: depth must be > 0 (got {})", spec.depth);
    }
    spec.islands = islands;
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::Lake { spec },
        children: Vec::new(),
    })
}

/// `<GroundDecal at size|radius feather noise texture texture-scale …>` — a
/// draped, feathered ground patch.
///
/// This replaces the migrated `<Plane>` "decals" (a plaza floor, a market
/// apron). A plane is a hard-edged quad at a fixed `y`: on sloped ground it
/// clips and floats, its square corners cut across the terrain, and with no
/// `texture` it renders as a flat white rectangle. A decal drapes, wobbles
/// its rim and fades to zero alpha.
fn finish_ground_decal(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = GroundDecalSpec {
        name: common.name.clone(),
        ..GroundDecalSpec::default()
    };
    let mut seeded = false;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "at" => spec.at = offset_point(values::parse_vec2(&value, &kctx)?, off),
            // Full extent, like <TerrainPad size> — halved into the radii.
            "size" => {
                let v = values::parse_vec2(&value, &kctx)?;
                spec.half_extent = Vec2::new(v[0], v[1]) * 0.5;
            }
            "half-size" => {
                let v = values::parse_vec2(&value, &kctx)?;
                spec.half_extent = Vec2::new(v[0], v[1]);
            }
            "radius" => spec.half_extent = Vec2::splat(values::parse_f32(&value, &kctx)?),
            "feather" | "edge-feather" => spec.feather = values::parse_f32(&value, &kctx)?,
            "noise" | "edge-noise" => spec.noise = values::parse_f32(&value, &kctx)?,
            "seed" => {
                spec.seed = values::parse_f32(&value, &kctx)?.max(0.0) as u32;
                seeded = true;
            }
            "texture" | "texture-url" => spec.texture = Some(value.trim().to_string()),
            "texture-scale" | "texture-tile-size" => {
                spec.texture_scale = values::parse_f32(&value, &kctx)?
            }
            "base-color" => spec.base_color = values::parse_color(&value, &kctx)?,
            "roughness" => spec.roughness = values::parse_f32(&value, &kctx)?,
            "lift" => spec.lift = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if spec.half_extent.x <= 0.0 || spec.half_extent.y <= 0.0 {
        bail!(
            "{ctx_tag}: size/radius must be > 0 (got {} x {})",
            spec.half_extent.x,
            spec.half_extent.y
        );
    }
    if !seeded {
        // Position-derived seed: stable across runs, distinct per decal.
        spec.seed = spec.at.x.to_bits() ^ spec.at.y.to_bits().rotate_left(16);
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::GroundDecal { spec },
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
            "bank" => {
                spec.bank =
                    crate::terrain::water::BankStyle::from_name(&value).ok_or_else(|| {
                        anyhow::anyhow!(
                            "{kctx}: unknown bank style `{value}` \
                             (soft|beach|cliff|terraced|gorge|overhang)"
                        )
                    })?;
            }
            "rocks" => spec.rocks = values::parse_bool(&value, &kctx)?,
            "rocks-density" => {
                spec.rocks_spec.density = values::parse_f32(&value, &kctx)?.clamp(0.01, 1.0)
            }
            "rocks-scale-max" => {
                spec.rocks_spec.scale_max = values::parse_f32(&value, &kctx)?.clamp(0.5, 4.0)
            }
            "pool-spacing" => spec.pool_spacing = values::parse_f32(&value, &kctx)?.max(0.0),
            "cascades" => spec.cascades = values::parse_bool(&value, &kctx)?,
            "waterfalls" => spec.waterfalls = values::parse_bool(&value, &kctx)?,
            "waterfall-min-drop" => {
                spec.waterfall_min_drop = values::parse_f32(&value, &kctx)?
                    .clamp(crate::terrain::water::CASCADE_DROP, 20.0)
            }
            "waterfall-notch" => spec.waterfall_notch = values::parse_bool(&value, &kctx)?,
            "spring" => spec.spring = values::parse_bool(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    // width/depth ≤ 0 falham o carve silenciosamente — rejeita no parse.
    if spec.width <= 0.0 {
        bail!("{ctx_tag}: width must be > 0 (got {})", spec.width);
    }
    if spec.depth <= 0.0 {
        bail!("{ctx_tag}: depth must be > 0 (got {})", spec.depth);
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::River { spec },
        children: Vec::new(),
    })
}

/// `<Cliff path width height angle profile side noise seed>` — a procedural
/// wall face along a crest polyline (`src/terrain/cliffs.rs`).
///
/// The heightfield is 2.5D, so the face stays at or below vertical: `concave`
/// leans the wall back toward the toe (quarry look) instead of overhanging.
fn finish_cliff(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = CliffSpec::default();
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "path" => {
                spec.path = offset_path(values::parse_vec2_list(&value, &kctx)?, off);
                if spec.path.len() < 2 {
                    bail!("{kctx}: a cliff needs at least 2 points (x z pairs)");
                }
            }
            "width" => spec.width = values::parse_f32(&value, &kctx)?,
            "height" => spec.height = Some(values::parse_f32(&value, &kctx)?),
            "angle" => spec.angle = Some(values::parse_f32(&value, &kctx)?),
            "profile" => {
                spec.profile = CliffProfile::parse(&value).ok_or_else(|| {
                    anyhow::anyhow!(
                        "{kctx}: unknown profile `{value}` (vertical|concave|convex|columnar|terraced|overhang|arch)"
                    )
                })?;
            }
            "side" => {
                spec.side = CliffSide::parse(&value).ok_or_else(|| {
                    anyhow::anyhow!("{kctx}: unknown side `{value}` (auto|left|right)")
                })?;
            }
            "noise" => spec.noise = values::parse_f32(&value, &kctx)?,
            "gullies" => spec.gullies = values::parse_f32(&value, &kctx)?.clamp(0.0, 0.6),
            "notches" => spec.notches = values::parse_f32(&value, &kctx)?.clamp(0.0, 0.5),
            "talus" => spec.talus = values::parse_bool(&value, &kctx)?,
            "talus-angle" => spec.talus_angle = values::parse_f32(&value, &kctx)?,
            "seed" => spec.seed = values::parse_f32(&value, &kctx)?.max(0.0) as u64,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if spec.path.len() < 2 {
        bail!("{ctx_tag}: `path` with at least 2 points is required");
    }
    // A degenerate width fails the carve silently — reject at parse, unless
    // height+angle were authored (they derive the width in the carve).
    if spec.width <= 0.0 && (spec.height.is_none() || spec.angle.is_none()) {
        bail!(
            "{ctx_tag}: width must be > 0 (got {w}) — or author both height and angle",
            w = spec.width
        );
    }
    if let Some(a) = spec.angle {
        if a <= 0.0 {
            bail!("{ctx_tag}: angle must be > 0 (got {a})");
        }
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::Cliff { spec },
        children: Vec::new(),
    })
}

/// `<Cave>` — a tunnel through the terrain (`src/terrain/voxel/cave.rs`).
///
/// `path` is XZ like every other ground feature; the tunnel takes its height
/// from the terrain and sits `depth` below the surface. Unlike `<Cliff>`, this
/// is not a carve — nothing is written to the heightfield. It is a subtractive
/// solid in the voxel field, which is why there can be rock above you inside.
fn finish_cave(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = CaveSpec {
        name: common.name.clone(),
        ..CaveSpec::default()
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "path" => {
                spec.path = offset_path(values::parse_vec2_list(&value, &kctx)?, off);
            }
            "radius" => {
                let radii = values::parse_f32_list(&value, &kctx)?;
                if radii.is_empty() {
                    bail!("{ctx_tag}: `radius` needs at least one number");
                }
                spec.radius = radii;
            }
            "depth" => spec.depth = values::parse_f32(&value, &kctx)?,
            "open-ends" => spec.open_ends = values::parse_bool(&value, &kctx)?,
            "mouth-flare" => spec.mouth_flare = values::parse_f32(&value, &kctx)?,
            "mouth-fraction" => spec.mouth_fraction = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    // Rooms and chimneys hang off the tunnel, the way `<Island>` hangs off a
    // `<Lake>`: repeated children, each one a solid of its own.
    for child in &node.children {
        if child.tag.eq_ignore_ascii_case("chamber") {
            spec.chambers.push(parse_cave_chamber(child, off, ctx)?);
        } else if child.tag.eq_ignore_ascii_case("shaft") {
            spec.shafts.push(parse_cave_shaft(child, off, ctx)?);
        } else {
            ctx.warnings.push(format!(
                "{ctx_tag}: ignored child <{}> (expected <Chamber> or <Shaft>)",
                child.tag
            ));
        }
    }
    if spec.path.len() < 2 {
        bail!("{ctx_tag}: a cave needs a `path` with at least 2 points (x z pairs)");
    }
    let (r_min, r_max) = (spec.radius_min(), spec.radius_max());
    if r_min <= 0.0 || !r_min.is_finite() || !r_max.is_finite() {
        bail!("{ctx_tag}: radius must be > 0 (got {r_min})");
    }
    if spec.mouth_flare <= 0.0 || !spec.mouth_flare.is_finite() {
        bail!(
            "{ctx_tag}: mouth-flare must be > 0 (got {f})",
            f = spec.mouth_flare
        );
    }
    if spec.mouth_fraction <= 0.0 || spec.mouth_fraction > 0.5 {
        bail!(
            "{ctx_tag}: mouth-fraction must be in (0, 0.5] (got {f})",
            f = spec.mouth_fraction
        );
    }
    // A tunnel shallower than its own radius is a trench, not a cave: it would
    // breach the surface along its whole length and leave no roof.
    if spec.depth < r_max {
        ctx.warnings.push(format!(
            "{ctx_tag}: depth {d} is under the radius {r_max} — the tunnel breaches the surface \
             along its length instead of having a roof",
            d = spec.depth
        ));
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::Cave { spec },
        children: Vec::new(),
    })
}

/// `<Chamber>` inside a `<Cave>` — a room hollowed out on the tunnel's line.
fn parse_cave_chamber(
    node: &XmlNode,
    off: Vec2,
    ctx: &mut ParseCtx,
) -> Result<crate::terrain::voxel::cave::CaveChamberSpec> {
    let mut spec = crate::terrain::voxel::cave::CaveChamberSpec::default();
    let mut authored_at = false;
    for (key, value) in &node.attrs {
        let key = key.to_ascii_lowercase();
        let kctx = format!("<Chamber> {key}");
        match key.as_str() {
            "name" => spec.name = Some(value.clone()),
            "at" => {
                spec.at = offset_point(values::parse_vec2(value, &kctx)?, off);
                authored_at = true;
            }
            "radius" => spec.radius = values::parse_f32(value, &kctx)?,
            "height" => spec.height = values::parse_f32(value, &kctx)?,
            "depth" => spec.depth = Some(values::parse_f32(value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("<Chamber>: ignored attribute `{other}`")),
        }
    }
    if !authored_at {
        bail!("<Chamber>: `at` with an \"x z\" position is required");
    }
    for (key, size) in [("radius", spec.radius), ("height", spec.height)] {
        if size <= 0.0 || !size.is_finite() {
            bail!("<Chamber>: {key} must be > 0 (got {size})");
        }
    }
    Ok(spec)
}

/// `<Shaft>` inside a `<Cave>` — a chimney from the tunnel up to daylight.
fn parse_cave_shaft(
    node: &XmlNode,
    off: Vec2,
    ctx: &mut ParseCtx,
) -> Result<crate::terrain::voxel::cave::CaveShaftSpec> {
    let mut spec = crate::terrain::voxel::cave::CaveShaftSpec::default();
    let mut authored_at = false;
    for (key, value) in &node.attrs {
        let key = key.to_ascii_lowercase();
        let kctx = format!("<Shaft> {key}");
        match key.as_str() {
            "name" => spec.name = Some(value.clone()),
            "at" => {
                spec.at = offset_point(values::parse_vec2(value, &kctx)?, off);
                authored_at = true;
            }
            "radius" => spec.radius = values::parse_f32(value, &kctx)?,
            "depth" => spec.depth = Some(values::parse_f32(value, &kctx)?),
            other => ctx
                .warnings
                .push(format!("<Shaft>: ignored attribute `{other}`")),
        }
    }
    if !authored_at {
        bail!("<Shaft>: `at` with an \"x z\" position is required");
    }
    if spec.radius <= 0.0 || !spec.radius.is_finite() {
        bail!("<Shaft>: radius must be > 0 (got {r})", r = spec.radius);
    }
    Ok(spec)
}

/// `<Arch>` — a free-standing rock portal (`src/terrain/voxel/arch.rs`).
///
/// Like `<Cave>`, this is a solid in the voxel field, not a carve: a column
/// under the opening resolves to TWO solid spans, and the walker chooses
/// which one to stand on. `at` follows the `at` semantics of every ground
/// feature (own `translation` + ancestor groups apply).
fn finish_arch(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = ArchSpec {
        name: common.name.clone(),
        ..ArchSpec::default()
    };
    let mut authored_at = false;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "at" => {
                spec.at = offset_point(values::parse_vec2(&value, &kctx)?, off);
                authored_at = true;
            }
            "path" => spec.path = offset_path(values::parse_vec2_list(&value, &kctx)?, off),
            "width" | "span" => spec.span = Some(values::parse_f32(&value, &kctx)?),
            "height" => spec.height = values::parse_f32(&value, &kctx)?,
            "thickness" => spec.thickness = values::parse_f32(&value, &kctx)?,
            "depth" => spec.depth = values::parse_f32(&value, &kctx)?,
            "yaw" => spec.yaw = values::parse_f32(&value, &kctx)?,
            "spans" => spec.spans = values::parse_u32(&value, &kctx)?,
            "profile" => {
                spec.profile = ArchProfile::parse(&value).ok_or_else(|| {
                    anyhow!(
                        "{kctx}: unknown profile `{value}` (expected one of: {})",
                        ArchProfile::NAMES
                    )
                })?
            }
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    let has_path = spec.path.len() >= 2;
    if authored_at && has_path {
        bail!(
            "{ctx_tag}: use either `at` (one portal) or `path` (feet on their own ground), not both"
        );
    }
    if !authored_at && !has_path {
        bail!("{ctx_tag}: needs `at` with an \"x z\" position, or a `path` of at least 2 points");
    }
    if !spec.path.is_empty() && !has_path {
        bail!("{ctx_tag}: a `path` needs at least 2 points (x z pairs)");
    }
    if let Some(span) = spec.span {
        if span <= 0.0 || !span.is_finite() {
            bail!("{ctx_tag}: width must be > 0 (got {span})");
        }
    }
    for (key, size) in [
        ("height", spec.height),
        ("thickness", spec.thickness),
        ("depth", spec.depth),
    ] {
        if size <= 0.0 || !size.is_finite() {
            bail!("{ctx_tag}: {key} must be > 0 (got {size})");
        }
    }
    if spec.spans == 0 {
        bail!("{ctx_tag}: spans must be >= 1");
    }
    if spec.spans > 1 && !has_path {
        bail!("{ctx_tag}: `spans` needs a `path` to spread the openings along");
    }
    if !spec.yaw.is_finite() {
        bail!("{ctx_tag}: yaw must be a finite number");
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::Arch { spec },
        children: Vec::new(),
    })
}

/// `<Bridge>` — a crossing you can walk on (`src/terrain/voxel/bridge.rs`).
///
/// The volumetric counterpart of `<Road profile="bridge">`: that one draws a
/// ribbon and nothing more, this one puts the deck in the voxel field, where
/// the column trimesh picks it up as collider for free.
fn finish_bridge(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = BridgeSpec {
        name: common.name.clone(),
        ..BridgeSpec::default()
    };
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "path" => spec.path = offset_path(values::parse_vec2_list(&value, &kctx)?, off),
            "width" => spec.width = values::parse_f32(&value, &kctx)?,
            "rise" => spec.rise = values::parse_f32(&value, &kctx)?,
            "thickness" => spec.thickness = values::parse_f32(&value, &kctx)?,
            "spans" => spec.spans = Some(values::parse_u32(&value, &kctx)?),
            "pier-width" => spec.pier_width = values::parse_f32(&value, &kctx)?,
            "parapet" => spec.parapet = values::parse_f32(&value, &kctx)?,
            "clearance" => spec.clearance = values::parse_f32(&value, &kctx)?,
            "style" => {
                spec.style = BridgeStyle::parse(&value).ok_or_else(|| {
                    anyhow!(
                        "{kctx}: unknown style `{value}` (expected one of: {})",
                        BridgeStyle::NAMES
                    )
                })?
            }
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if spec.path.len() < 2 {
        bail!("{ctx_tag}: a bridge needs a `path` with at least 2 points (x z pairs)");
    }
    for (key, size) in [("width", spec.width), ("thickness", spec.thickness)] {
        if size <= 0.0 || !size.is_finite() {
            bail!("{ctx_tag}: {key} must be > 0 (got {size})");
        }
    }
    if !spec.rise.is_finite() {
        bail!(
            "{ctx_tag}: rise must be a finite number (got {r})",
            r = spec.rise
        );
    }
    if spec.spans == Some(0) {
        bail!("{ctx_tag}: spans must be >= 1 (use style=\"natural\" for a bridge with no piers)");
    }
    if spec.pier_width <= 0.0 || !spec.pier_width.is_finite() {
        bail!(
            "{ctx_tag}: pier-width must be > 0 (got {w})",
            w = spec.pier_width
        );
    }
    // A slab thinner than two LOD0 cells vanishes at distance: the mesher has
    // nothing to hang a surface on, so both the deck and its collider blink out
    // as soon as the column coarsens. The default terrain cell is 1 m.
    if spec.thickness < 2.0 {
        ctx.warnings.push(format!(
            "{ctx_tag}: thickness {t} is under two LOD0 cells — the deck thins out at distance; \
             2 m or more keeps it solid at every level",
            t = spec.thickness
        ));
    }
    if spec.style == BridgeStyle::Natural && spec.spans.is_some() {
        ctx.warnings.push(format!(
            "{ctx_tag}: `spans` has no meaning for style=\"natural\" — a rock span has no arcades"
        ));
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::Bridge { spec },
        children: Vec::new(),
    })
}

/// `<RockFeatures>` — seeds arches, caves and rock spans over a region
/// (`src/terrain/voxel/scatter.rs`).
///
/// The tag does not describe a solid: it describes where to look for sites.
/// At bootstrap it resolves into ordinary `<Arch>`/`<Cave>`/`<Bridge>` specs
/// against the carved terrain, which is why `analyze` can only report the
/// field and not its contents — the heightfield does not exist yet at parse
/// time.
fn finish_rock_features(node: &XmlNode, ctx: &mut ParseCtx) -> Result<EntitySpec> {
    let (common, rest) = parse_common(node, ctx)?;
    warn_children(node, ctx);
    let ctx_tag = format!("<{}>", node.tag);
    let off = terrain_offset(&common, node, ctx);
    let mut spec = RockFeaturesSpec {
        name: common.name.clone(),
        ..RockFeaturesSpec::default()
    };
    let mut authored_region = false;
    for (key, value) in rest {
        let kctx = format!("{ctx_tag} {key}");
        match key.as_str() {
            "region" => {
                let corners = values::parse_vec2_list(&value, &kctx)?;
                if corners.len() != 2 {
                    bail!(
                        "{ctx_tag}: `region` needs two corners (\"minX minZ maxX maxZ\"), got {} \
                         point(s)",
                        corners.len()
                    );
                }
                spec.min = offset_point(corners[0], off);
                spec.max = offset_point(corners[1], off);
                authored_region = true;
            }
            "seed" => spec.seed = values::parse_u64(&value, &kctx)?,
            "arches" => spec.arches = values::parse_u32(&value, &kctx)?,
            "caves" => spec.caves = values::parse_u32(&value, &kctx)?,
            "bridges" => spec.bridges = values::parse_u32(&value, &kctx)?,
            "min-slope" => spec.min_slope = values::parse_f32(&value, &kctx)?,
            "max-slope" => spec.max_slope = values::parse_f32(&value, &kctx)?,
            "min-drop" => spec.min_drop = values::parse_f32(&value, &kctx)?,
            "min-rise" => spec.min_rise = values::parse_f32(&value, &kctx)?,
            "spacing" => spec.spacing = values::parse_f32(&value, &kctx)?,
            "clear-of-roads" => spec.clear_of_roads = values::parse_f32(&value, &kctx)?,
            other => ctx
                .warnings
                .push(format!("{ctx_tag}: ignored attribute `{other}`")),
        }
    }
    if !authored_region {
        bail!("{ctx_tag}: `region` with \"minX minZ maxX maxZ\" is required");
    }
    if spec.min.x == spec.max.x || spec.min.y == spec.max.y {
        bail!(
            "{ctx_tag}: `region` has no area ({:?} to {:?})",
            spec.min,
            spec.max
        );
    }
    if spec.spacing <= 0.0 || !spec.spacing.is_finite() {
        bail!("{ctx_tag}: spacing must be > 0 (got {s})", s = spec.spacing);
    }
    if spec.min_slope > spec.max_slope {
        bail!(
            "{ctx_tag}: min-slope {lo} is above max-slope {hi} — no site can pass",
            lo = spec.min_slope,
            hi = spec.max_slope
        );
    }
    if spec.min_rise < 0.0 || !spec.min_rise.is_finite() {
        ctx.warnings.push(format!(
            "{ctx_tag}: min-rise {} makes no sense — clamped to 0",
            spec.min_rise
        ));
        spec.min_rise = 0.0;
    }
    if spec.requested() == 0 {
        ctx.warnings.push(format!(
            "{ctx_tag}: asks for no features — set `arches`, `caves` or `bridges`"
        ));
    }
    // The lattice has one candidate per `spacing` cell, and seeded features
    // keep a `spacing` gap from each other, so a region that small cannot
    // hold what was asked for.
    let cells = (((spec.max.x - spec.min.x) / spec.spacing).abs().floor()
        * ((spec.max.y - spec.min.y) / spec.spacing).abs().floor()) as u32;
    if spec.requested() > cells.max(1) {
        ctx.warnings.push(format!(
            "{ctx_tag}: asks for {n} features but the region only holds ~{cells} sites at \
             spacing {s} — expect fewer",
            n = spec.requested(),
            s = spec.spacing
        ));
    }
    Ok(EntitySpec {
        name: common.name,
        tag: common.tag,
        script: common.script,
        transform: common.transform,
        physics: common.physics,
        destructible: common.destructible,
        kind: EntityKind::RockFeatures { spec },
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
        destructible: common.destructible,
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
        destructible: common.destructible,
        kind: EntityKind::RoadNetwork { spec },
        children: Vec::new(),
    })
}

/// Headless summary used by `viber analyze` and tests.
#[derive(Debug, Default, PartialEq)]
pub struct WorldSummary {
    pub groups: usize,
    /// `<Composition>` roots — objetos inteiros em primitivas.
    pub compositions: usize,
    /// Primitivas descendentes de uma composition (as "partes").
    pub composition_parts: usize,
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
    /// `<Cliff>` carved wall faces.
    pub cliffs: usize,
    pub caves: usize,
    /// `<Arch>` free-standing rock portals.
    pub arches: usize,
    /// `<Bridge>` volumetric crossings.
    pub bridges: usize,
    /// `<RockFeatures>` seeding fields (not the features they resolve into —
    /// that happens at bootstrap, which `analyze` never runs).
    pub rock_fields: usize,
    pub roads: usize,
    pub road_networks: usize,
    /// `<GroundDecal>` draped ground patches.
    pub ground_decals: usize,
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
    /// `<UiStyle>` stylesheets.
    pub ui_stylesheets: usize,
    /// `<UiRoot>` declarative UI trees.
    pub ui_roots: usize,
    /// Elements inside those trees (the whole HUD, in one number).
    pub ui_elements: usize,
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
            + self.compositions
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
        self.terrain_pads
            + self.lakes
            + self.rivers
            + self.cliffs
            + self.caves
            + self.arches
            + self.bridges
            + self.rock_fields
            + self.roads
            + self.road_networks
            + self.ground_decals
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

/// Elements in a `<UiRoot>` subtree, root included.
fn count_ui_elements(node: &XmlNode) -> usize {
    1 + node.children.iter().map(count_ui_elements).sum::<usize>()
}

/// Walk the entity tree and count each kind.
pub fn summarize(world: &ParsedWorld) -> WorldSummary {
    fn walk(specs: &[EntitySpec], in_composition: bool, out: &mut WorldSummary) {
        for spec in specs {
            // Primitivas dentro de uma composition contam duas vezes: como
            // primitivas (entidades) e como partes da composition.
            let mut next_in_composition = in_composition;
            match &spec.kind {
                EntityKind::Group => out.groups += 1,
                EntityKind::Composition { .. } => {
                    out.compositions += 1;
                    next_in_composition = true;
                }
                EntityKind::Use { .. } => {} // expandida em parse-time
                EntityKind::Primitive { .. } => {
                    if in_composition {
                        out.composition_parts += 1;
                    }
                    out.primitives += 1;
                }
                EntityKind::PointLight { .. } => out.point_lights += 1,
                EntityKind::DirectionalLight { .. } => out.directional_lights += 1,
                EntityKind::AmbientLight { .. } => out.has_ambient = true,
                EntityKind::OrbitCamera { .. } => out.cameras += 1,
                EntityKind::Terrain { .. } => out.terrain += 1,
                EntityKind::TerrainPad { .. } => out.terrain_pads += 1,
                EntityKind::Lake { .. } => out.lakes += 1,
                EntityKind::GroundDecal { .. } => out.ground_decals += 1,
                EntityKind::River { .. } => out.rivers += 1,
                EntityKind::Cliff { .. } => out.cliffs += 1,
                EntityKind::Cave { .. } => out.caves += 1,
                EntityKind::Arch { .. } => out.arches += 1,
                EntityKind::Bridge { .. } => out.bridges += 1,
                EntityKind::RockFeatures { .. } => out.rock_fields += 1,
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
                EntityKind::UiStyle { .. } => out.ui_stylesheets += 1,
                EntityKind::UiTree { node } => {
                    out.ui_elements += count_ui_elements(node);
                    out.ui_roots += 1;
                }
                EntityKind::AudioMixer { .. } => out.audio_mixer += 1,
                EntityKind::MusicLayer { .. } => out.music_layers += 1,
                EntityKind::DayCycle { .. }
                | EntityKind::Weather { .. }
                | EntityKind::BiomeRegion { .. }
                | EntityKind::WorldBorder { .. }
                | EntityKind::EngineConfig { .. } => out.world_systems += 1,
            }
            walk(&spec.children, next_in_composition, out);
        }
    }
    let mut out = WorldSummary::default();
    walk(&world.entities, false, &mut out);
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
            text: String::new(),
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

    /// Cliff gate: default ON, attrs `avoid-cliff`/`cliff-margin` fazem parse
    /// (margem negativa satura a 0) e o `Vegetation` partilha os defaults.
    #[test]
    fn test_spawner_cliff_attrs_parse_and_default_on() {
        let mut template = node("GameObject", &[("role", "static")]);
        template.children = vec![node(
            "GLTFLoader",
            &[("url", "/assets/meshes/forest/pine_dark_lod0.glb")],
        )];
        // Default (sem attrs): avoid_cliff ON, margem 2 m.
        let mut spawner = node("StaticSpawner", &[("count", "3")]);
        spawner.children = vec![template.clone()];
        let (spec, _) = parse_one(&spawner).unwrap();
        let EntityKind::StaticSpawner { spec } = spec.kind else {
            panic!("expected static spawner");
        };
        assert!(spec.avoid_cliff, "avoid-cliff is ON by default");
        assert_eq!(spec.cliff_margin, crate::recipes::DEFAULT_CLIFF_MARGIN);

        // Attrs explícitos: opt-out + margem autoral; negativa satura a 0.
        let mut spawner = node(
            "StaticSpawner",
            &[
                ("count", "3"),
                ("avoid-cliff", "0"),
                ("cliff-margin", "-1.5"),
            ],
        );
        spawner.children = vec![template];
        let (spec, _) = parse_one(&spawner).unwrap();
        let EntityKind::StaticSpawner { spec } = spec.kind else {
            panic!("expected static spawner");
        };
        assert!(!spec.avoid_cliff);
        assert_eq!(spec.cliff_margin, 0.0);

        // Vegetation partilha os mesmos defaults.
        let veg = node(
            "Vegetation",
            &[
                ("meshes", "/assets/meshes/vegetation/grass.glb"),
                ("density-per-km2", "100"),
            ],
        );
        let (spec, _) = parse_one(&veg).unwrap();
        let EntityKind::Vegetation { spec } = spec.kind else {
            panic!("expected vegetation");
        };
        assert!(spec.avoid_cliff);
        assert_eq!(spec.cliff_margin, crate::recipes::DEFAULT_CLIFF_MARGIN);
        let group = spec.to_spawner_spec();
        assert!(group.avoid_cliff && group.cliff_margin == crate::recipes::DEFAULT_CLIFF_MARGIN);
    }

    #[test]
    fn test_static_spawner_without_template_warns_but_keeps_spec() {
        let (spec, w) = parse_one(&node("StaticSpawner", &[("count", "5")])).unwrap();
        let EntityKind::StaticSpawner { spec } = spec.kind else {
            panic!("expected static spawner");
        };
        assert!(spec.template_urls.is_empty());
        assert!(w.iter().any(|m| m.contains("no template glTF url")));
        // Default ON: árvores/pedras/props fora do leito das estradas
        // (VibeGame perfis tree/foliage/creature); `avoid-road="0"` liga.
        assert!(spec.avoid_road, "avoid-road defaults ON");
    }

    #[test]
    fn test_destructible_spec_parses_all_fields() {
        let spec = DestructibleSpec::parse(
            "popup-text: Dark Wood; popup-color: #1a3320; preset: leaves; burst-count: 24; \
             hits: 3; spark-on-hit: 1; hit-preset: woodchips; hit-burst-count: 12; \
             shake-on-hit: 1; crack-on-hit: 1; crack-style: vertical; break-style: fall; \
             cut-height: 0.7; range: 3.2",
        );
        assert_eq!(spec.popup_text.as_deref(), Some("Dark Wood"));
        let [r, g, b] = spec.popup_color.expect("popup-color");
        assert!((r - 26.0 / 255.0).abs() < 1e-5);
        assert!((g - 51.0 / 255.0).abs() < 1e-5);
        assert!((b - 32.0 / 255.0).abs() < 1e-5);
        assert_eq!(spec.preset.as_deref(), Some("leaves"));
        assert_eq!(spec.burst_count, Some(24));
        assert_eq!(spec.hits, Some(3));
        assert_eq!(spec.hit_preset.as_deref(), Some("woodchips"));
        assert_eq!(spec.hit_burst_count, Some(12));
        assert!(spec.shake_on_hit);
        assert_eq!(spec.break_style, BreakStyleSpec::Fall);
        assert!((spec.range.unwrap() - 3.2).abs() < 1e-4);
        assert!(spec.resource.is_none());
    }

    #[test]
    fn test_destructible_spec_defaults_are_tolerant() {
        let spec = DestructibleSpec::parse("popup-text: Stone; break-style: shatter");
        assert_eq!(spec.break_style, BreakStyleSpec::Shatter);
        assert_eq!(spec.hits, None, "default aplicado na conversão runtime");
        assert!(!spec.shake_on_hit);
        assert_eq!(spec.range, None);
        // desconhecidas/lixo não quebram nem produzem dados
        let junk = DestructibleSpec::parse("hits: abc; burst-count: -2; range: nan; foo: bar");
        assert_eq!(junk.hits, None);
        assert_eq!(junk.burst_count, None);
        assert_eq!(junk.range, None);
        // default puro: burst
        assert_eq!(
            DestructibleSpec::parse("").break_style,
            BreakStyleSpec::Burst
        );
    }

    #[test]
    fn test_spawner_collects_template_destructible_and_resource_node() {
        let mut destructible = node(
            "GameObject",
            &[
                ("role", "static"),
                (
                    "destructible",
                    "popup-text: Stone; popup-color: #cccccc; preset: dust; burst-count: 22; \
                     hits: 3; hit-preset: rockshards; hit-burst-count: 10; shake-on-hit: 1; \
                     break-style: shatter",
                ),
            ],
        );
        destructible.children = vec![
            node(
                "GLTFLoader",
                &[("url", "/assets/meshes/swamp/moss_rock_lod0.glb")],
            ),
            node("ResourceNode", &[("kind", "stone"), ("yield", "4")]),
        ];
        let mut spawner = node(
            "StaticSpawner",
            &[
                ("count", "8"),
                ("seed", "6105"),
                ("region-min", "-10 0 -10"),
                ("region-max", "10 0 10"),
            ],
        );
        spawner.children = vec![destructible];
        let (spec, w) = parse_one(&spawner).unwrap();
        assert!(
            w.iter().all(|m| !m.contains("destructible")),
            "destructible nunca vira warning: {w:?}"
        );
        let EntityKind::StaticSpawner { spec } = spec.kind else {
            panic!("expected static spawner");
        };
        let template = spec.template_destructible.expect("template destructible");
        assert_eq!(template.popup_text.as_deref(), Some("Stone"));
        assert_eq!(template.hits, Some(3));
        assert_eq!(template.break_style, BreakStyleSpec::Shatter);
        assert_eq!(template.resource, Some(("stone".into(), 4)));
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
                node(
                    "Cliff",
                    &[("path", "0 -10 0 10"), ("height", "18"), ("angle", "60")],
                ),
                node(
                    "Cave",
                    &[("path", "-20 0 20 0"), ("radius", "3"), ("depth", "10")],
                ),
                node(
                    "Bridge",
                    &[("path", "-20 8 20 8"), ("width", "6"), ("thickness", "2")],
                ),
                node(
                    "RockFeatures",
                    &[("region", "-50 -50 50 50"), ("arches", "2")],
                ),
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
                caves: 1,
                compositions: 0,
                composition_parts: 0,
                arches: 0,
                bridges: 1,
                rock_fields: 1,
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
                cliffs: 1,
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
                ui_stylesheets: 0,
                ui_roots: 0,
                ui_elements: 0,
                audio_mixer: 1,
                music_layers: 1,
                world_systems: 1,
                ground_decals: 0,
            }
        );
        assert_eq!(summary.entities(), 8);
        assert_eq!(summary.ground_features(), 9);
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
    fn test_ground_decal_parses_size_and_defaults() {
        let (spec, _) = parse_one(&node(
            "GroundDecal",
            &[
                ("at", "15 -17"),
                ("size", "14 12.5"),
                ("feather", "3"),
                ("noise", "0.16"),
                ("texture", "/assets/textures/forest_floor/albedo.webp"),
                ("texture-scale", "6"),
            ],
        ))
        .unwrap();
        let EntityKind::GroundDecal { spec } = spec.kind else {
            panic!("expected a GroundDecal");
        };
        assert_eq!(spec.at, Vec2::new(15.0, -17.0));
        // `size` is the full extent — halved into the ellipse radii.
        assert_eq!(spec.half_extent, Vec2::new(7.0, 6.25));
        assert_eq!(spec.feather, 3.0);
        assert!((spec.noise - 0.16).abs() < 1e-6);
        assert_eq!(spec.texture_scale, 6.0);
        assert!(
            spec.lift < crate::terrain::roads::RIBBON_LIFT,
            "decals go under the ribbons"
        );
        assert_ne!(spec.seed, 0, "an unseeded decal seeds from its position");
    }

    #[test]
    fn test_ground_decal_radius_is_a_circle() {
        let (spec, _) = parse_one(&node("GroundDecal", &[("radius", "10.5")])).unwrap();
        let EntityKind::GroundDecal { spec } = spec.kind else {
            panic!("expected a GroundDecal");
        };
        assert_eq!(spec.half_extent, Vec2::splat(10.5));
    }

    #[test]
    fn test_ground_decal_rejects_zero_size() {
        assert!(parse_one(&node("GroundDecal", &[("radius", "0")])).is_err());
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

    /// Voxels de água: gorge/overhang, pools, cascades, spring e ilhas
    /// parseiam (e o vocabulário de bank estende-se sem quebrar o antigo).
    #[test]
    fn test_water_voxel_attrs_parse() {
        let (spec, w) = parse_one(&node(
            "River",
            &[
                ("path", "0 0 20 0 40 5"),
                ("width", "8"),
                ("bank", "gorge"),
                ("pool-spacing", "14"),
                ("cascades", "0"),
                ("waterfalls", "0"),
                ("waterfall-min-drop", "5"),
                ("waterfall-notch", "0"),
                ("spring", "1"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::River { spec } = spec.kind else {
            panic!("expected river");
        };
        assert_eq!(spec.bank, crate::terrain::water::BankStyle::Gorge);
        assert!(spec.bank.is_voxel());
        assert_eq!(spec.pool_spacing, 14.0);
        assert!(!spec.cascades);
        assert!(!spec.waterfalls);
        assert_eq!(spec.waterfall_min_drop, 5.0);
        assert!(!spec.waterfall_notch);
        assert!(spec.spring);

        let (spec, _) = parse_one(&node(
            "Lake",
            &[("at", "10 -10"), ("radius", "18"), ("bank", "overhang")],
        ))
        .unwrap();
        let EntityKind::Lake { spec } = spec.kind else {
            panic!("expected lake");
        };
        assert_eq!(spec.bank, crate::terrain::water::BankStyle::Overhang);
        assert!(spec.islands.is_empty(), "sem filhos: sem ilhas");

        // Ilha como FILHO do lago, com o offset do grupo aplicado ao `at`.
        let mut lake = node(
            "Lake",
            &[("at", "10 -10"), ("radius", "18"), ("depth", "2.4")],
        );
        lake.children.push(node(
            "Island",
            &[("at", "12 -8"), ("radius", "4.5"), ("height", "1.6")],
        ));
        let (spec, w) = parse_one(&lake).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Lake { spec } = spec.kind else {
            panic!("expected lake");
        };
        assert_eq!(spec.islands.len(), 1);
        let island = &spec.islands[0];
        assert_eq!(island.at, Vec2::new(12.0, -8.0));
        assert_eq!(island.radius, 4.5);
        assert_eq!(island.height, 1.6);

        // bank desconhecido continua a ser erro.
        let bad = node("River", &[("path", "0 0 10 0"), ("bank", "lava")]);
        assert!(parse_one(&bad).is_err());
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
        // Filhos que não sejam `<Island>` continuam avisados (e ignorados).
        let mut lake = node("Lake", &[("at", "0 0")]);
        lake.children = vec![node("Cuboid", &[])];
        let (_, w) = parse_one(&lake).unwrap();
        assert!(
            w.iter().any(|m| m.contains("child <Cuboid> ignored")),
            "{w:?}"
        );
        // E um filho <Island> NÃO avisa (é suportado).
        let mut lake = node("Lake", &[("at", "0 0")]);
        lake.children = vec![node("Island", &[("at", "2 2")])];
        let (_, w) = parse_one(&lake).unwrap();
        assert!(w.is_empty(), "{w:?}");
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

    #[test]
    fn test_biome_region_display_name_present_and_absent() {
        // Com o attr: o nome de exposição viaja no IR, aparado.
        let (spec, w) = parse_one(&node(
            "BiomeRegion",
            &[
                ("id", "dark-forest"),
                ("display-name", "  Floresta Sombria  "),
                ("polygon", "[-56,56;56,56]"),
            ],
        ))
        .unwrap();
        assert!(w.is_empty(), "display-name é attr conhecido: {w:?}");
        let EntityKind::BiomeRegion { display_name, .. } = spec.kind else {
            panic!("expected biome region");
        };
        assert_eq!(display_name, "Floresta Sombria");

        // Sem o attr: campo vazio (a engine cai na tabela de fallback).
        let (spec, w) = parse_one(&node(
            "BiomeRegion",
            &[("id", "desert"), ("polygon", "[56,-56;56,56]")],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::BiomeRegion { display_name, .. } = spec.kind else {
            panic!("expected biome region");
        };
        assert!(display_name.is_empty());
    }
}

/// Composição e protótipos: `<Composition>`, `<Prototype>`, `<Use>`,
/// colisores por parte, `place` e os aliases de compat VibeGame.
#[cfg(test)]
mod composition_tests {
    use super::*;
    use crate::physics::ColliderShape;

    fn node(tag: &str, attrs: &[(&str, &str)]) -> XmlNode {
        XmlNode {
            tag: tag.to_string(),
            attrs: attrs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            text: String::new(),
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
    fn test_composition_defaults_body_fixed_collider_per_part() {
        let mut comp = node("Composition", &[]);
        comp.children = vec![
            node("Box", &[("half-size", "1 0.5 1")]),
            node("PointLight", &[("intensity", "100")]),
        ];
        let (spec, w) = parse_one(&comp).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Composition {
            body,
            collider,
            place,
            seat,
        } = spec.kind
        else {
            panic!("expected composition");
        };
        assert_eq!(body, BodyKind::Fixed, "default é cenário imóvel");
        assert!(collider, "default liga colisores por parte");
        assert!(place.is_none());
        assert!(seat, "default assenta no terreno como os grupos");
        // A parte ganha colisor exato (Auto → derivado da forma no spawn).
        assert!(matches!(
            spec.children[0].physics.collider,
            ColliderShape::Auto
        ));
        // A luz NÃO ganha colisor.
        assert_eq!(spec.children[1].physics.collider, ColliderShape::None);
    }

    #[test]
    fn test_composition_body_none_and_collider_none() {
        let mut comp = node("Composition", &[("body", "none"), ("collider", "none")]);
        comp.children = vec![node("Sphere", &[("radius", "0.4")])];
        let (spec, w) = parse_one(&comp).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Composition { body, collider, .. } = spec.kind else {
            panic!("expected composition");
        };
        assert_eq!(body, BodyKind::None, "none é honrado (bug do VibeGame)");
        assert!(!collider);
        // Sem colisores, a parte não é marcada.
        assert_eq!(spec.children[0].physics.collider, ColliderShape::None);
    }

    #[test]
    fn test_composition_per_part_collider_override_respected() {
        let mut comp = node("Composition", &[]);
        comp.children = vec![node("Sphere", &[("radius", "0.4"), ("collider", "none")])];
        let (spec, _) = parse_one(&comp).unwrap();
        assert_eq!(
            spec.children[0].physics.collider,
            ColliderShape::None,
            "collider=none autoral na parte é respeitado"
        );
    }

    #[test]
    fn test_composition_nested_group_parts_get_colliders() {
        let mut comp = node("Composition", &[]);
        let mut sub = node("Group", &[("translation", "0 2 0")]);
        sub.children = vec![node("Cylinder", &[("radius", "0.2")])];
        comp.children = vec![sub];
        let (spec, _) = parse_one(&comp).unwrap();
        assert!(
            matches!(
                spec.children[0].children[0].physics.collider,
                ColliderShape::Auto
            ),
            "sub-pivots propagam o colisor às suas partes"
        );
    }

    #[test]
    fn test_composition_place_parse() {
        let (spec, w) = parse_one(&node(
            "Composition",
            &[(
                "place",
                "at: 10 20; align-to-terrain: 1; base-y-offset: 0.5",
            )],
        ))
        .unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Composition { place, seat, .. } = spec.kind else {
            panic!("expected composition");
        };
        let place = place.expect("place parsed");
        assert_eq!(place.at, [10.0, 20.0]);
        assert!(place.align_to_terrain);
        assert_eq!(place.base_y_offset, 0.5);
        // Com place explícito o seating auto desliga-se.
        assert!(!seat);
    }

    #[test]
    fn test_composition_seat_none() {
        let (spec, _) = parse_one(&node("Composition", &[("seat", "none")])).unwrap();
        let EntityKind::Composition { seat, .. } = spec.kind else {
            panic!("expected composition");
        };
        assert!(!seat);
    }

    #[test]
    fn test_box_tag_is_cuboid_alias() {
        let (spec, w) = parse_one(&node("Box", &[("half-size", "2 1 0.5")])).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Primitive { shape, .. } = spec.kind else {
            panic!("expected primitive");
        };
        assert!(matches!(shape, Shape::Cuboid { half_size } if half_size == [2.0, 1.0, 0.5]));
    }

    #[test]
    fn test_rotation_three_values_is_euler_radians() {
        // VibeGame composition convention: rotation="x y z" em RADIANOS.
        let (spec, _) = parse_one(&node("Entity", &[("rotation", "0 0 1.5707964")])).unwrap();
        let q = spec.transform.rotation_quat.expect("quat parsed");
        let out = transform::quat_apply(q, [1.0, 0.0, 0.0]);
        assert!(
            (out[1] - 1.0).abs() < 1e-4,
            "π/2 rad em Z manda X para Y: {out:?}"
        );
    }

    #[test]
    fn test_rotation_four_values_still_quat() {
        let (spec, _) = parse_one(&node("Entity", &[("rotation", "0 0 0 1")])).unwrap();
        assert_eq!(spec.transform.rotation_quat, Some([0.0, 0.0, 0.0, 1.0]));
    }

    #[test]
    fn test_primitive_emissive_parsed() {
        let (spec, w) = parse_one(&node("Sphere", &[("emissive", "#ff6622")])).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Primitive { material, .. } = spec.kind else {
            panic!();
        };
        assert_eq!(material.emissive, Some([1.0, 0.4, 0.13333334]));
        // Alias do VibeGame / grafia alternativa.
        let (spec, w) = parse_one(&node("Sphere", &[("emissive-color", "red")])).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Primitive { material, .. } = spec.kind else {
            panic!();
        };
        assert!(material.emissive.is_some());
    }

    #[test]
    fn test_primitive_opacity_parsed_and_clamped() {
        let (spec, w) = parse_one(&node("Box", &[("opacity", "0.25")])).unwrap();
        assert!(w.is_empty(), "{w:?}");
        let EntityKind::Primitive { material, .. } = spec.kind else {
            panic!();
        };
        assert_eq!(material.opacity, Some(0.25));
        let (spec, _) = parse_one(&node("Box", &[("opacity", "7")])).unwrap();
        let EntityKind::Primitive { material, .. } = spec.kind else {
            panic!();
        };
        assert_eq!(material.opacity, Some(1.0), "clamp 0..1");
    }

    #[test]
    fn test_prototype_collected_and_use_expanded_with_overrides() {
        let mut proto = node("Prototype", &[("id", "wall")]);
        let mut comp = node("Composition", &[]);
        comp.children = vec![node("Box", &[("half-size", "2 0.1 0.2")])];
        proto.children = vec![comp];
        let use_a = node(
            "Use",
            &[
                ("prototype", "wall"),
                ("translation", "1 0 2"),
                ("name", "a"),
            ],
        );
        let world = parse_world(&[], &[proto, use_a]).unwrap();
        assert!(world.warnings.is_empty(), "{:?}", world.warnings);
        assert_eq!(world.entities.len(), 1, "protótipo não spawna entidade");
        assert_eq!(world.prototypes.len(), 1);
        assert_eq!(world.prototype_instances.get("wall"), Some(&1));
        let inst = &world.entities[0];
        assert_eq!(inst.name.as_deref(), Some("a"), "override de name");
        assert_eq!(
            inst.transform.translation,
            [1.0, 0.0, 2.0],
            "override de pos"
        );
        assert!(
            matches!(inst.kind, EntityKind::Composition { .. }),
            "a instância é a árvore do protótipo"
        );
        assert!(
            matches!(inst.children[0].physics.collider, ColliderShape::Auto),
            "as marcas de colisor por parte sobrevivem à expansão"
        );
    }

    #[test]
    fn test_use_inside_group_expands_in_place() {
        let mut proto = node("Prototype", &[("id", "p")]);
        proto.children = vec![node("Sphere", &[("radius", "1")])];
        let mut group = node("Group", &[("translation", "5 0 5")]);
        group.children = vec![node("Use", &[("prototype", "p")])];
        let world = parse_world(&[], &[proto, group]).unwrap();
        assert_eq!(world.entities[0].children.len(), 1);
        assert!(matches!(
            world.entities[0].children[0].kind,
            EntityKind::Primitive { .. }
        ));
    }

    #[test]
    fn test_use_unknown_prototype_is_recorded_not_fatal() {
        let world = parse_world(&[], &[node("Use", &[("prototype", "ghost")])]).unwrap();
        assert_eq!(world.entities.len(), 0);
        assert_eq!(world.unknown_prototypes, vec!["ghost".to_string()]);
    }

    #[test]
    fn test_prototype_cycle_is_cut_with_warning() {
        let mut a = node("Prototype", &[("id", "a")]);
        a.children = vec![node("Use", &[("prototype", "b")])];
        let mut b = node("Prototype", &[("id", "b")]);
        b.children = vec![node("Use", &[("prototype", "a")])];
        let seed = node("Use", &[("prototype", "a")]);
        let world = parse_world(&[], &[a, b, seed]).unwrap();
        assert!(
            world
                .warnings
                .iter()
                .any(|w| w.contains("ciclo de protótipos")),
            "{:?}",
            world.warnings
        );
        assert_eq!(world.entities.len(), 0, "a instância em ciclo é descartada");
    }

    #[test]
    fn test_nested_use_chains_resolve() {
        // a instancia b que instancia uma primitiva — dois níveis.
        let mut a = node("Prototype", &[("id", "a")]);
        a.children = vec![node("Use", &[("prototype", "b")])];
        let mut b = node("Prototype", &[("id", "b")]);
        b.children = vec![node("Sphere", &[("radius", "0.3")])];
        let use_a = node("Use", &[("prototype", "a"), ("pos", "9 9 9")]);
        let world = parse_world(&[], &[a, b, use_a]).unwrap();
        let root = &world.entities[0];
        assert!(matches!(root.kind, EntityKind::Primitive { .. }));
        assert_eq!(root.transform.translation, [9.0, 9.0, 9.0]);
        assert_eq!(world.prototype_instances.get("a"), Some(&1));
        assert_eq!(world.prototype_instances.get("b"), Some(&1));
    }

    #[test]
    fn test_prototype_defined_in_included_position_is_found() {
        // Definição DEPOIS do uso na ordem do documento: a passagem 0
        // recolhe tudo antes da expansão.
        let use_early = node("Use", &[("prototype", "late"), ("name", "x")]);
        let mut proto = node("Prototype", &[("id", "late")]);
        proto.children = vec![node("Sphere", &[("radius", "1")])];
        let world = parse_world(&[], &[use_early, proto]).unwrap();
        assert!(
            world.unknown_prototypes.is_empty(),
            "{:?}",
            world.unknown_prototypes
        );
        assert_eq!(world.entities.len(), 1);
    }

    #[test]
    fn test_summarize_counts_compositions_and_parts() {
        let mut comp = node("Composition", &[]);
        comp.children = vec![node("Box", &[]), node("Box", &[]), node("PointLight", &[])];
        let world = parse_world(&[], &[comp]).unwrap();
        let summary = summarize(&world);
        assert_eq!(summary.compositions, 1);
        assert_eq!(summary.composition_parts, 2);
        assert_eq!(summary.primitives, 2);
        assert_eq!(summary.point_lights, 1);
    }
}
