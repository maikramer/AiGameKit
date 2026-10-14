//! `viber bake` — congela a montagem automática de um mundo em artefactos
//! estáticos editáveis (o contrato do ViberEditor).
//!
//! O pipeline normal é one-way: XML declarativo → carve determinístico →
//! entidades. Este módulo é o caminho de volta, EMITIDO UMA VEZ:
//!
//! * `bake/terrain.baked.ahgt` — a grelha u16 FINAL (pós-carve de pads,
//!   lagos, rios, estradas) via [`HeightMapU16::to_ahgt`];
//! * `bake/bake.json` — tudo o que era "automático", agora explícito:
//!   features resolvidas (altura do pad, contorno orgânico do lago,
//!   poliline do rio, ribbons de estrada) + placements por grupo de spawn
//!   com pose final (posição/quaternião/escala) e nomes estáveis;
//! * `world.static.xml` (ao LADO do world.xml original, para herdar o
//!   config.yaml) — spawners `<StaticSpawner>`/`<DynamicSpawner>`
//!   substituídos por `<Group>`s de templates duplicados com a pose de cada
//!   instância; `<Vegetation>` MANTIDO (determinístico, caminho de
//!   instancing próprio); tags de features mantidas (o re-carve sobre a
//!   grelha já carvada é idempotente: pads flatten para os mesmos valores,
//!   água é lower-only, rio é prefix-min) com `height` explícito nos pads.
//!
//! Como corre: uma App headless (`MinimalPlugins`) com o MESMO arranque do
//! jogo (`recipes::spawn::startup` → `terrain::runtime::bootstrap`), sem
//! janela/GPU/áudio. As colocações são recomputadas com a própria
//! [`crate::spawner::compute_placements`] contra o runtime carvado — a
//! promessa de determinismo ("mesma seed, mesmo mundo") é o que faz o bake
//! fiel ao mundo vivo.
//!
//! Limitação conhecida: spawners dentro de `<Prototype>`/`<Use>` expandidos
//! N outras vezes não têm nó XML único para substituir — o bake falha com
//! contagens desiguais em vez de emitir um estático silenciosamente
//! incompleto.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use bevy::app::App;
use bevy::asset::AssetApp;
use bevy::ecs::resource::Resource;
use bevy::ecs::schedule::IntoScheduleConfigs;
use bevy::ecs::world::World;
use bevy::math::{Vec2, Vec3};
use serde::{Deserialize, Serialize};

use crate::recipes::spawn::PendingWorld;
use crate::spawner::{
    PendingSpawnGroups, PlacedInstance, SpawnOccupancy, TerrainSample, compute_placements,
};
use crate::terrain::cliffs::CliffMask;
use crate::terrain::heightmap::{AhgtMeta, HeightMapU16};
use crate::terrain::runtime::TerrainRuntime;
use crate::xml::XmlNode;

/// Nome do ficheiro de documento dentro do dir do bake.
pub const BAKE_JSON: &str = "bake.json";
/// Nome do heightmap carvado dentro do dir do bake.
pub const BAKE_AHGT: &str = "terrain.baked.ahgt";
/// Nome do mundo estático (escrito ao lado do world.xml original).
pub const STATIC_WORLD: &str = "world.static.xml";

// ---------------------------------------------------------------------------
// Documento bake.json (contrato schema 1 — espelhado no ViberEditor)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeDocument {
    pub schema: u32,
    pub source: BakeSource,
    pub terrain: BakeTerrain,
    #[serde(default)]
    pub features: BakeFeatures,
    #[serde(default)]
    pub spawn_groups: Vec<BakeSpawnGroup>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeSource {
    #[serde(default)]
    pub world: String,
    #[serde(default)]
    pub viber_version: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeTerrain {
    #[serde(default)]
    pub world_size: f32,
    #[serde(default)]
    pub max_height: f32,
    /// Heightmap carvado, relativo ao dir do MUNDO (não ao dir do bake).
    #[serde(default)]
    pub heightmap: String,
    #[serde(default)]
    pub source_heightmap: Option<String>,
    #[serde(default)]
    pub layers: Vec<String>,
    #[serde(default)]
    pub spec: BakeTerrainSpec,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeTerrainSpec {
    #[serde(default)]
    pub chunk_size: f32,
    #[serde(default)]
    pub levels: u8,
    #[serde(default)]
    pub resolution: u32,
    #[serde(default)]
    pub collision_resolution: u32,
    #[serde(default)]
    pub render_distance: Option<f32>,
    #[serde(default)]
    pub lod_hysteresis: f32,
    #[serde(default)]
    pub lod_distance_ratio: f32,
    #[serde(default)]
    pub shore_width: f32,
    #[serde(default)]
    pub seed: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeFeatures {
    #[serde(default)]
    pub pads: Vec<BakePad>,
    #[serde(default)]
    pub lakes: Vec<BakeLake>,
    #[serde(default)]
    pub rivers: Vec<BakeRiver>,
    #[serde(default)]
    pub road_networks: Vec<BakeRoadNetwork>,
    #[serde(default)]
    pub roads: Vec<BakeRoad>,
    #[serde(default)]
    pub cliffs: Vec<BakeCliff>,
    #[serde(default)]
    pub arches: Vec<BakeArch>,
    #[serde(default)]
    pub caves: Vec<BakeCave>,
    #[serde(default)]
    pub exclusions: Vec<BakeExclusion>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakePad {
    pub id: String,
    pub at: [f32; 2],
    pub size: [f32; 2],
    #[serde(default)]
    pub falloff: f32,
    #[serde(default)]
    pub corner_radius: f32,
    pub resolved_height: f32,
    #[serde(default)]
    pub explicit_height: bool,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeLake {
    pub id: String,
    pub at: [f32; 2],
    #[serde(default)]
    pub radius: f32,
    #[serde(default)]
    pub depth: f32,
    pub water_level: f32,
    #[serde(default)]
    pub outline: Vec<[f32; 2]>,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeRiver {
    pub id: String,
    pub points: Vec<[f32; 2]>,
    #[serde(default)]
    pub width: f32,
    #[serde(default)]
    pub depth: f32,
    #[serde(default)]
    pub water_level: Option<f32>,
    #[serde(default)]
    pub surface_y: Vec<f32>,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeRoadNetwork {
    pub id: String,
    #[serde(default)]
    pub profile: Option<String>,
    #[serde(default)]
    pub segments: Vec<BakeRoadSegment>,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeRoadSegment {
    #[serde(default)]
    pub a: String,
    #[serde(default)]
    pub b: String,
    #[serde(default)]
    pub polyline: Vec<[f32; 2]>,
    #[serde(default)]
    pub width: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeRoad {
    pub id: String,
    pub points: Vec<[f32; 2]>,
    #[serde(default)]
    pub width: f32,
    #[serde(default)]
    pub profile: Option<String>,
    #[serde(default)]
    pub bridge: bool,
    #[serde(default)]
    pub name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeCliff {
    pub id: String,
    #[serde(default)]
    pub path: Vec<[f32; 2]>,
    #[serde(default)]
    pub width: f32,
    #[serde(default)]
    pub height: Option<f32>,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeArch {
    pub id: String,
    #[serde(default)]
    pub at: [f32; 2],
    #[serde(default)]
    pub path: Vec<[f32; 2]>,
    #[serde(default)]
    pub span: Option<f32>,
    #[serde(default)]
    pub height: f32,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeCave {
    pub id: String,
    #[serde(default)]
    pub path: Vec<[f32; 2]>,
    #[serde(default)]
    pub radius: Vec<f32>,
    #[serde(default)]
    pub depth: f32,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeExclusion {
    pub id: String,
    pub at: [f32; 2],
    pub radius: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeSpawnGroup {
    pub id: String,
    #[serde(default)]
    pub name: Option<String>,
    /// static | dynamic | vegetation
    pub kind: String,
    #[serde(default)]
    pub seed: u64,
    #[serde(default)]
    pub rules: BakeRules,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub instances: Vec<BakeInstance>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeRules {
    #[serde(default)]
    pub count: u32,
    #[serde(default)]
    pub region_min: Option<[f32; 3]>,
    #[serde(default)]
    pub region_max: Option<[f32; 3]>,
    #[serde(default)]
    pub avoid_water: Option<bool>,
    #[serde(default)]
    pub in_water: Option<bool>,
    #[serde(default)]
    pub near_water: Option<bool>,
    #[serde(default)]
    pub avoid_road: Option<bool>,
    #[serde(default)]
    pub avoid_cliff: Option<bool>,
    #[serde(default)]
    pub max_slope_deg: Option<f32>,
    #[serde(default)]
    pub max_slope_attempts: Option<u32>,
    #[serde(default)]
    pub footprint_radius: Option<f32>,
    #[serde(default)]
    pub cluster_count: Option<u32>,
    #[serde(default)]
    pub cluster_radius: Option<f32>,
    #[serde(default)]
    pub max_distance: Option<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BakeInstance {
    pub name: String,
    pub translation: [f32; 3],
    /// Quaternion [x, y, z, w] — a pose final (inclui tilt de
    /// `align-to-terrain`; criaturas já passaram por `stand_upright`).
    pub rotation: [f32; 4],
    #[serde(default)]
    pub yaw_deg: f32,
    pub scale: [f32; 3],
    #[serde(default)]
    pub asset: Option<String>,
}

/// Resumo do que o bake escreveu (stdout do CLI + testes).
#[derive(Debug, Clone, Default)]
pub struct BakeReport {
    pub out_dir: PathBuf,
    pub ahgt_path: PathBuf,
    pub bake_json_path: PathBuf,
    pub static_world_path: Option<PathBuf>,
    /// Ficheiros do estático (o mundo + os includes espelhados em `static/`).
    pub static_files: usize,
    pub groups: usize,
    pub instances: usize,
    pub pads: usize,
    pub lakes: usize,
    pub rivers: usize,
    pub roads: usize,
    pub warnings: Vec<String>,
}

// ---------------------------------------------------------------------------
// Snapshot de features (corre ENTRE startup e bootstrap)
// ---------------------------------------------------------------------------

/// Recolha das specs declarativas antes de o bootstrap as consumir — é daqui
/// que vêm cliffs/arches/caves (sólidos voxel sem registo resolvido no
/// `TerrainRuntime`).
#[derive(Resource, Default)]
struct BakeFeatureSnapshot {
    features: Option<crate::terrain::features::TerrainFeatures>,
}

fn capture_features(world: &mut World) {
    if let Some(pending) = world.get_resource::<crate::recipes::spawn::PendingTerrain>() {
        world.resource_mut::<BakeFeatureSnapshot>().features = Some(pending.features.clone());
    }
}

/// Um grupo com as suas instâncias resolvidas + o nó XML do spawner
/// (posição na walk de spawners, para a substituição no estático).
struct ResolvedGroup {
    /// Índice do nó XML correspondente em `collect_spawner_nodes` (a walk
    /// pré-order por documento é a MESMA que recolheu os grupos). `None` =
    /// grupo gerado pela engine (pedras de margem), emitido à parte.
    xml_index: Option<usize>,
    kind: GroupKind,
    name: Option<String>,
    spec: crate::recipes::StaticSpawnerSpec,
    instances: Vec<PlacedInstance>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum GroupKind {
    Static,
    Dynamic,
    Vegetation,
}

impl GroupKind {
    fn as_str(self) -> &'static str {
        match self {
            GroupKind::Static => "static",
            GroupKind::Dynamic => "dynamic",
            GroupKind::Vegetation => "vegetation",
        }
    }
}

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

/// O mundo estático (o canónico) da pasta `world_dir`, se já foi gerado.
pub fn static_world_in(world_dir: &Path) -> Option<PathBuf> {
    let path = world_dir.join(STATIC_WORLD);
    path.is_file().then_some(path)
}

/// Recusa gerar por cima de um estático existente sem `force`: o estático é a fonte da
/// verdade (agentes e o editor editam-no) e regenerar apaga essas edições. Também recusa
/// "bakear" um mundo que já é estático.
pub fn check_bake_target(world_path: &Path, force: bool) -> Result<()> {
    let doc = crate::xml::parse_file(world_path)?;
    let is_static = doc
        .root_attrs
        .iter()
        .any(|(key, value)| key == "static" && matches!(value.as_str(), "true" | "1"));
    if is_static {
        anyhow::bail!(
            "{} já é um mundo estático — o bake gera-o a partir do mundo procedural (world.xml)",
            world_path.display()
        );
    }
    let dir = world_path.parent().unwrap_or(Path::new("."));
    if !force && let Some(existing) = static_world_in(dir) {
        anyhow::bail!(
            "{} já existe e é a fonte da verdade do mundo (edições de agentes/editor vivem lá). \
             `viber bake --force` regenera-o a partir de {} e PERDE essas edições",
            existing.display(),
            world_path.display()
        );
    }
    Ok(())
}

/// Corre o bake completo de um mundo.
///
/// `out_dir` é o diretório do bake (default `<world_dir>/bake`); o mundo
/// estático é sempre escrito ao lado do `world.xml` original.
///
/// # Errors
/// Falha quando o mundo não parseia, quando o bootstrap não publica o
/// runtime, ou quando o alinhamento spawner↔XML é impossível (spawners
/// dentro de `<Prototype>` expandido — ver limite no doc do módulo).
pub fn run_bake(world_path: &Path, out_dir: &Path) -> Result<BakeReport> {
    let world_path = &std::path::absolute(world_path)?;
    let world_dir = world_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let config = crate::config::load(&world_dir)?;
    let loaded = crate::xml::include::load_world(world_path)?;
    let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes)?;

    let mut app = headless_app(world_path, &config, parsed)?;
    app.add_systems(
        bevy::app::Startup,
        (
            crate::recipes::spawn::startup,
            capture_features,
            crate::terrain::runtime::bootstrap,
        )
            .chain(),
    );
    // O assentamento e a colocação do jogo, uma vez, sobre o terreno carvado — as poses
    // que daqui saem são escritas no estático (o runtime estático não assenta nada).
    app.add_systems(
        bevy::app::Update,
        (
            crate::worldsys::seat_statics_once,
            crate::worldsys::resolve_pending_place,
        )
            .chain(),
    );
    app.update();
    let settled: Vec<SettledPose> = {
        let world = app.world_mut();
        let mut query = world.query::<(
            &crate::scene::SceneSource,
            &bevy::transform::components::Transform,
            &bevy::transform::components::GlobalTransform,
        )>();
        query
            .iter(world)
            .map(|(source, transform, global)| (source.0.clone(), *transform, *global))
            .collect()
    };

    let runtime = app
        .world_mut()
        .remove_resource::<TerrainRuntime>()
        .context("o bootstrap do terreno não publicou o TerrainRuntime")?;
    let cliffs = app.world_mut().remove_resource::<CliffMask>();
    let pending = app
        .world_mut()
        .remove_resource::<PendingSpawnGroups>()
        .context("o arranque não recolheu grupos de spawn")?;
    let snapshot = app
        .world_mut()
        .remove_resource::<BakeFeatureSnapshot>()
        .expect("snapshot inserido na headless_app");

    // ---- colocações (a MESMA passada do jogo, sem gate de assets) ----
    // Grupos com `fixed_candidates` são gerados pela ENGINE (pedras de
    // margem de `<Lake rocks>`/`<River rocks>`) — não têm nó XML; alinham-se
    // à parte (xml_index = None) e são emitidos como Groups sintéticos.
    let spawner_nodes = collect_spawner_nodes(&loaded.nodes);
    let mut xml_cursor = 0usize;
    let mut groups: Vec<ResolvedGroup> = Vec::with_capacity(pending.groups.len());

    let mut occupancy = SpawnOccupancy::new();
    for exclusion in &pending.exclusions {
        occupancy.register(exclusion.center.x, exclusion.center.y, exclusion.radius);
    }

    let mut total_instances = 0usize;
    for (index, group) in pending.groups.iter().enumerate() {
        let engine_generated = !group.spec.fixed_candidates.is_empty();
        let (xml_index, kind, name) = if engine_generated {
            (None, GroupKind::Static, Some(format!("engine-{index}")))
        } else {
            let node = spawner_nodes.get(xml_cursor).ok_or_else(|| {
                anyhow::anyhow!(
                    "desalinhamento spawner↔XML: mais grupos ({}) do que nós de spawner ({}) \
                     — spawners dentro de <Prototype>/<Use> expandidos ainda não são bakeáveis",
                    pending.groups.len(),
                    spawner_nodes.len()
                )
            })?;
            let kind = match node.tag.to_ascii_lowercase().as_str() {
                "dynamicspawner" => GroupKind::Dynamic,
                "vegetation" => GroupKind::Vegetation,
                _ => GroupKind::Static,
            };
            let slot = xml_cursor;
            xml_cursor += 1;
            (Some(slot), kind, node.attr("name").map(str::to_string))
        };
        let (instances, _stats) = {
            let near_radius = group.spec.near_water_radius;
            let cliff_clearance = group.spec.cliff_margin.max(0.0)
                + group.spec.footprint_radius.max(0.0)
                    * group.spec.scale_min.max(group.spec.scale_max).max(0.0);
            let mut sample = |x: f32, z: f32| {
                terrain_sample(
                    &runtime,
                    cliffs.as_ref(),
                    x,
                    z,
                    near_radius,
                    cliff_clearance,
                )
            };
            let (mut instances, stats) =
                compute_placements(&group.spec, &mut occupancy, &mut sample);
            if group.dynamic {
                crate::spawner::stand_upright(&mut instances);
            }
            (instances, stats)
        };
        total_instances += instances.len();
        groups.push(ResolvedGroup {
            xml_index,
            kind,
            name,
            spec: group.spec.clone(),
            instances,
        });
    }
    if xml_cursor != spawner_nodes.len() {
        anyhow::bail!(
            "desalinhamento spawner↔XML: {xml_cursor} grupos atribuídos a {} nós de spawner \
             — spawners dentro de <Prototype>/<Use> expandidos ainda não são bakeáveis \
             (ver src/bake.rs)",
            spawner_nodes.len()
        );
    }

    // ---- artefactos ----
    std::fs::create_dir_all(out_dir).with_context(|| format!("criar {}", out_dir.display()))?;

    let mut report = BakeReport {
        out_dir: out_dir.to_path_buf(),
        ..BakeReport::default()
    };

    // 1. terrain.baked.ahgt — a grelha final + o meta do mundo (worldSize/
    // maxHeight autoritativos + cunhas de bioma, preservados pelo spec).
    let grid = &runtime.grid;
    let map = HeightMapU16 {
        width: grid.width(),
        depth: grid.depth(),
        data: grid.raw().to_vec(),
    };
    let meta = AhgtMeta {
        world_size: Some(runtime.spec.world_size),
        max_height: Some(runtime.spec.max_height),
        biomes: runtime.spec.biomes.clone(),
    };
    let ahgt_bytes = map.to_ahgt(&meta)?;
    let ahgt_path = out_dir.join(BAKE_AHGT);
    std::fs::write(&ahgt_path, &ahgt_bytes)
        .with_context(|| format!("escrever {}", ahgt_path.display()))?;
    report.ahgt_path = ahgt_path.clone();
    // 1b. A camada de PINTEL segue o heightmap: o mesmo sidecar do runtime
    // (`` `<ahgt>`.paint ``) — re-bakear preserva a pintura do mundo.
    if !runtime.paint.is_empty() {
        runtime
            .paint
            .save(&ahgt_path)
            .with_context(|| "escrever a camada de pintura")?;
    }

    // 2. bake.json
    let doc = build_bake_document(
        world_path,
        &runtime,
        snapshot.features.as_ref(),
        &groups,
        &pending,
    );
    let bake_json_path = out_dir.join(BAKE_JSON);
    std::fs::write(
        &bake_json_path,
        serde_json::to_string_pretty(&doc).context("serializar bake.json")?,
    )
    .with_context(|| format!("escrever {}", bake_json_path.display()))?;
    report.bake_json_path = bake_json_path;

    // 3. O mundo estático: `world.static.xml` + o espelho modular em `static/`.
    let mut warnings = Vec::new();
    let mut static_doc = build_static_scene(
        world_path,
        out_dir,
        &loaded,
        &runtime,
        &groups,
        &settled,
        &mut warnings,
    )?;
    static_doc.save().context("gravar o mundo estático")?;
    report.static_world_path = Some(static_doc.files[0].path.clone());
    report.static_files = static_doc.files.len();
    report.warnings.extend(warnings);

    report.groups = groups.len();
    report.instances = total_instances;
    report.pads = runtime.pads.len();
    report.lakes = runtime.water.iter().filter(|w| w.radius > 0.0).count();
    report.rivers = runtime.water.iter().filter(|w| w.radius == 0.0).count();
    report.roads = runtime.roads.len();
    Ok(report)
}

/// App mínima headless com os recursos que o arranque do jogo espera — o
/// mesmo espírito do teste end-to-end do bootstrap (runtime.rs), com o
/// asset server a registar a fonte multi-root para os loads de template não
/// falharem na criação dos handles (nunca são aguardados).
pub(crate) fn headless_app(
    world_path: &Path,
    config: &crate::config::GameConfig,
    parsed: crate::recipes::ParsedWorld,
) -> Result<App> {
    let world_dir = world_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let asset_roots = config.asset_roots(&world_dir);

    let mut app = App::new();
    app.add_plugins((
        bevy::MinimalPlugins,
        bevy::asset::AssetPlugin::default(),
        bevy::transform::TransformPlugin,
    ));
    crate::meshopt::register_asset_source(&mut app, asset_roots);
    // Asset stores que o arranque panica se não existirem: meshes, imagem,
    // materiais standard + os 3 custom do run (sky/water/terrain-chunk) e
    // glTF (handles de template — loads nunca aguardados). O `init_asset`
    // REGISTA o tipo (o `server.load` panica sem registo); o
    // `init_resource` cria o store que o startup remove/reinsere.
    app.init_asset::<bevy::render::mesh::Mesh>();
    app.init_resource::<bevy::asset::Assets<bevy::render::mesh::Mesh>>();
    app.init_asset::<bevy::pbr::StandardMaterial>();
    app.init_resource::<bevy::asset::Assets<bevy::pbr::StandardMaterial>>();
    app.init_asset::<bevy::image::Image>();
    app.init_resource::<bevy::asset::Assets<bevy::image::Image>>();
    app.init_asset::<crate::sky::SkyMaterial>();
    app.init_resource::<bevy::asset::Assets<crate::sky::SkyMaterial>>();
    app.init_asset::<crate::terrain::water_material::WaterMaterial>();
    app.init_resource::<bevy::asset::Assets<crate::terrain::water_material::WaterMaterial>>();
    app.init_asset::<crate::terrain::layer_material::TerrainChunkMaterial>();
    app.init_resource::<bevy::asset::Assets<crate::terrain::layer_material::TerrainChunkMaterial>>(
    );
    app.init_asset::<bevy::render::storage::ShaderBuffer>();
    app.init_resource::<bevy::asset::Assets<bevy::render::storage::ShaderBuffer>>();
    // HUD declarativo do simple-rpg usa UiText (fontes).
    app.init_asset::<bevy::text::Font>();
    app.init_resource::<bevy::asset::Assets<bevy::text::Font>>();
    app.init_resource::<crate::textures::WorldTiledTextures>();
    // O arranque constrói a UI declarativa (<UiRoot>) — só os RECURSOS do
    // plugin, sem os sistemas de Update (que exigem input/render).
    app.init_resource::<crate::ui::StyleSheet>()
        .init_resource::<crate::ui::runtime::UiRegistry>()
        .init_resource::<crate::ui::UiData>()
        .init_resource::<crate::ui::UiBindWarnings>()
        .init_resource::<crate::ui::runtime::UiClicks>()
        .init_resource::<crate::ui::UiToast>()
        .init_resource::<crate::ui::UiPrompt>()
        .init_resource::<crate::ui::UiScriptState>()
        .init_resource::<crate::ui::UiLists>()
        .init_resource::<crate::ui::UiModalsOpen>()
        .init_resource::<crate::ui::UiTabs>()
        .init_resource::<crate::ui::UiEvents>()
        .init_resource::<crate::ui::script::UiScriptBinds>()
        .init_resource::<crate::ui::widgets::UiFocusedInput>()
        .init_resource::<crate::ui::widgets::UiTooltipLayer>()
        .init_resource::<crate::ui::actions::UiActionOwners>();
    app.init_asset::<bevy::gltf::Gltf>();
    app.init_resource::<bevy::asset::Assets<bevy::gltf::Gltf>>();
    app.insert_resource(config.clone());
    app.insert_resource(PendingWorld {
        world: parsed,
        base_dir: Some(world_dir),
    });
    app.init_resource::<BakeFeatureSnapshot>();
    Ok(app)
}

/// A closure de amostragem do `instantiate_spawn_groups`, replicada — é o
/// contrato de gates (superfície renderizada, gradiente voxel, linha de
/// água EXATA, ribbon de estrada, máscara de cliff dilatada, laje fina).
pub(crate) fn terrain_sample(
    runtime: &TerrainRuntime,
    cliffs: Option<&CliffMask>,
    x: f32,
    z: f32,
    near_radius: f32,
    cliff_clearance: f32,
) -> TerrainSample {
    let height = runtime.sample_mesh_surface(x, z);
    TerrainSample {
        height,
        normal: runtime
            .voxel
            .gradient(&runtime.base(), Vec3::new(x, height, z), 0.5),
        water: runtime.in_water(x, z),
        water_surface: runtime.water_surface_at(x, z),
        near_water: runtime
            .water
            .iter()
            .any(|body| body.distance_to_waterline(Vec2::new(x, z)).abs() <= near_radius),
        road: runtime.on_road(x, z),
        cliff: cliffs.is_some_and(|mask| mask.is_cliff_within(Vec2::new(x, z), cliff_clearance)),
        roof: runtime.has_thin_roof(x, z),
    }
}

/// Walk DFS pré-order (a ordem do `collect_spawn_groups`) apanhando os nós
/// de spawner COM template (vegetation sem `meshes` e spawners sem glTF no
/// subtree não geram grupo e ficam de fora dos dois lados do alinhamento).
fn collect_spawner_nodes(nodes: &[XmlNode]) -> Vec<&XmlNode> {
    let mut out = Vec::new();
    fn walk<'a>(nodes: &'a [XmlNode], out: &mut Vec<&'a XmlNode>) {
        for node in nodes {
            let tag = node.tag.to_ascii_lowercase();
            let has_template = match tag.as_str() {
                "vegetation" => node.attr("meshes").is_some_and(|m| !m.trim().is_empty()),
                "staticspawner" | "dynamicspawner" => subtree_has_gltf(node),
                _ => false,
            };
            if has_template {
                out.push(node);
            }
            walk(&node.children, out);
        }
    }
    walk(nodes, &mut out);
    out
}

fn subtree_has_gltf(node: &XmlNode) -> bool {
    let tag = node.tag.to_ascii_lowercase();
    if matches!(tag.as_str(), "gltfloader" | "gltfscene") && node.attr("url").is_some() {
        return true;
    }
    node.children.iter().any(subtree_has_gltf)
}

// ---------------------------------------------------------------------------
// bake.json
// ---------------------------------------------------------------------------

#[allow(clippy::too_many_lines)]
fn build_bake_document(
    world_path: &Path,
    runtime: &TerrainRuntime,
    features: Option<&crate::terrain::features::TerrainFeatures>,
    groups: &[ResolvedGroup],
    pending: &PendingSpawnGroups,
) -> BakeDocument {
    let spec = &runtime.spec;
    let mut doc = BakeDocument {
        schema: 1,
        source: BakeSource {
            world: world_path.display().to_string(),
            viber_version: env!("CARGO_PKG_VERSION").to_string(),
        },
        terrain: BakeTerrain {
            world_size: spec.world_size,
            max_height: spec.max_height,
            heightmap: format!("bake/{BAKE_AHGT}"),
            source_heightmap: spec.heightmap.clone(),
            layers: spec.layers.clone(),
            spec: BakeTerrainSpec {
                chunk_size: spec.chunk_size,
                levels: spec.levels,
                resolution: spec.resolution,
                collision_resolution: spec.collision_resolution,
                render_distance: spec.render_distance,
                lod_hysteresis: spec.lod_hysteresis,
                lod_distance_ratio: spec.lod_distance_ratio,
                shore_width: spec.shore_width,
                seed: spec.seed,
            },
        },
        ..BakeDocument::default()
    };

    // Pads: altura resolvida; `explicit_height` quando o XML já a autorava.
    for (i, pad) in runtime.pads.iter().enumerate() {
        doc.features.pads.push(BakePad {
            id: format!("pad-{i}"),
            at: [pad.at.x, pad.at.y],
            size: [pad.size.x, pad.size.y],
            falloff: pad.falloff,
            corner_radius: pad.corner_radius,
            resolved_height: pad.height,
            explicit_height: features
                .and_then(|f| f.pads.get(i))
                .is_some_and(|s| s.height.is_some()),
            source: None,
        });
    }

    // Água: lagos (contorno orgânico amostrado a 64 raios) e rios.
    let mut lake_i = 0;
    let mut river_i = 0;
    for body in &runtime.water {
        if body.radius > 0.0 {
            let outline: Vec<[f32; 2]> = (0..64)
                .map(|k| {
                    let theta = k as f32 * (std::f32::consts::TAU / 64.0);
                    let r = body.shape.contour(body.radius, theta);
                    [body.at.x + r * theta.cos(), body.at.y + r * theta.sin()]
                })
                .collect();
            doc.features.lakes.push(BakeLake {
                id: format!("lake-{lake_i}"),
                at: [body.at.x, body.at.y],
                radius: body.radius,
                depth: body.depths.first().copied().unwrap_or_default(),
                water_level: body.water_y,
                outline,
                source: None,
            });
            lake_i += 1;
        } else {
            doc.features.rivers.push(BakeRiver {
                id: format!("river-{river_i}"),
                points: body.stations.iter().map(|p| [p.x, p.y]).collect(),
                width: body.water_width,
                depth: body.depths.first().copied().unwrap_or_default(),
                water_level: Some(body.water_y),
                surface_y: body.surface_y.clone(),
                source: None,
            });
            river_i += 1;
        }
    }

    // Estradas: ribbons carvadas (standalone + redes expandidas), com
    // fidelidade total de estações; as redes ficam como resumo por spec.
    for (i, road) in runtime.roads.iter().enumerate() {
        let width = road.half_width.iter().cloned().fold(0.0f32, f32::max) * 2.0;
        doc.features.roads.push(BakeRoad {
            id: format!("road-{i}"),
            points: road.stations.iter().map(|p| [p.x, p.y]).collect(),
            width,
            profile: Some(format!("{:?}", road.profile)),
            bridge: road.bridge,
            name: road.name.clone(),
        });
    }
    if let Some(features) = features {
        for (i, net) in features.networks.iter().enumerate() {
            doc.features.road_networks.push(BakeRoadNetwork {
                id: format!("net-{i}"),
                profile: Some(format!("{:?}", net.default_profile)),
                segments: net
                    .segments
                    .iter()
                    .map(|seg| BakeRoadSegment {
                        a: seg.a.clone(),
                        b: seg.b.clone(),
                        polyline: Vec::new(),
                        width: seg.width.unwrap_or(0.0),
                    })
                    .collect(),
                source: None,
            });
        }
        // Sólidos voxel (specs declarativas — o voxel field é o runtime).
        for (i, cliff) in features.cliffs.iter().enumerate() {
            doc.features.cliffs.push(BakeCliff {
                id: format!("cliff-{i}"),
                path: cliff.path.iter().map(|p| [p.x, p.y]).collect(),
                width: cliff.width,
                height: cliff.height,
                source: None,
            });
        }
        for (i, arch) in features.arches.iter().enumerate() {
            doc.features.arches.push(BakeArch {
                id: format!("arch-{i}"),
                at: [arch.at.x, arch.at.y],
                path: arch.path.iter().map(|p| [p.x, p.y]).collect(),
                span: arch.span,
                height: arch.height,
                source: None,
            });
        }
        for (i, cave) in features.caves.iter().enumerate() {
            doc.features.caves.push(BakeCave {
                id: format!("cave-{i}"),
                path: cave.path.iter().map(|p| [p.x, p.y]).collect(),
                radius: cave.radius.clone(),
                depth: cave.depth,
                source: None,
            });
        }
    }

    for (i, exclusion) in pending.exclusions.iter().enumerate() {
        doc.features.exclusions.push(BakeExclusion {
            id: format!("excl-{i}"),
            at: [exclusion.center.x, exclusion.center.y],
            radius: exclusion.radius,
        });
    }

    for (index, group) in groups.iter().enumerate() {
        let spec = &group.spec;
        doc.spawn_groups.push(BakeSpawnGroup {
            id: format!("grp-{index}"),
            name: group.name.clone(),
            kind: group.kind.as_str().to_string(),
            seed: spec.seed,
            rules: BakeRules {
                count: spec.count,
                region_min: Some(spec.region_min),
                region_max: Some(spec.region_max),
                avoid_water: Some(spec.avoid_water),
                in_water: Some(spec.in_water),
                near_water: Some(spec.near_water),
                avoid_road: Some(spec.avoid_road),
                avoid_cliff: Some(spec.avoid_cliff),
                max_slope_deg: Some(spec.max_slope_deg),
                max_slope_attempts: Some(spec.max_slope_attempts),
                footprint_radius: Some(spec.footprint_radius),
                cluster_count: Some(spec.cluster_count),
                cluster_radius: Some(spec.cluster_radius),
                max_distance: Some(spec.max_distance),
            },
            instances: group
                .instances
                .iter()
                .enumerate()
                .map(|(i, inst)| BakeInstance {
                    name: format!("inst-grp-{index}-{i:04}"),
                    translation: [inst.position.x, inst.position.y, inst.position.z],
                    rotation: [
                        inst.rotation.x,
                        inst.rotation.y,
                        inst.rotation.z,
                        inst.rotation.w,
                    ],
                    yaw_deg: inst.yaw_deg,
                    scale: [inst.scale.x, inst.scale.y, inst.scale.z],
                    asset: spec.template_urls.get(inst.template_index).cloned(),
                })
                .collect(),
        });
    }
    doc
}

// ---------------------------------------------------------------------------
// world.static.xml
// ---------------------------------------------------------------------------

/// Pasta (relativa ao mundo) do espelho modular dos includes do estático: o
/// `world/village.xml` da fonte vira `static/world/village.xml`, e os `<Include>` do
/// estático apontam para lá (`/static/…`). A pasta do mundo não muda — `config.yaml`,
/// assets, scripts e o heightmap resolvem igual.
pub const STATIC_DIR: &str = "static";

/// Pose final de uma entidade com proveniência, lida da App headless depois do
/// assentamento (`seat_statics_once`) e da colocação (`resolve_pending_place`).
type SettledPose = (
    std::sync::Arc<crate::xml::NodeOrigin>,
    bevy::transform::components::Transform,
    bevy::transform::components::GlobalTransform,
);

/// Constrói o mundo estático como um [`SceneDoc`] — os ficheiros da fonte, um a um (sem
/// achatar includes), editados pelas "ferramentas" do bake:
///
/// 1. spawners (não vegetation) → `<Group name="spawn/…">` de instâncias explícitas;
/// 2. features de terreno com os valores resolvidos (`height` + `pre-carved` nos pads,
///    `water-level(s)` na água, `flatten="0"` nas estradas, `heightmap` carvado);
/// 3. poses assentadas/colocadas escritas no XML (`translation`/`euler` canónicos, `place`
///    removido) — o runtime estático não assenta nada;
/// 4. `<world static="true">` e os caminhos retargetados para `world.static.xml` +
///    [`STATIC_DIR`].
///
/// O mapeamento runtime→XML é feito pela proveniência dos nós (`XmlNode::origin`), não por
/// cursores: cada nó expandido sabe o ficheiro e o caminho de onde veio.
fn build_static_scene(
    world_path: &Path,
    out_dir: &Path,
    loaded: &crate::xml::include::LoadedWorld,
    runtime: &TerrainRuntime,
    groups: &[ResolvedGroup],
    settled: &[SettledPose],
    warnings: &mut Vec<String>,
) -> Result<crate::scene::doc::SceneDoc> {
    use crate::scene::doc::{NodeRef, SceneDoc};

    let mut doc = SceneDoc::load(world_path)?;
    // Referencial mundo de cada nó com entidade (pós-assentamento).
    let globals: std::collections::HashMap<NodeRef, bevy::math::Affine3A> = settled
        .iter()
        .filter_map(|(origin, _, global)| Some((doc.node_ref_for(origin)?, global.affine())))
        .collect();
    let node_of = |doc: &SceneDoc, node: &XmlNode, what: &str| -> Result<NodeRef> {
        node.origin
            .as_ref()
            .and_then(|origin| doc.node_ref_for(origin))
            .ok_or_else(|| anyhow::anyhow!("{what} <{}> sem nó de origem no documento", node.tag))
    };

    // 1. Spawners → Groups de instâncias (por origem: o nó exato no ficheiro exato).
    let spawner_nodes = collect_spawner_nodes(&loaded.nodes);
    let mut engine_groups: Vec<XmlNode> = Vec::new();
    let mut vegetation_index = 0usize;
    let mut scatter_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut pending_outputs: Vec<(NodeRef, XmlNode)> = Vec::new();
    for (group_index, group) in groups.iter().enumerate() {
        if group.kind == GroupKind::Dynamic {
            // Criaturas com IA/respawn são GAMEPLAY, não geração de mundo: o
            // `<DynamicSpawner>` fica e corre em runtime.
            continue;
        }
        if group.kind == GroupKind::Vegetation {
            // Folhagem pré-gerada: binário `.vinst` + `instances` no `<Vegetation>` (os
            // parâmetros ficam como receita para a ferramenta de scatter regenerar).
            let Some(spawner) = group.xml_index.and_then(|index| spawner_nodes.get(index)) else {
                continue;
            };
            let target = node_of(&doc, spawner, "vegetation")?;
            let file = out_dir
                .join("veg")
                .join(format!("{vegetation_index:03}.vinst"));
            vegetation_index += 1;
            crate::vinst::write(&file, &group.instances)?;
            let file = file.canonicalize().unwrap_or(file);
            let rel = file
                .strip_prefix(&doc.root_dir)
                .map(|rel| rel.to_string_lossy().replace('\\', "/"))
                .unwrap_or_else(|_| file.display().to_string());
            doc.node_mut(&target)
                .expect("resolvido")
                .set_attr("instances", rel);
            doc.touch(target.file);
            continue;
        }
        let url_of = |inst: &PlacedInstance| {
            group
                .spec
                .template_urls
                .get(inst.template_index)
                .map(String::as_str)
        };
        let Some(spawner_index) = group.xml_index else {
            // Grupo da engine (pedras de margem): sem nó XML — instâncias nuas por URL.
            let name = group
                .name
                .clone()
                .unwrap_or_else(|| format!("engine-{group_index}"));
            let mut replacement = XmlNode::new("Group").with_attr("name", format!("spawn/{name}"));
            for (i, inst) in group.instances.iter().enumerate() {
                if let Some(url) = url_of(inst) {
                    replacement.push_child(static_instance_node(
                        &XmlNode::new("GltfScene").with_attr("url", url),
                        inst,
                        &format!("inst-grp-{group_index}-{i:04}"),
                    ));
                }
            }
            engine_groups.push(replacement);
            continue;
        };
        let spawner = spawner_nodes
            .get(spawner_index)
            .ok_or_else(|| anyhow::anyhow!("nó do spawner {spawner_index} fora do documento"))?;
        let target = node_of(&doc, spawner, "spawner")?;
        // A receita fica: o spawner vira `<Scatter id>` (a ferramenta `scatter` regenera o
        // resultado) e o grupo gerado entra logo a seguir, com `generated-by`.
        let mut id = group
            .name
            .clone()
            .unwrap_or_else(|| format!("grp-{spawner_index}"));
        if !scatter_ids.insert(id.clone()) {
            id = format!("{id}-{spawner_index}");
            scatter_ids.insert(id.clone());
        }
        let parent_world = parent_world_frame(&doc, &target, &globals);
        let output = generated_group(
            spawner,
            &id,
            &group.instances,
            &group.spec.template_urls,
            parent_world,
            &format!("inst-grp-{group_index}"),
        );
        let recipe = doc.node_mut(&target).expect("resolvido");
        recipe.tag = "Scatter".into();
        recipe.set_attr("id", id);
        doc.touch(target.file);
        pending_outputs.push((target, output));
    }
    let world_root = NodeRef::root(0);
    for group in engine_groups {
        doc.children_mut(&world_root).expect("raiz").push(group);
    }

    // 2. Features com os valores resolvidos — pela ordem de recolha do carve (lagos e rios
    //    por ordem de documento), cada uma no seu nó de origem.
    let mut pads = runtime.pads.iter();
    let mut lakes = runtime
        .water
        .iter()
        .filter(|w| matches!(w.kind, crate::terrain::water::WaterKind::Lake));
    let mut rivers = runtime
        .water
        .iter()
        .filter(|w| matches!(w.kind, crate::terrain::water::WaterKind::River));
    let mut feature_attrs: Vec<(NodeRef, Vec<(&'static str, String)>)> = Vec::new();
    let mut features = Vec::new();
    collect_feature_nodes(&loaded.nodes, &mut features);
    for node in features {
        let attrs: Vec<(&'static str, String)> = match node.tag.to_ascii_lowercase().as_str() {
            "terrainpad" => {
                let pad = pads
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("mais <TerrainPad> do que pads resolvidos"))?;
                vec![
                    ("height", format!("{}", pad.height)),
                    ("pre-carved", "1".into()),
                ]
            }
            "lake" => {
                let body = lakes
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("mais <Lake> do que lagos resolvidos"))?;
                // As pedras de margem saem como instâncias explícitas (grupo `spawn/engine-*`):
                // o estático não as volta a gerar.
                vec![
                    ("water-level", format!("{}", body.water_y)),
                    ("rocks", "0".into()),
                ]
            }
            "river" => {
                let body = rivers
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("mais <River> do que rios resolvidos"))?;
                let levels: Vec<String> = body.surface_y.iter().map(|y| format!("{y}")).collect();
                vec![("water-levels", levels.join(" ")), ("rocks", "0".into())]
            }
            // A grelha já tem o leito carvado — ribbon decal-only segue o leito existente.
            "road" | "roadnetwork" => vec![("flatten", "0".into())],
            "terrain" => vec![("heightmap", format!("bake/{BAKE_AHGT}"))],
            _ => continue,
        };
        feature_attrs.push((node_of(&doc, node, "feature")?, attrs));
    }
    if pads.next().is_some() || lakes.next().is_some() || rivers.next().is_some() {
        anyhow::bail!(
            "features resolvidas sem nó XML correspondente (pads/lagos/rios desalinhados)"
        );
    }
    for (node, attrs) in feature_attrs {
        let target = doc.node_mut(&node).expect("resolvido");
        for (key, value) in attrs {
            target.set_attr(key, value);
        }
        doc.touch(node.file);
    }

    // 3. Poses assentadas/colocadas → XML. Só origens com UMA entidade: os filhos de um
    //    `<Use>` partilham a origem do `<Prototype>` e escrever lá mudava todas as cópias.
    let mut by_origin: std::collections::HashMap<
        &crate::xml::NodeOrigin,
        Vec<&bevy::transform::components::Transform>,
    > = std::collections::HashMap::new();
    for (origin, transform, _) in settled {
        by_origin
            .entry(origin.as_ref())
            .or_default()
            .push(transform);
    }
    let mut pose_ops = Vec::new();
    for (origin, transforms) in by_origin {
        let Some(node) = doc.node_ref_for(origin) else {
            continue;
        };
        let xml = doc.node(&node).expect("resolvido");
        let has_place = xml.attr("place").is_some();
        let [transform] = transforms.as_slice() else {
            if has_place {
                warnings.push(format!(
                    "{}: `place` num nó instanciado várias vezes — mantido",
                    doc.selector(&node)
                ));
            }
            continue;
        };
        let authored = crate::scene::pose::read_pose(xml)?;
        let moved = authored.translation.distance(transform.translation) > 1e-3;
        let turned = authored.rotation.dot(transform.rotation).abs() < 1.0 - 1e-6;
        if !(moved || turned || has_place) {
            continue;
        }
        let pose = crate::scene::pose::Pose {
            translation: transform.translation,
            rotation: transform.rotation,
            scale: authored.scale,
        };
        let parts = crate::scene::pose::PoseParts {
            translation: moved || has_place,
            rotation: turned,
            scale: false,
        };
        let selector = doc.selector(&node);
        let mut op = crate::scene::pose::pose_op(&selector, xml, pose, parts)?;
        if let crate::scene::ops::EditOp::Set { attrs, .. } = &mut op
            && has_place
        {
            attrs.insert("place".into(), None);
        }
        pose_ops.push(op);
    }
    pose_ops.sort_by(|a, b| format!("{a:?}").cmp(&format!("{b:?}")));
    crate::scene::ops::apply_batch(&mut doc, "bake: poses assentadas", &pose_ops)?;

    // 3b. Os grupos gerados entram a seguir às receitas — no FIM, por ordem de documento
    //     decrescente, para não deslocar os caminhos que os passos acima resolveram.
    pending_outputs.sort_by(|a, b| (b.0.file, &b.0.path).cmp(&(a.0.file, &a.0.path)));
    let mut touched = std::collections::BTreeSet::new();
    for (recipe, output) in pending_outputs {
        let parent = recipe.parent().expect("um spawner nunca é raiz");
        let index = recipe.path.last().copied().unwrap_or(0) + 1;
        doc.children_mut(&parent)
            .expect("pai do spawner")
            .insert(index, output);
        touched.insert(recipe.file);
    }
    for file in touched {
        doc.reindex_includes(file);
    }

    // 4. Marca de mundo estático + retarget dos caminhos (todos os ficheiros são escritos).
    doc.files[0].doc.set_root_attr("static", "true");
    retarget_static(&mut doc)?;
    Ok(doc)
}

/// Referencial MUNDO do pai de `node`: sobe pelos pais até um com entidade (Groups,
/// Compositions…); a raiz de um ficheiro incluído continua pelo `<Include>` que o inclui;
/// a raiz do mundo é a identidade.
fn parent_world_frame(
    doc: &crate::scene::doc::SceneDoc,
    node: &crate::scene::doc::NodeRef,
    globals: &std::collections::HashMap<crate::scene::doc::NodeRef, bevy::math::Affine3A>,
) -> bevy::math::Affine3A {
    let mut current = node.clone();
    for _ in 0..256 {
        let parent = match current.parent() {
            Some(parent) if !parent.is_root() => parent,
            _ => {
                // Raiz de um ficheiro: o mundo (identidade) ou o `<Include>` que o carrega.
                let includer = (0..doc.files.len()).find_map(|file| {
                    doc.files[file]
                        .includes
                        .iter()
                        .find(|(_, target)| *target == current.file)
                        .map(|(path, _)| crate::scene::doc::NodeRef::new(file, path.clone()))
                });
                match includer {
                    Some(include) => {
                        // Um include com transform tem entidade (o Group da expansão): o
                        // seu frame mundo já é pai × pose do include — é o referencial dos
                        // filhos de topo do ficheiro incluído.
                        if let Some(frame) = globals.get(&include) {
                            return *frame;
                        }
                        current = include;
                        continue;
                    }
                    None => return bevy::math::Affine3A::IDENTITY,
                }
            }
        };
        if let Some(frame) = globals.get(&parent) {
            return *frame;
        }
        current = parent;
    }
    bevy::math::Affine3A::IDENTITY
}

/// O `<Group name="spawn/{id}" generated-by="{id}">` de um gerador: as instâncias
/// (colocações MUNDO) escritas no referencial `parent_world` do sítio onde o grupo fica, cada
/// uma a partir do filho-template do spawner/scatter que carrega o seu glTF. Partilhado pelo
/// bake e pela ferramenta `scatter` (`crate::scene::tools`).
pub(crate) fn generated_group(
    recipe: &XmlNode,
    id: &str,
    instances: &[PlacedInstance],
    template_urls: &[String],
    parent_world: bevy::math::Affine3A,
    name_prefix: &str,
) -> XmlNode {
    let mut group = XmlNode::new("Group")
        .with_attr("name", format!("spawn/{id}"))
        .with_attr("generated-by", id);
    let to_local = parent_world.inverse();
    let (parent_scale, parent_rotation, _) = parent_world.to_scale_rotation_translation();
    for (i, inst) in instances.iter().enumerate() {
        let url = template_urls.get(inst.template_index).map(String::as_str);
        let template = template_child_for(recipe, url);
        let mut local = inst.clone();
        local.position = to_local.transform_point3(inst.position);
        local.rotation = (parent_rotation.inverse() * inst.rotation).normalize();
        local.scale = inst.scale / parent_scale;
        group.push_child(static_instance_node(
            template,
            &local,
            &format!("{name_prefix}-{i:04}"),
        ));
    }
    group
}

/// Os nós de features de terreno, pela ordem de documento expandido (a da recolha).
fn collect_feature_nodes<'a>(nodes: &'a [XmlNode], out: &mut Vec<&'a XmlNode>) {
    for node in nodes {
        if matches!(
            node.tag.to_ascii_lowercase().as_str(),
            "terrainpad" | "lake" | "river" | "road" | "roadnetwork" | "terrain"
        ) {
            out.push(node);
        }
        collect_feature_nodes(&node.children, out);
    }
}

/// Muda os caminhos do documento da fonte para os do estático: o mundo para
/// `world.static.xml`, cada include para `static/<caminho relativo>`, e reescreve os
/// `<Include src>` para os novos caminhos. Todos os ficheiros ficam marcados para gravar.
fn retarget_static(doc: &mut crate::scene::doc::SceneDoc) -> Result<()> {
    let rel: Vec<String> = (0..doc.files.len())
        .map(|file| doc.relative_path(file))
        .collect();
    if let Some(outside) = rel
        .iter()
        .skip(1)
        .find(|rel| rel.starts_with("..") || Path::new(rel).is_absolute())
    {
        anyhow::bail!(
            "include fora da pasta do mundo ({outside}) — o estático só espelha a pasta do mundo"
        );
    }
    for file in 0..doc.files.len() {
        let includes = doc.files[file].includes.clone();
        for (path, target) in includes {
            let node = crate::scene::doc::NodeRef::new(file, path);
            doc.node_mut(&node)
                .expect("include indexado")
                .set_attr("src", format!("/{STATIC_DIR}/{}", rel[target]));
        }
    }
    let root = doc.root_dir.clone();
    for (file, rel) in rel.iter().enumerate() {
        doc.files[file].path = if file == 0 {
            root.join(STATIC_WORLD)
        } else {
            root.join(STATIC_DIR).join(rel)
        };
        doc.files[file].dirty = true;
    }
    Ok(())
}

/// O filho-template cujo subtree carrega o glTF escolhido para a instância
/// (urls por ordem de documento — a mesma enumeração do parse). Fallback:
/// o primeiro filho.
fn template_child_for<'a>(spawner: &'a XmlNode, url: Option<&str>) -> &'a XmlNode {
    if let Some(url) = url {
        for child in &spawner.children {
            if subtree_has_url(child, url) {
                return child;
            }
        }
    }
    spawner.children.first().unwrap_or(spawner)
}

fn subtree_has_url(node: &XmlNode, url: &str) -> bool {
    if node.attr("url") == Some(url) {
        return true;
    }
    node.children.iter().any(|c| subtree_has_url(c, url))
}

/// Primeiro nó de glTF do subtree (o loader do template).
fn find_gltf_node(node: &XmlNode) -> Option<&XmlNode> {
    let tag = node.tag.to_ascii_lowercase();
    if matches!(tag.as_str(), "gltfloader" | "gltfscene") && node.attr("url").is_some() {
        return Some(node);
    }
    node.children.iter().find_map(find_gltf_node)
}

/// Primeiro `<ResourceNode kind yield>` do subtree (colheita do template).
fn collect_resource(node: &XmlNode) -> Option<(String, u32)> {
    if node.tag.eq_ignore_ascii_case("resourcenode") {
        let kind = node.attr("kind").map(str::to_string)?;
        let amount = node
            .attr("yield")
            .and_then(|v| v.trim().parse::<u32>().ok())
            .unwrap_or(1);
        return Some((kind, amount));
    }
    node.children.iter().find_map(collect_resource)
}

/// Instância estática: um `<GltfScene>` com os attrs universais do template
/// (collider/rigidbody/destructible/script), a ladder de LOD do loader, o
/// resource do `<ResourceNode>` em attrs, e a pose final do placement.
/// `GameObject`/`Creature` ficam fora — são tags de template de spawner,
/// não entidades; o `<GltfScene>` é a forma canónica de GLB solto.
fn static_instance_node(template: &XmlNode, inst: &PlacedInstance, name: &str) -> XmlNode {
    let mut node = XmlNode::new("GltfScene");
    if let Some(gltf) = find_gltf_node(template) {
        if let Some(url) = gltf.attr("url") {
            node.set_attr("url", url);
        }
        for key in [
            "lod1-url",
            "lod2-url",
            "lod-threshold-near",
            "lod-threshold-mid",
        ] {
            if let Some(value) = gltf.attr(key) {
                node.set_attr(key, value);
            }
        }
    }
    for key in ["collider", "rigidbody", "body", "destructible", "script"] {
        if let Some(value) = template.attr(key) {
            node.set_attr(key, value);
        }
    }
    if let Some((kind, amount)) = collect_resource(template) {
        node.set_attr("resource-kind", kind);
        node.set_attr("resource-yield", amount.to_string());
    }
    node.set_attr("name", name);
    // Pose CANÓNICA (a mesma de `scene::pose::pose_op`): a 1.ª edição de um agente ou do
    // editor não reescreve a pose inteira por mudar de forma (quat → euler, 8 → 4 casas).
    node.set_attr(
        "translation",
        crate::scene::pose::format_vec3(inst.position),
    );
    if inst.rotation.dot(bevy::math::Quat::IDENTITY).abs() < 1.0 - 1e-7 {
        let (key, value) = crate::scene::pose::rotation_attr(inst.rotation);
        node.set_attr(key, value);
    }
    node.set_attr("scale", crate::scene::pose::format_scale(inst.scale));
    node
}

// ---------------------------------------------------------------------------
// Verify
// ---------------------------------------------------------------------------

/// Verificação do estático: (1) o mundo estático parseia sem tags
/// desconhecidas; (2) determinismo — os grupos vegetation que sobreviveram
/// no estático recomputam para as MESMAS instâncias do bake.json (prova que
/// o mundo estático não desvia as regras). Devolve as instâncias validadas.
pub fn verify_static(static_path: &Path, bake_path: &Path) -> Result<usize> {
    let static_path = &std::path::absolute(static_path)?;
    let baked: BakeDocument =
        serde_json::from_str(&std::fs::read_to_string(bake_path)?).context("ler bake.json")?;

    let config_dir = static_path.parent().unwrap_or(Path::new("."));
    let config = crate::config::load(config_dir)?;
    let loaded = crate::xml::include::load_world(static_path)?;
    let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes)?;
    if !parsed.skipped_tags.is_empty() {
        anyhow::bail!(
            "o mundo estático introduziu tags desconhecidas: {:?}",
            parsed.skipped_tags
        );
    }

    let mut app = headless_app(static_path, &config, parsed)?;
    app.add_systems(
        bevy::app::Startup,
        (
            crate::recipes::spawn::startup,
            crate::terrain::runtime::bootstrap,
        )
            .chain(),
    );
    app.update();

    let runtime = app
        .world_mut()
        .remove_resource::<TerrainRuntime>()
        .context("bootstrap do estático não publicou runtime")?;
    let cliffs = app.world_mut().remove_resource::<CliffMask>();
    let pending = app
        .world_mut()
        .remove_resource::<PendingSpawnGroups>()
        .context("estático sem grupos (vegetation)")?;

    let mut occupancy = SpawnOccupancy::new();
    for exclusion in &pending.exclusions {
        occupancy.register(exclusion.center.x, exclusion.center.y, exclusion.radius);
    }

    let baked_veg: Vec<&BakeSpawnGroup> = baked
        .spawn_groups
        .iter()
        .filter(|g| g.kind == "vegetation")
        .collect();
    // No estático só sobram spawners vegetation (+ os grupos de engine das
    // pedras de margem, que se re-geram a partir dos lagos — fora do zip).
    let static_veg: Vec<&crate::spawner::SpawnGroupState> = pending
        .groups
        .iter()
        .filter(|g| g.spec.fixed_candidates.is_empty())
        .collect();
    if static_veg.len() != baked_veg.len() {
        anyhow::bail!(
            "estático tem {} grupos vegetation vs {} no bake",
            static_veg.len(),
            baked_veg.len()
        );
    }
    let mut checked = 0usize;
    let mut drift_warnings = 0usize;
    for (group, baked_group) in static_veg.iter().zip(baked_veg.iter()) {
        let near_radius = group.spec.near_water_radius;
        let cliff_clearance = group.spec.cliff_margin.max(0.0)
            + group.spec.footprint_radius.max(0.0)
                * group.spec.scale_min.max(group.spec.scale_max).max(0.0);
        let (instances, _) = {
            let mut sample = |x: f32, z: f32| {
                terrain_sample(
                    &runtime,
                    cliffs.as_ref(),
                    x,
                    z,
                    near_radius,
                    cliff_clearance,
                )
            };
            compute_placements(&group.spec, &mut occupancy, &mut sample)
        };
        if instances.len() != baked_group.instances.len() {
            anyhow::bail!(
                "vegetation '{}': {} instâncias no estático vs {} no bake",
                baked_group.name.as_deref().unwrap_or(&baked_group.id),
                instances.len(),
                baked_group.instances.len()
            );
        }
        // Desvios de POSIÇÃO são esperados e documentados: os spawners
        // estáticos substituídos já não registam discos de ocupação, pelo
        // que a vegetation que vinha depois deles re-rola candidatos (a
        // verdade canónica vive no bake.json). A paridade ESTRUTURAL
        // (contagem por grupo) é a que quebra o mundo.
        for (live, baked_inst) in instances.iter().zip(baked_group.instances.iter()) {
            let dx = live.position.x - baked_inst.translation[0];
            let dy = live.position.y - baked_inst.translation[1];
            let dz = live.position.z - baked_inst.translation[2];
            if dx * dx + dy * dy + dz * dz > 1e-6 {
                drift_warnings += 1;
            }
        }
        checked += instances.len();
    }
    if drift_warnings > 0 {
        bevy::log::info!(
            "bake verify: {drift_warnings} instâncias de vegetation re-rolaram no estático \
             (ocupação dos spawners substituídos) — cosmético, bake.json é a referência"
        );
    }
    Ok(checked)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// Mundo mínimo de bake: terreno procedural + pad + lago + um
    /// StaticSpawner de cubos + vegetation. Sem heightmap externo.
    const MINIMAL_WORLD: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<world>
  <Terrain world-size="128" max-height="40" seed="7" />
  <TerrainPad at="0 0" size="16 16" falloff="6" corner-radius="2" />
  <Lake at="-30 20" radius="8" depth="4" water-offset="0.25" />
  <SpawnExclusion at="10 10" radius="6" />
  <StaticSpawner profile="tree" count="5" seed="42" region-min="-40 0 -40" region-max="40 0 40" avoid-water="1" random-yaw="true">
    <GameObject role="static" destructible="popup-text: Test Wood; hits: 2" rigidbody="type: fixed; mass: 0; gravity-scale: 0" collider="shape: precompute; mesh-url: /assets/meshes/test/cube_lod0.glb">
      <GLTFLoader url="/assets/meshes/test/cube_lod0.glb" lod1-url="/assets/meshes/test/cube_lod1.glb" lod-threshold-near="30" lod-threshold-mid="90" />
      <ResourceNode kind="test-wood" yield="2" />
    </GameObject>
  </StaticSpawner>
  <Vegetation meshes="/assets/meshes/test/grass.glb" density-per-km2="9000" seed="9" region-min="-30 0 -30" region-max="30 0 30" />
</world>
"#;

    fn write_minimal_world() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("world.xml"), MINIMAL_WORLD).unwrap();
        std::fs::create_dir_all(dir.path().join("assets/meshes/test")).unwrap();
        // A forma mínima do CONFIG_YAML_TEMPLATE do scaffold (docs/ASSETS.md).
        std::fs::write(
            dir.path().join("config.yaml"),
            "title: Bake Test\n\
             assets:\n  \
             roots: []\n  \
             bgm_dir: assets/audio/bgm\n  \
             sfx_dir: assets/audio/sfx\n  \
             terrain_textures_dir: assets/textures\n\
             game:\n  \
             scripts_dir: scripts\n\
             save:\n  \
             dir: ~/.local/share/viber\n",
        )
        .unwrap();
        dir
    }

    #[test]
    fn test_bake_document_serializes_roundtrip() {
        let mut doc = BakeDocument::default();
        doc.schema = 1;
        doc.source.world = "world.xml".into();
        doc.terrain.world_size = 4000.0;
        doc.features.pads.push(BakePad {
            id: "pad-0".into(),
            at: [0.0, 0.0],
            size: [120.0, 120.0],
            falloff: 20.0,
            corner_radius: 18.0,
            resolved_height: 46.3,
            explicit_height: false,
            source: Some("world/cities/discordia.xml".into()),
        });
        doc.spawn_groups.push(BakeSpawnGroup {
            id: "grp-0".into(),
            name: Some("forest-pines".into()),
            kind: "static".into(),
            seed: 12,
            rules: BakeRules {
                count: 620,
                region_min: Some([-450.0, 0.0, -450.0]),
                avoid_water: Some(true),
                ..BakeRules::default()
            },
            instances: vec![BakeInstance {
                name: "inst-grp-0-0000".into(),
                translation: [1.0, 46.2, 3.0],
                rotation: [0.0, 0.38, 0.0, 0.92],
                yaw_deg: 45.0,
                scale: [1.0, 1.1, 1.0],
                asset: Some("/assets/meshes/forest/pine_a.glb".into()),
            }],
        });
        let json = serde_json::to_string_pretty(&doc).unwrap();
        let back: BakeDocument = serde_json::from_str(&json).unwrap();
        assert_eq!(back.schema, 1);
        assert_eq!(back.features.pads.len(), 1);
        assert_eq!(back.spawn_groups[0].instances[0].name, "inst-grp-0-0000");
        // O editor tolera campos ausentes (serde default).
        let minimal = serde_json::json!({"schema": 1, "source": {}, "terrain": {}});
        let tolerant: BakeDocument = serde_json::from_value(minimal).unwrap();
        assert!(tolerant.features.pads.is_empty());
    }

    #[test]
    fn test_bake_minimal_world_end_to_end() {
        let dir = write_minimal_world();
        let world = dir.path().join("world.xml");
        let out = dir.path().join("bake");
        let report = run_bake(&world, &out).expect("bake corre");

        assert!(report.ahgt_path.exists(), "ahgt escrito");
        assert!(report.bake_json_path.exists(), "bake.json escrito");
        assert_eq!(
            report.static_world_path.as_deref(),
            Some(dir.path().join(STATIC_WORLD).as_path())
        );

        let doc: BakeDocument =
            serde_json::from_str(&std::fs::read_to_string(&report.bake_json_path).unwrap())
                .unwrap();
        assert_eq!(doc.schema, 1);
        assert_eq!(doc.features.pads.len(), 1, "um pad resolvido");
        assert!(!doc.features.pads[0].explicit_height, "height era auto");
        assert_eq!(doc.features.lakes.len(), 1);
        assert_eq!(doc.features.lakes[0].outline.len(), 64);
        assert_eq!(doc.features.exclusions.len(), 1);
        assert_eq!(doc.spawn_groups.len(), 2, "static + vegetation");
        assert_eq!(doc.spawn_groups[0].kind, "static");
        assert_eq!(doc.spawn_groups[1].kind, "vegetation");
        assert!(
            !doc.spawn_groups[0].instances.is_empty(),
            "cubos colocados fora de água/exclusão"
        );

        // O .ahgt relê e casa com o mundo declarado.
        let bytes = std::fs::read(&report.ahgt_path).unwrap();
        let (map, meta) = HeightMapU16::from_ahgt(&bytes).unwrap();
        assert_eq!(map.width, 128);
        assert_eq!(meta.world_size, Some(128.0));

        // O estático parseia limpo e o vegetation é fiel ao bake.
        let checked =
            verify_static(&dir.path().join(STATIC_WORLD), &report.bake_json_path).expect("verify");
        assert!(checked > 0, "instâncias de vegetation verificadas");

        // O XML estático contém as instâncias explícitas e o heightmap novo.
        let xml = std::fs::read_to_string(dir.path().join(STATIC_WORLD)).unwrap();
        assert!(xml.contains("heightmap=\"bake/terrain.baked.ahgt\""));
        assert!(xml.contains("inst-grp-0-0000"));
        assert!(!xml.contains("<StaticSpawner"));
        assert!(
            xml.contains("<GltfScene"),
            "instâncias como GltfScene soltos"
        );
        assert!(
            xml.contains("resource-kind=\"test-wood\""),
            "resource em attr"
        );
        assert!(xml.contains("lod1-url="), "ladder de LOD preservada");
        assert!(xml.contains("destructible="), "comportamento do template");
        assert!(xml.contains("<Vegetation"), "vegetation mantida");
        let pad_height = doc.features.pads[0].resolved_height;
        assert!(
            xml.contains(&format!("height=\"{pad_height}\"")),
            "pad com height explícito {pad_height}"
        );
    }

    /// O estático é modular (includes espelhados em `static/`), marcado `static="true"`,
    /// com o assentamento e o `place` escritos no XML — e o runtime estático não mexe.
    #[test]
    fn test_bake_static_is_modular_and_settled() {
        let dir = write_minimal_world();
        let world = MINIMAL_WORLD.replace(
            "  <SpawnExclusion",
            "  <Include src=\"/world/props.xml\" />\n  <SpawnExclusion",
        );
        std::fs::write(dir.path().join("world.xml"), world).unwrap();
        std::fs::create_dir_all(dir.path().join("world")).unwrap();
        std::fs::write(
            dir.path().join("world/props.xml"),
            r#"<world>
  <Group name="hut"><Cuboid size="2" translation="0 1 0" />
    <StaticSpawner profile="prop" count="3" seed="5" region-min="-20 0 -20" region-max="20 0 20" avoid-water="1">
      <GameObject role="static"><GLTFLoader url="/assets/meshes/test/rock.glb" /></GameObject>
    </StaticSpawner>
  </Group>
  <Composition name="bench" place="at: 4 -3"><Cuboid size="1" /></Composition>
</world>"#,
        )
        .unwrap();
        let report =
            run_bake(&dir.path().join("world.xml"), &dir.path().join("bake")).expect("bake");
        assert_eq!(report.static_files, 2);

        let entry = std::fs::read_to_string(dir.path().join(STATIC_WORLD)).unwrap();
        assert!(entry.contains("<world static=\"true\">"), "{entry}");
        assert!(
            entry.contains("src=\"/static/world/props.xml\""),
            "include retargetado"
        );
        // A fonte fica intacta (o procedural é o gerador).
        let source = std::fs::read_to_string(dir.path().join("world.xml")).unwrap();
        assert!(source.contains("<StaticSpawner") && !source.contains("static=\"true\""));

        let static_props =
            crate::scene::doc::SceneDoc::load(&dir.path().join(STATIC_WORLD)).unwrap();
        assert_eq!(static_props.relative_path(1), "static/world/props.xml");
        let hut = static_props.resolve("name=hut").unwrap();
        let hut_pose = crate::scene::pose::read_pose(static_props.node(&hut).unwrap()).unwrap();
        assert!(
            hut_pose.translation.y > 1.0,
            "assentado no pad: {:?}",
            hut_pose.translation
        );
        let bench = static_props
            .node(&static_props.resolve("name=bench").unwrap())
            .unwrap();
        assert_eq!(bench.attr("place"), None, "place resolvido");
        let bench_pose = crate::scene::pose::read_pose(bench).unwrap();
        assert_eq!(
            (bench_pose.translation.x, bench_pose.translation.z),
            (4.0, -3.0)
        );

        // Carregado, o estático não assenta nem coloca: a pose do XML é a final.
        let loaded = crate::xml::include::load_world(&dir.path().join(STATIC_WORLD)).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        assert!(parsed.static_world);
        let config = crate::config::load(dir.path()).unwrap();
        let mut app = headless_app(&dir.path().join(STATIC_WORLD), &config, parsed).unwrap();
        app.add_systems(
            bevy::app::Startup,
            (
                crate::recipes::spawn::startup,
                crate::terrain::runtime::bootstrap,
            )
                .chain(),
        );
        app.add_systems(
            bevy::app::Update,
            (
                crate::worldsys::seat_statics_once,
                crate::worldsys::resolve_pending_place,
            )
                .chain(),
        );
        app.update();
        let world = app.world_mut();
        let mut query = world.query::<(
            &bevy::prelude::Name,
            &bevy::transform::components::Transform,
        )>();
        let live_hut = query
            .iter(world)
            .find(|(n, _)| n.as_str() == "hut")
            .unwrap()
            .1
            .translation;
        assert!(
            (live_hut - hut_pose.translation).length() < 1e-3,
            "{live_hut:?} vs {:?}",
            hut_pose.translation
        );

        // Instâncias de um spawner que vivia dentro do grupo assentado: no estático ficam
        // no referencial do pai — a posição MUNDO viva é a colocação do bake.
        let baked: BakeDocument =
            serde_json::from_str(&std::fs::read_to_string(&report.bake_json_path).unwrap())
                .unwrap();
        let mut globals = world.query::<(
            &bevy::prelude::Name,
            &bevy::transform::components::GlobalTransform,
        )>();
        let mut checked = 0;
        for group in baked.spawn_groups.iter().filter(|g| g.kind == "static") {
            for inst in &group.instances {
                let (_, global) = globals
                    .iter(world)
                    .find(|(n, _)| n.as_str() == inst.name)
                    .unwrap_or_else(|| panic!("{} spawnada", inst.name));
                let expected = bevy::math::Vec3::from(inst.translation);
                assert!(
                    (global.translation() - expected).length() < 1e-2,
                    "{}: vivo {:?} vs bake {:?}",
                    inst.name,
                    global.translation(),
                    expected
                );
                checked += 1;
            }
        }
        assert!(
            checked >= 3,
            "instâncias do spawner aninhado verificadas ({checked})"
        );

        // Vegetação pré-gerada: `instances` no nó + o binário com as instâncias do bake, e o
        // runtime estático carrega-as em vez de calcular.
        let veg = static_props.find_by_attr("instances", "bake/veg/000.vinst");
        assert_eq!(veg.len(), 1, "o <Vegetation> aponta para o binário");
        let preplaced = crate::vinst::read(&dir.path().join("bake/veg/000.vinst")).unwrap();
        let baked_veg = baked
            .spawn_groups
            .iter()
            .find(|g| g.kind == "vegetation")
            .unwrap();
        assert_eq!(preplaced.len(), baked_veg.instances.len());
        let pending = world.resource::<crate::spawner::PendingSpawnGroups>();
        let veg_group = pending
            .groups
            .iter()
            .find(|g| g.spec.instances_file.is_some())
            .expect("grupo vegetation");
        assert_eq!(
            veg_group.spec.preplaced.as_ref().map(|p| p.len()),
            Some(preplaced.len())
        );
    }

    /// Ferramenta `scatter` sobre o estático: a receita sobreviveu ao bake, regenerar dá as
    /// MESMAS colocações (determinismo), e as ops inversas repõem o documento.
    #[test]
    fn test_scatter_tool_regenerates_baked_output() {
        let dir = write_minimal_world();
        run_bake(&dir.path().join("world.xml"), &dir.path().join("bake")).expect("bake");
        let static_world = dir.path().join(STATIC_WORLD);

        let doc = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        let scatters = crate::scene::tools::list_scatters(&doc);
        assert_eq!(scatters.as_array().unwrap().len(), 1, "{scatters}");
        let selector = scatters[0]["selector"].as_str().unwrap().to_owned();
        let id = scatters[0]["id"].as_str().unwrap().to_owned();
        let baked_count = scatters[0]["instances"].as_u64().unwrap();
        assert!(baked_count > 0);
        let poses = |doc: &crate::scene::doc::SceneDoc| -> Vec<String> {
            let group = doc.find_by_attr("generated-by", &id)[0].clone();
            doc.children(&group)
                .unwrap()
                .iter()
                .map(|n| n.attr("translation").unwrap_or_default().to_owned())
                .collect()
        };
        let baked = poses(&doc);
        let before = std::fs::read_to_string(&static_world).unwrap();

        let out =
            crate::scene::tools::run_offline(&static_world, &selector, false).expect("scatter");
        assert_eq!(out["tool"]["placed"].as_u64(), Some(baked_count), "{out}");
        let regenerated = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        assert_eq!(
            poses(&regenerated),
            baked,
            "mesma receita + mesmo terreno = mesmas colocações"
        );
        assert_eq!(
            regenerated.find_by_attr("generated-by", &id).len(),
            1,
            "substituído, não duplicado"
        );

        // O `inverse` do relatório é o undo de um agente.
        let inverse: Vec<crate::scene::ops::EditOp> =
            serde_json::from_value(out["inverse"].clone()).unwrap();
        let mut doc = regenerated;
        crate::scene::ops::apply_batch(&mut doc, "undo", &inverse).unwrap();
        doc.save().unwrap();
        assert_eq!(
            std::fs::read_to_string(&static_world).unwrap(),
            before,
            "undo byte a byte"
        );
    }

    /// Pincéis de terreno como operações: traço agrupado num undo, persistência no `.ahgt`,
    /// undo byte a byte e sync para o terreno vivo.
    #[test]
    fn test_terrain_brush_ops_persist_undo_and_sync() {
        use crate::scene::ops::{EditOp, History};
        use crate::scene::terrain::BrushMode;
        let dir = write_minimal_world();
        run_bake(&dir.path().join("world.xml"), &dir.path().join("bake")).expect("bake");
        let static_world = dir.path().join(STATIC_WORLD);
        let ahgt = dir.path().join("bake").join(BAKE_AHGT);
        let original = std::fs::read(&ahgt).unwrap();

        let brush = |x: f32| EditOp::TerrainBrush {
            mode: BrushMode::Raise,
            at: [x, 0.0],
            radius: 10.0,
            amount: 3.0,
            height: None,
        };
        let mut doc = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        let before = doc.terrain_mut().unwrap().sample(0.0, 0.0);
        let mut history = History::default();
        for x in [-2.0, 0.0, 2.0] {
            history
                .apply_coalesced(&mut doc, "stroke", &[brush(x)], 1)
                .unwrap();
        }
        assert!(doc.is_dirty());
        assert!(
            doc.dirty_paths().iter().any(|p| p.ends_with(".ahgt")),
            "{:?}",
            doc.dirty_paths()
        );
        let raised = doc.terrain_mut().unwrap().sample(0.0, 0.0);
        assert!(raised > before + 5.0, "{before} → {raised}");
        doc.save().unwrap();
        let mut reloaded = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        assert!((reloaded.terrain_mut().unwrap().sample(0.0, 0.0) - raised).abs() < 1e-3);

        // Um traço = um undo, e volta ao ficheiro original exato.
        history.undo(&mut doc).unwrap().expect("undo do traço");
        assert!(!history.can_undo(), "as 3 pinceladas eram uma transação");
        doc.save().unwrap();
        assert_eq!(std::fs::read(&ahgt).unwrap(), original);

        // Sync: o terreno vivo (bootstrap real) passa a ver a altura nova.
        let config = crate::config::load(dir.path()).unwrap();
        let loaded = crate::xml::include::load_world(&static_world).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        let mut app = headless_app(&static_world, &config, parsed).unwrap();
        app.add_plugins((
            crate::scene::live::ScenePlugin,
            crate::scene::sync::SceneSyncPlugin,
        ));
        app.insert_resource(crate::scene::doc::SceneDoc::load(&static_world).unwrap());
        app.add_systems(
            bevy::app::Startup,
            (
                crate::recipes::spawn::startup,
                crate::terrain::runtime::bootstrap,
            )
                .chain(),
        );
        app.update();
        let live_before = app.world().resource::<TerrainRuntime>().sample(0.0, 0.0);
        crate::scene::live::apply(app.world_mut(), "brush", &[brush(0.0)]).unwrap();
        app.update();
        let runtime = app.world().resource::<TerrainRuntime>();
        let doc_height = app
            .world()
            .resource::<crate::scene::doc::SceneDoc>()
            .terrain
            .as_ref()
            .unwrap()
            .sample(0.0, 0.0);
        let live_after = runtime.sample(0.0, 0.0);
        assert!(runtime.deltas.revision() > 0, "o overlay recebeu o recorte");
        assert!(
            live_after > live_before + 2.0,
            "{live_before} → {live_after}"
        );
        assert!(
            (live_after - doc_height).abs() < 0.05,
            "vivo {live_after} vs documento {doc_height}"
        );
    }

    /// Pintura de material ao vivo: a pincelada chega ao terreno do runtime só na janela
    /// tocada (sem copiar a grelha inteira) e o undo limpa-a.
    #[test]
    fn test_terrain_paint_syncs_window_to_live_runtime() {
        use crate::scene::ops::EditOp;
        let dir = write_minimal_world();
        run_bake(&dir.path().join("world.xml"), &dir.path().join("bake")).expect("bake");
        let static_world = dir.path().join(STATIC_WORLD);
        let config = crate::config::load(dir.path()).unwrap();
        let loaded = crate::xml::include::load_world(&static_world).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        let mut app = headless_app(&static_world, &config, parsed).unwrap();
        app.add_plugins((
            crate::scene::live::ScenePlugin,
            crate::scene::sync::SceneSyncPlugin,
        ));
        app.insert_resource(crate::scene::doc::SceneDoc::load(&static_world).unwrap());
        app.add_systems(
            bevy::app::Startup,
            (
                crate::recipes::spawn::startup,
                crate::terrain::runtime::bootstrap,
            )
                .chain(),
        );
        app.update();
        let side = app.world().resource::<TerrainRuntime>().paint.side();
        let paint = EditOp::TerrainPaint {
            at: [10.0, 10.0],
            radius: 6.0,
            layer: "dirt_trail".into(),
            amount: 1.0,
        };
        crate::scene::live::apply(app.world_mut(), "paint", &[paint]).unwrap();
        app.update();
        let runtime = app.world().resource::<TerrainRuntime>();
        assert_eq!(
            runtime.paint.side(),
            side,
            "a grelha viva mantém-se (cópia por janela)"
        );
        assert_eq!(
            runtime.paint.sample(10.0, 10.0).map(|(slot, _)| slot),
            Some(crate::terrain::splat::SLOT_DIRT_TRAIL)
        );
        assert_eq!(
            runtime.paint.sample(-40.0, -40.0),
            None,
            "fora da pincelada fica limpa"
        );

        crate::scene::live::undo(app.world_mut())
            .unwrap()
            .expect("undo");
        app.update();
        assert_eq!(
            app.world()
                .resource::<TerrainRuntime>()
                .paint
                .sample(10.0, 10.0),
            None
        );
    }

    /// `settle` depois de editar o terreno: o grupo gerado volta ao chão; sobre terreno
    /// intacto não gera ruído.
    #[test]
    fn test_settle_follows_edited_terrain() {
        use crate::scene::ops::{EditOp, apply_batch};
        use crate::scene::terrain::BrushMode;
        let dir = write_minimal_world();
        run_bake(&dir.path().join("world.xml"), &dir.path().join("bake")).expect("bake");
        let static_world = dir.path().join(STATIC_WORLD);
        let mut doc = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        let group = doc
            .walk_find(|xml| xml.attr("generated-by").is_some())
            .expect("grupo gerado");
        let selector = doc.selector(&group);

        let quiet = crate::scene::tools::settle(&mut doc, &selector, 0.0).unwrap();
        assert!(
            quiet.ops.is_empty(),
            "acabado de bakear já está assente: {:?}",
            quiet.ops.len()
        );

        // Uma colina larga por baixo de tudo.
        apply_batch(
            &mut doc,
            "hill",
            &[EditOp::TerrainBrush {
                mode: BrushMode::Raise,
                at: [0.0, 0.0],
                radius: 90.0,
                amount: 6.0,
                height: None,
            }],
        )
        .unwrap();
        let out = crate::scene::tools::settle(&mut doc, &selector, 0.0).unwrap();
        assert!(!out.ops.is_empty());
        apply_batch(&mut doc, &out.label, &out.ops).unwrap();
        let frame = crate::scene::pose::world_frame(&doc, &group);
        for index in 0..doc.children(&group).unwrap().len() {
            let child = group.child(index);
            let local = crate::scene::pose::read_pose(doc.node(&child).unwrap()).unwrap();
            let world = frame.transform_point3(local.translation);
            let ground = doc.terrain.as_ref().unwrap().sample(world.x, world.z);
            assert!(
                (world.y - ground).abs() < 0.03,
                "{} y={} chão={ground}",
                doc.selector(&child),
                world.y
            );
        }
    }

    /// O estático sai em pose canónica: re-escrever a pose de uma instância com `pose_op`
    /// (o que o gizmo/um agente faz) não muda nada — zero ruído no diff.
    #[test]
    fn test_static_instances_are_canonical() {
        let dir = write_minimal_world();
        run_bake(&dir.path().join("world.xml"), &dir.path().join("bake")).expect("bake");
        let doc = crate::scene::doc::SceneDoc::load(&dir.path().join(STATIC_WORLD)).unwrap();
        let group = doc
            .walk_find(|xml| xml.attr("generated-by").is_some())
            .unwrap();
        let mut checked = 0;
        for index in 0..doc.children(&group).unwrap().len() {
            let node = group.child(index);
            let xml = doc.node(&node).unwrap();
            assert!(xml.attr("rotation").is_none(), "sem quaterniões crus");
            let pose = crate::scene::pose::read_pose(xml).unwrap();
            let op = crate::scene::pose::pose_op(
                &doc.selector(&node),
                xml,
                pose,
                crate::scene::pose::PoseParts::ALL,
            )
            .unwrap();
            let crate::scene::ops::EditOp::Set { attrs, .. } = op else {
                panic!("set")
            };
            for (key, value) in attrs {
                assert_eq!(
                    xml.attr(&key).map(str::to_owned),
                    value,
                    "{key} já canónico"
                );
            }
            checked += 1;
        }
        assert!(checked > 0);
    }
}
