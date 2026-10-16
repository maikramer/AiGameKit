//! `viber bake` — converte um mundo PROCEDURAL em estático, no próprio sítio.
//!
//! O runtime só carrega mundos estáticos (`<world static="true">`); o bake é a
//! conversão única de um mundo procedural (geradores, `place`, relevo
//! procedural) para essa forma. Escreve:
//!
//! * os MESMOS ficheiros XML (o mundo + os includes), reescritos no sítio com
//!   `static="true"` — spawners `<StaticSpawner>` substituídos por
//!   `<Scatter id>` (a receita) + `<Group generated-by>` de instâncias com a
//!   pose de cada uma (grupos vazios saem por inteiro; as pedras de margem da
//!   engine saem como `<Scatter id="engine-N">` sintéticos); `<Vegetation>`
//!   com `instances` em binário; poses assentadas e `place` resolvido; features
//!   com os valores resolvidos (`height` nos pads, níveis de água). Includes
//!   que JÁ são estáticos (partilhados com outro mundo convertido) nunca são
//!   reescritos;
//! * `<stem>.data/` ([`data_dir`]): `terrain.ahgt` (a grelha u16 FINAL,
//!   pós-carve) + `.paint`, `veg/NNN.vinst` e `bake.json` (tudo o que era
//!   automático, explícito — o documento do World Overview do editor).
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
use serde::{Deserialize, Serialize};

use crate::recipes::spawn::PendingWorld;
use crate::spawner::{PendingSpawnGroups, PlacedInstance, SpawnOccupancy};
use crate::terrain::cliffs::CliffMask;
use crate::terrain::heightmap::{AhgtMeta, HeightMapU16};
use crate::terrain::runtime::TerrainRuntime;
use crate::xml::XmlNode;

/// Nome do ficheiro de documento dentro do dir do bake.
pub const BAKE_JSON: &str = "bake.json";
/// Nome do heightmap carvado dentro da pasta de dados do mundo.
pub const BAKE_AHGT: &str = "terrain.ahgt";
/// Sufixo da pasta de dados binários de um mundo: `world.xml` → `world.data/`
/// (heightmap + `.paint`, vegetação `.vinst`, `bake.json`).
pub const DATA_SUFFIX: &str = ".data";

/// A pasta de dados de um mundo (`<dir>/<stem>.data`) — onde o bake e o `viber create`
/// escrevem o heightmap, a pintura e a vegetação.
pub fn data_dir(world_path: &Path) -> PathBuf {
    let stem = world_path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or("world");
    world_path
        .parent()
        .unwrap_or(Path::new("."))
        .join(format!("{stem}{DATA_SUFFIX}"))
}

/// `true` quando a raiz do ficheiro XML declara `static="true"`.
pub fn is_static_root(attrs: &[(String, String)]) -> bool {
    attrs
        .iter()
        .any(|(key, value)| key == "static" && matches!(value.trim(), "true" | "1"))
}

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

/// Resumo do que o bake escreveu (stdout do CLI + testes). Num mundo sem `<Terrain>` não há
/// dados: `out_dir`/`ahgt_path`/`bake_json_path` ficam vazios.
#[derive(Debug, Clone, Default)]
pub struct BakeReport {
    pub out_dir: PathBuf,
    pub ahgt_path: PathBuf,
    pub bake_json_path: PathBuf,
    pub static_world_path: Option<PathBuf>,
    /// Ficheiros XML reescritos no sítio (o mundo + os includes que não eram estáticos).
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
    /// Id do `<Scatter>` que o representa no estático (engine-N, o `name` do
    /// spawner, ou grp-<nó>) — bake e ferramenta `scatter` nomeiam as
    /// instâncias a partir dele (`inst-{id}-{i:04}`). Vazio para dynamic
    /// (fica no runtime) e vegetation (mantém a tag).
    scatter_id: String,
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

/// Recusa converter um mundo que já é estático: o estático é a fonte da verdade (agentes e
/// o editor editam-no) e não há nada procedural para converter. Regenerar partes dele é
/// trabalho das ferramentas (`viber tool scatter|settle`, pincéis de terreno).
pub fn check_bake_target(world_path: &Path) -> Result<()> {
    let doc = crate::xml::parse_file(world_path)?;
    if is_static_root(&doc.root_attrs) {
        anyhow::bail!(
            "{} já é um mundo estático — edita-o com `viber scene`/`viber tool`/ViberEditor \
             (o bake só converte mundos procedurais)",
            world_path.display()
        );
    }
    Ok(())
}

/// Converte um mundo procedural em estático NO PRÓPRIO SÍTIO.
///
/// O mundo e os seus includes são reescritos nos mesmos caminhos (marcados
/// `static="true"`); os dados binários vão para `out_dir` (default [`data_dir`]:
/// `<stem>.data/`). Includes que já são estáticos (fragmentos partilhados com outro mundo
/// já convertido) nunca são reescritos.
///
/// # Errors
/// Falha quando o mundo não parseia, quando o bootstrap não publica o
/// runtime, ou quando o alinhamento spawner↔XML é impossível (spawners
/// dentro de `<Prototype>` expandido — ver limite no doc do módulo).
pub fn run_bake(world_path: &Path, out_dir: &Path) -> Result<BakeReport> {
    let world_path = &std::path::absolute(world_path)?;
    let world_dir = crate::project::root_of(world_path);
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

    let Some(runtime) = app.world_mut().remove_resource::<TerrainRuntime>() else {
        // Mundo SEM `<Terrain>` (cenas de primitivas, UI): não há dados para gerar — a
        // conversão é só a marca estática. Geradores precisam de terreno onde assentar.
        let generators = app
            .world()
            .get_resource::<PendingSpawnGroups>()
            .map_or(0, |pending| pending.groups.len());
        anyhow::ensure!(
            generators == 0,
            "{generators} gerador(es) de spawn sem <Terrain> onde assentar — o bake precisa do terreno"
        );
        let mut doc = crate::scene::doc::SceneDoc::load(world_path)?;
        let mut report = BakeReport::default();
        // `place` sem terreno nunca teve efeito (a pose autoral ficava): sai do XML.
        let mut placed = Vec::new();
        doc.walk(|node, xml| {
            if xml.attr("place").is_some() {
                placed.push(node.clone());
            }
        });
        for node in placed {
            if let Some(xml) = doc.node_mut(&node) {
                xml.remove_attr("place");
            }
            doc.touch(node.file);
        }
        promote_static(&mut doc, &mut report.warnings);
        report.static_files = doc.save().context("gravar o mundo estático")?;
        report.static_world_path = Some(doc.files[0].path.clone());
        return Ok(report);
    };
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
        occupancy.register_exclusion(exclusion.center.x, exclusion.center.y, exclusion.radius);
    }

    let mut total_instances = 0usize;
    let mut scatter_ids: std::collections::HashSet<String> = std::collections::HashSet::new();
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
        // O id do `<Scatter>` do estático, calculado UMA VEZ para bake.json e
        // world.static.xml usarem o mesmo (e a tool `scatter` repeti-lo nos
        // nomes): `name` do spawner, senão o índice do nó; dedup por sufixo.
        let scatter_id = if engine_generated {
            format!("engine-{index}")
        } else if kind == GroupKind::Static {
            let slot = xml_index.expect("não-engine tem xml_index");
            let mut id = name.clone().unwrap_or_else(|| format!("grp-{slot}"));
            if !scatter_ids.insert(id.clone()) {
                id = format!("{id}-{slot}");
                scatter_ids.insert(id.clone());
            }
            id
        } else {
            String::new()
        };
        let (instances, _stats) = {
            let (mut instances, stats) =
                crate::placement::place(&group.spec, &mut occupancy, &runtime, cliffs.as_ref());
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
            scatter_id,
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

    // 3. O mundo estático: os MESMOS ficheiros, reescritos no sítio.
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
    report.static_files = static_doc.save().context("gravar o mundo estático")?;
    report.static_world_path = Some(static_doc.files[0].path.clone());
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
    // `source.world` RELATIVO à pasta do mundo (só o nome do ficheiro): o
    // bake.json vive em `<mundo>/bake/` e tem de ser portável entre clones —
    // caminhos absolutos da máquina que bakeou são lixo para outrem.
    let source_world = world_path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| world_path.display().to_string());
    let mut doc = BakeDocument {
        schema: 1,
        source: BakeSource {
            world: source_world,
            viber_version: env!("CARGO_PKG_VERSION").to_string(),
        },
        terrain: BakeTerrain {
            world_size: spec.world_size,
            max_height: spec.max_height,
            // Relativo à pasta de dados (é assim que o World Overview do editor o lê).
            heightmap: BAKE_AHGT.to_string(),
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
        // Prefixo dos nomes de instância: o id do `<Scatter>` do estático
        // (`inst-{id}-{i:04}`) — vegetation/dynamic não têm Scatter e usam o
        // id do grupo do bake.json (`inst-grp-{index}-{i:04}`).
        let name_prefix = if group.scatter_id.is_empty() {
            format!("grp-{index}")
        } else {
            group.scatter_id.clone()
        };
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
                    name: format!("inst-{name_prefix}-{i:04}"),
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
// O mundo estático (os mesmos ficheiros, convertidos no sítio)
// ---------------------------------------------------------------------------

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
/// 1. spawners (não vegetation) → `<Scatter id>` (receita) + `<Group name="spawn/…"
///    generated-by>` de instâncias explícitas — grupos que resolveram 0 instâncias
///    saem por inteiro (nem receita nem grupo);
/// 2. features de terreno com os valores resolvidos (`height` + `pre-carved` nos pads,
///    `water-level(s)` na água, `flatten="0"` nas estradas, `heightmap` carvado);
/// 3. poses assentadas/colocadas escritas no XML (`translation`/`euler` canónicos, `place`
///    removido) — o runtime estático não assenta nada;
/// 4. `static="true"` na raiz de cada ficheiro convertido — gravados nos MESMOS caminhos
///    (os já estáticos, partilhados com outro mundo, ficam intocados).
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
    let heightmap_rel = {
        let ahgt = out_dir.join(BAKE_AHGT);
        let ahgt = ahgt.canonicalize().unwrap_or(ahgt);
        ahgt.strip_prefix(&doc.root_dir)
            .map(|rel| rel.to_string_lossy().replace('\\', "/"))
            .unwrap_or_else(|_| ahgt.display().to_string())
    };
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
    let mut engine_outputs: Vec<(XmlNode, XmlNode)> = Vec::new();
    let mut removals: Vec<NodeRef> = Vec::new();
    let mut vegetation_index = 0usize;
    let mut pending_outputs: Vec<(NodeRef, XmlNode)> = Vec::new();
    for group in groups.iter() {
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
        let Some(spawner_index) = group.xml_index else {
            // Grupo da engine (pedras de margem), agora COM receita: o `<Scatter
            // id="engine-N">` sintético + o grupo `generated-by` — regenerável
            // por `viber tool scatter` (o Lake/River mantém `rocks="0"`; a
            // receita não volta a correr em runtime). Grupo vazio não se emite.
            if group.instances.is_empty() {
                continue;
            }
            let id = group.scatter_id.clone();
            let recipe = engine_scatter_node(&id, &group.spec, &group.instances);
            let output = generated_group(
                &recipe,
                &id,
                &group.instances,
                &group.spec.template_urls,
                bevy::math::Affine3A::IDENTITY,
                &format!("inst-{id}"),
            );
            engine_outputs.push((recipe, output));
            continue;
        };
        let spawner = spawner_nodes
            .get(spawner_index)
            .ok_or_else(|| anyhow::anyhow!("nó do spawner {spawner_index} fora do documento"))?;
        let target = node_of(&doc, spawner, "spawner")?;
        // Grupo que resolveu 0 instâncias NÃO se emite — nem o `<Scatter>` nem
        // o `<Group generated-by>`: não há nada para regenerar nem para
        // mostrar, e o spawner original não existe no lado estático (o
        // `<StaticSpawner>` num mundo estático nunca correria — zero perda).
        if group.instances.is_empty() {
            removals.push(target);
            continue;
        }
        // A receita fica: o spawner vira `<Scatter id>` (a ferramenta `scatter` regenera o
        // resultado) e o grupo gerado entra logo a seguir, com `generated-by`.
        let id = group.scatter_id.clone();
        let parent_world = parent_world_frame(&doc, &target, &globals);
        let output = generated_group(
            spawner,
            &id,
            &group.instances,
            &group.spec.template_urls,
            parent_world,
            &format!("inst-{id}"),
        );
        let recipe = doc.node_mut(&target).expect("resolvido");
        recipe.tag = "Scatter".into();
        recipe.set_attr("id", id);
        doc.touch(target.file);
        pending_outputs.push((target, output));
    }
    let world_root = NodeRef::root(0);
    for (recipe, output) in engine_outputs {
        doc.children_mut(&world_root).expect("raiz").push(recipe);
        doc.children_mut(&world_root).expect("raiz").push(output);
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
            "terrain" => vec![("heightmap", heightmap_rel.clone())],
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

    // 3b. As mutações estruturais da secção 1 — grupos gerados a seguir às
    //     receitas e spawners vazios removidos — aplicadas no FIM, juntas e
    //     por ordem de (ficheiro, caminho) DECRESCENTE: cada uma não desloca
    //     os caminhos das que ainda vêm (menores), e as do passo 3 acima já
    //     correram contra os caminhos originais.
    enum PendingEdit {
        Output { recipe: NodeRef, xml: XmlNode },
        Remove(NodeRef),
    }
    let mut edits: Vec<PendingEdit> = pending_outputs
        .into_iter()
        .map(|(recipe, xml)| PendingEdit::Output { recipe, xml })
        .chain(removals.into_iter().map(PendingEdit::Remove))
        .collect();
    edits.sort_by(|a, b| {
        let key = |e: &PendingEdit| match e {
            PendingEdit::Output { recipe, .. } => (recipe.file, recipe.path.clone()),
            PendingEdit::Remove(node) => (node.file, node.path.clone()),
        };
        key(b).cmp(&key(a))
    });
    let mut touched = std::collections::BTreeSet::new();
    for edit in edits {
        let file = match edit {
            PendingEdit::Output { recipe, xml } => {
                let parent = recipe.parent().expect("um spawner nunca é raiz");
                let index = recipe.path.last().copied().unwrap_or(0) + 1;
                doc.children_mut(&parent)
                    .expect("pai do spawner")
                    .insert(index, xml);
                recipe.file
            }
            PendingEdit::Remove(node) => {
                let index = *node.path.last().expect("um spawner nunca é raiz");
                doc.children_mut(&node.parent().expect("um spawner nunca é raiz"))
                    .expect("pai do spawner")
                    .remove(index);
                node.file
            }
        };
        touched.insert(file);
    }
    for file in touched {
        doc.reindex_includes(file);
    }

    // 4. Marca de mundo estático em cada ficheiro convertido (no sítio).
    promote_static(&mut doc, warnings);
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

/// Marca os ficheiros do documento como estáticos para gravar no sítio: cada raiz
/// `<world>`/`<scene>` ganha `static="true"`. Um ficheiro que JÁ era estático (fragmento
/// partilhado com um mundo convertido antes — os mundos de QA incluem o mundo principal)
/// nunca é reescrito: se o bake o alterou (ex.: assentou-o noutro terreno), as alterações
/// são descartadas com um aviso.
fn promote_static(doc: &mut crate::scene::doc::SceneDoc, warnings: &mut Vec<String>) {
    for file in 0..doc.files.len() {
        let rel = doc.relative_path(file);
        let scene = &mut doc.files[file];
        let original = crate::xml::parse_file(&scene.path).ok();
        if original
            .as_ref()
            .is_some_and(|original| is_static_root(&original.root_attrs))
        {
            if scene.dirty {
                warnings.push(format!(
                    "{rel}: já é estático (partilhado) — as alterações do bake neste ficheiro foram descartadas"
                ));
            }
            scene.dirty = false;
            continue;
        }
        if matches!(scene.doc.root_tag.as_str(), "world" | "scene") {
            scene.doc.set_root_attr("static", "true");
        }
        scene.dirty = true;
    }
}

/// A receita `<Scatter>` SINTÉTICA de um grupo da engine (pedras de margem de
/// `<Lake rocks>`/`<River rocks>`): os parâmetros de placement da spec
/// (count real pós-gates, seed, escala, gates de água/declive) e, por região,
/// a caixa XZ das colocações; os templates do pool como filhos, com a ladder
/// de LOD — o `generated_group`/`parse_scatter` leem daqui o que copiam para
/// as instâncias. O anel EXACTO das margens vem de `fixed_candidates`
/// (engine-interna, derivados do corpo de água em runtime) e não é
/// serializável — a receita cobre a MESMA faixa para `viber tool scatter`
/// regenerar depois de o terreno mudar; o bake do procedural é que refaz o
/// anel fiel.
fn engine_scatter_node(
    id: &str,
    spec: &crate::recipes::StaticSpawnerSpec,
    instances: &[PlacedInstance],
) -> XmlNode {
    let mut min = [f32::MAX; 2];
    let mut max = [f32::MIN; 2];
    for inst in instances {
        min[0] = min[0].min(inst.position.x);
        min[1] = min[1].min(inst.position.z);
        max[0] = max[0].max(inst.position.x);
        max[1] = max[1].max(inst.position.z);
    }
    let coord = |v: f32| format!("{v:.1}");
    let flag = |b: bool| if b { "1" } else { "0" };
    let mut node = XmlNode::new("Scatter")
        .with_attr("id", id)
        // Metadado (no-op no parse): a origem da receita.
        .with_attr("profile", "shore-rocks")
        .with_attr("count", instances.len().to_string())
        .with_attr("seed", spec.seed.to_string())
        .with_attr(
            "region-min",
            format!("{} 0 {}", coord(min[0]), coord(min[1])),
        )
        .with_attr(
            "region-max",
            format!("{} 0 {}", coord(max[0]), coord(max[1])),
        )
        .with_attr("avoid-water", flag(spec.avoid_water))
        .with_attr("avoid-road", flag(spec.avoid_road))
        .with_attr("avoid-cliff", flag(spec.avoid_cliff))
        .with_attr("align-to-terrain", flag(spec.align_to_terrain))
        .with_attr("random-yaw", flag(spec.random_yaw))
        .with_attr("avoid-overlaps", flag(spec.avoid_overlaps))
        .with_attr("footprint-radius", format!("{}", spec.footprint_radius))
        .with_attr("max-slope-deg", format!("{}", spec.max_slope_deg))
        .with_attr("max-slope-attempts", spec.max_slope_attempts.to_string())
        .with_attr("scale-min", format!("{}", spec.scale_min))
        .with_attr("scale-max", format!("{}", spec.scale_max))
        .with_attr("scale-axis-min", format!("{}", spec.scale_axis_min))
        .with_attr("scale-axis-max", format!("{}", spec.scale_axis_max))
        .with_attr("base-y-offset", format!("{}", spec.base_y_offset));
    for (index, url) in spec.template_urls.iter().enumerate() {
        let mut child = XmlNode::new("GltfScene").with_attr("url", url.clone());
        if let Some(lod) = spec.template_lods.get(index) {
            if let Some(lod1) = &lod.lod1_url {
                child.set_attr("lod1-url", lod1.clone());
            }
            if let Some(lod2) = &lod.lod2_url {
                child.set_attr("lod2-url", lod2.clone());
            }
            child.set_attr("lod-threshold-near", format!("{}", lod.near));
            child.set_attr("lod-threshold-mid", format!("{}", lod.mid));
        }
        node.push_child(child);
    }
    node
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
        occupancy.register_exclusion(exclusion.center.x, exclusion.center.y, exclusion.radius);
    }

    let baked_veg: Vec<&BakeSpawnGroup> = baked
        .spawn_groups
        .iter()
        .filter(|g| g.kind == "vegetation")
        .collect();
    // No estático só sobra vegetation como grupo pending (os `<StaticSpawner>`
    // viraram `<Scatter>` no-op e as pedras de margem vêm da receita
    // `engine-N`, também Scatter). Os `<DynamicSpawner>` ficam de propósito
    // (gameplay) e têm de ficar FORA da contagem: o filtro antigo
    // (`fixed_candidates.is_empty()`) apanhava-os e inflava o lado do estático
    // em qualquer mundo com criaturas (31 vs 9 no simple-rpg real).
    let static_veg: Vec<&crate::spawner::SpawnGroupState> = pending
        .groups
        .iter()
        .filter(|g| !g.dynamic && g.spec.fixed_candidates.is_empty())
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
        let (instances, _) =
            crate::placement::place(&group.spec, &mut occupancy, &runtime, cliffs.as_ref());
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
        write_test_config(dir.path());
        dir
    }

    /// A forma mínima do CONFIG_YAML_TEMPLATE do scaffold (docs/ASSETS.md) — comum aos
    /// mundos de teste que não usam [`write_minimal_world`].
    fn write_test_config(dir: &Path) {
        std::fs::write(
            dir.join("config.yaml"),
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
    }

    #[test]
    fn test_bake_document_serializes_roundtrip() {
        let mut doc = BakeDocument {
            schema: 1,
            ..BakeDocument::default()
        };
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
        let out = dir.path().join("world.data");
        let report = run_bake(&world, &out).expect("bake corre");

        assert!(report.ahgt_path.exists(), "ahgt escrito");
        assert!(report.bake_json_path.exists(), "bake.json escrito");
        assert_eq!(
            report.static_world_path.as_deref(),
            Some(dir.path().join("world.xml").as_path())
        );

        let doc: BakeDocument =
            serde_json::from_str(&std::fs::read_to_string(&report.bake_json_path).unwrap())
                .unwrap();
        assert_eq!(doc.schema, 1);
        // Portável: o caminho da fonte é RELATIVO (só o nome do ficheiro) e o
        // JSON não embute caminhos absolutos da máquina que bakeou.
        assert_eq!(doc.source.world, "world.xml");
        let raw_json = std::fs::read_to_string(&report.bake_json_path).unwrap();
        assert!(
            !raw_json.contains("/home/") && !raw_json.contains("/media/"),
            "bake.json sem caminhos absolutos"
        );
        assert!(
            !raw_json.contains(dir.path().display().to_string().as_str()),
            "bake.json sem o caminho do mundo que o gerou"
        );
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
            verify_static(&dir.path().join("world.xml"), &report.bake_json_path).expect("verify");
        assert!(checked > 0, "instâncias de vegetation verificadas");

        // O XML estático contém as instâncias explícitas e o heightmap novo.
        let xml = std::fs::read_to_string(dir.path().join("world.xml")).unwrap();
        assert!(xml.contains("heightmap=\"world.data/terrain.ahgt\""));
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

    /// Regressão do `verify_static`: os `<DynamicSpawner>` ficam no estático de
    /// propósito (gameplay em runtime) e NÃO contam como grupos vegetation — o
    /// filtro antigo (`fixed_candidates.is_empty()`) apanhava-os e fazia o
    /// verify falhar em qualquer mundo com criaturas (31 vs 9 no simple-rpg).
    #[test]
    fn test_verify_ignores_dynamic_spawners_when_counting_vegetation() {
        let dir = write_minimal_world();
        let world_xml = dir.path().join("world.xml");
        let with_dynamic = MINIMAL_WORLD.replace(
            "</world>",
            r#"  <DynamicSpawner count="2" seed="55" region-min="-20 0 -20" region-max="20 0 20" random-yaw="true">
    <Creature role="enemy">
      <GLTFLoader role="visual" url="/assets/meshes/test/cube_lod0.glb" />
    </Creature>
  </DynamicSpawner>
</world>"#,
        );
        std::fs::write(&world_xml, with_dynamic).unwrap();
        let report = run_bake(&world_xml, &dir.path().join("world.data")).expect("bake corre");
        // O estático mantém o <DynamicSpawner> e a vegetation; o verify cruza
        // SÓ vegetation (1 == 1) — sem o fix falhava com "2 grupos vs 1".
        let static_path = report.static_world_path.expect("estático escrito");
        let xml = std::fs::read_to_string(&static_path).unwrap();
        assert!(xml.contains("<DynamicSpawner"), "dinâmico preservado");
        let checked =
            verify_static(&static_path, &report.bake_json_path).expect("verify só vegetation");
        assert!(checked > 0, "instâncias de vegetation verificadas");
    }

    /// O estático é modular (os MESMOS ficheiros, convertidos no sítio), marcado
    /// `static="true"` em cada raiz, com o assentamento e o `place` escritos no XML — e o
    /// runtime estático não mexe.
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
        let report = run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");
        assert_eq!(report.static_files, 2);

        let entry = std::fs::read_to_string(dir.path().join("world.xml")).unwrap();
        assert!(entry.contains("<world static=\"true\">"), "{entry}");
        assert!(
            entry.contains("src=\"/world/props.xml\""),
            "include no mesmo caminho"
        );
        // O include foi convertido no sítio: marcado e sem geradores.
        let props = std::fs::read_to_string(dir.path().join("world/props.xml")).unwrap();
        assert!(props.contains("<world static=\"true\">"), "{props}");
        assert!(!props.contains("<StaticSpawner"), "{props}");
        assert!(!dir.path().join("static").exists(), "sem espelho static/");
        // Converter outra vez é recusado (nada procedural para converter).
        assert!(check_bake_target(&dir.path().join("world.xml")).is_err());

        let static_props =
            crate::scene::doc::SceneDoc::load(&dir.path().join("world.xml")).unwrap();
        assert_eq!(static_props.relative_path(1), "world/props.xml");
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
        let loaded = crate::xml::include::load_world(&dir.path().join("world.xml")).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        assert!(parsed.static_world);
        let config = crate::config::load(dir.path()).unwrap();
        let mut app = headless_app(&dir.path().join("world.xml"), &config, parsed).unwrap();
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
        let veg = static_props.find_by_attr("instances", "world.data/veg/000.vinst");
        assert_eq!(veg.len(), 1, "o <Vegetation> aponta para o binário");
        let preplaced = crate::vinst::read(&dir.path().join("world.data/veg/000.vinst")).unwrap();
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

    /// Mundo sem `<Terrain>` (primitivas/UI): a conversão é só a marca estática, sem dados.
    #[test]
    fn test_bake_world_without_terrain_only_promotes() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join(crate::config::CONFIG_FILE),
            crate::scaffold::CONFIG_YAML_TEMPLATE,
        )
        .unwrap();
        let world = dir.path().join("hello.xml");
        std::fs::write(
            &world,
            "<world>\n  <Cuboid name=\"box\" half-size=\"1 1 1\" />\n  <Composition name=\"mark\" translation=\"0 0 9\" place=\"at: 0 9\"><Cuboid size=\"1\" /></Composition>\n</world>",
        )
        .unwrap();
        let report = run_bake(&world, &data_dir(&world)).expect("promove");
        assert_eq!(report.static_files, 1);
        assert!(report.ahgt_path.as_os_str().is_empty());
        assert!(!data_dir(&world).exists(), "sem dados para gerar");
        let xml = std::fs::read_to_string(&world).unwrap();
        assert!(xml.contains("<world static=\"true\">"), "{xml}");
        assert!(xml.contains("name=\"box\""));
        assert!(!xml.contains("place="), "place sem terreno sai: {xml}");
        let loaded = crate::xml::include::load_world(&world).unwrap();
        crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).expect("estático válido");
    }

    /// Um segundo mundo procedural que inclui um fragmento JÁ convertido (os mundos de QA
    /// incluem o mundo principal): o fragmento estático nunca é reescrito.
    #[test]
    fn test_bake_never_rewrites_shared_static_fragment() {
        let dir = write_minimal_world();
        let world = MINIMAL_WORLD.replace(
            "  <SpawnExclusion",
            "  <Include src=\"/world/props.xml\" />\n  <SpawnExclusion",
        );
        std::fs::write(dir.path().join("world.xml"), &world).unwrap();
        std::fs::create_dir_all(dir.path().join("world")).unwrap();
        std::fs::write(
            dir.path().join("world/props.xml"),
            "<world>\n  <Group name=\"hut\"><Cuboid size=\"2\" translation=\"0 1 0\" /></Group>\n</world>",
        )
        .unwrap();
        // Um mundo de QA com o mesmo conteúdo, a incluir o mesmo fragmento.
        std::fs::write(dir.path().join("qa.xml"), &world).unwrap();
        run_bake(
            &dir.path().join("world.xml"),
            &data_dir(&dir.path().join("world.xml")),
        )
        .expect("bake do principal");
        let shared = std::fs::read_to_string(dir.path().join("world/props.xml")).unwrap();
        assert!(shared.contains("static=\"true\""), "{shared}");

        let qa = dir.path().join("qa.xml");
        let report = run_bake(&qa, &data_dir(&qa)).expect("bake do QA");
        assert_eq!(
            std::fs::read_to_string(dir.path().join("world/props.xml")).unwrap(),
            shared,
            "fragmento partilhado intacto"
        );
        assert!(dir.path().join("qa.data/terrain.ahgt").is_file());
        let qa_xml = std::fs::read_to_string(&qa).unwrap();
        assert!(
            qa_xml.contains("heightmap=\"qa.data/terrain.ahgt\""),
            "{qa_xml}"
        );
        assert_eq!(report.static_world_path.as_deref(), Some(qa.as_path()));
    }

    /// Ferramenta `scatter` sobre o estático: a receita sobreviveu ao bake, regenerar dá as
    /// MESMAS colocações (determinismo), e as ops inversas repõem o documento.
    #[test]
    fn test_scatter_tool_regenerates_baked_output() {
        let dir = write_minimal_world();
        run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");
        let static_world = dir.path().join("world.xml");

        let doc = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        let scatters = crate::scene::tools::list_scatters(&doc);
        let of_kind = |kind: &str| {
            scatters
                .as_array()
                .unwrap()
                .iter()
                .filter(|entry| entry["kind"] == kind)
                .count()
        };
        assert_eq!(of_kind("scatter"), 1, "{scatters}");
        assert_eq!(of_kind("vegetation"), 1, "o menu também lista o .vinst");
        let entry = &scatters.as_array().unwrap()[0];
        assert_eq!(entry["kind"], "scatter");
        let selector = entry["selector"].as_str().unwrap().to_owned();
        let id = entry["id"].as_str().unwrap().to_owned();
        let baked_count = entry["instances"].as_u64().unwrap();
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

    /// Frente "scatter de vegetation": `viber tool scatter` num `<Vegetation instances>`
    /// regenera o .vinst sobre o terreno atual (o XML não muda) — a contagem bate com o
    /// `compute_placements` do bake (mesma spec, mesma exclusão), byte a byte.
    #[test]
    fn test_scatter_tool_regenerates_vegetation_vinst() {
        // Sem StaticSpawner: a ocupação da tool (exclusões) é EXATAMENTE a do bake —
        // o .vinst regenerado tem de ser idêntico ao que o bake escreveu.
        let world = MINIMAL_WORLD.replace(
            r#"  <StaticSpawner profile="tree" count="5" seed="42" region-min="-40 0 -40" region-max="40 0 40" avoid-water="1" random-yaw="true">
    <GameObject role="static" destructible="popup-text: Test Wood; hits: 2" rigidbody="type: fixed; mass: 0; gravity-scale: 0" collider="shape: precompute; mesh-url: /assets/meshes/test/cube_lod0.glb">
      <GLTFLoader url="/assets/meshes/test/cube_lod0.glb" lod1-url="/assets/meshes/test/cube_lod1.glb" lod-threshold-near="30" lod-threshold-mid="90" />
      <ResourceNode kind="test-wood" yield="2" />
    </GameObject>
  </StaticSpawner>
"#,
            "",
        );
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("world.xml"), world).unwrap();
        std::fs::create_dir_all(dir.path().join("assets/meshes/test")).unwrap();
        write_test_config(dir.path());
        run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");
        let static_world = dir.path().join("world.xml");
        let vinst = dir.path().join("world.data/veg/000.vinst");
        let baked_bytes = std::fs::read(&vinst).unwrap();
        let baked = crate::vinst::read(&vinst).unwrap();
        assert!(!baked.is_empty(), "vegetation com instâncias");
        let xml_before = std::fs::read_to_string(&static_world).unwrap();

        let doc = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        let veg = doc.find_by_attr("instances", "world.data/veg/000.vinst")[0].clone();
        let selector = doc.selector(&veg);

        // dry-run: honesto — o .vinst fica COMO ESTAVA (o scatter grava, o dry-run repõe).
        let dry =
            crate::scene::tools::run_offline(&static_world, &selector, true).expect("dry-run");
        assert_eq!(dry["tool"]["kind"], "vegetation", "{dry}");
        assert_eq!(
            std::fs::read(&vinst).unwrap(),
            baked_bytes,
            "dry-run não muda o binário"
        );

        let out =
            crate::scene::tools::run_offline(&static_world, &selector, false).expect("scatter");
        assert_eq!(out["tool"]["kind"], "vegetation", "{out}");
        assert_eq!(
            out["tool"]["placed"].as_u64(),
            Some(baked.len() as u64),
            "contagem = compute_placements com a mesma ocupação"
        );
        assert_eq!(
            out["tool"]["undo"].as_str().unwrap(),
            "binary vinst: não desfazível via History — git ou re-scatter"
        );
        assert_eq!(
            std::fs::read(&vinst).unwrap(),
            baked_bytes,
            "mesma spec + mesmo terreno = mesmo binário"
        );
        assert_eq!(
            std::fs::read_to_string(&static_world).unwrap(),
            xml_before,
            "o XML não muda (o attr instances já apontava ao ficheiro)"
        );
    }

    /// Pedras de margem COM receita: o grupo da engine sai como `<Scatter id="engine-N">` +
    /// `<Group generated-by>` (regenerável pela tool) e os nomes das instâncias seguem o id
    /// do Scatter — o grupo `spawn/grp-0` contém `inst-grp-0-*`, nunca `inst-grp-{índice
    /// global}-*` (o bug do desalinhamento com engine groups intercalados).
    #[test]
    fn test_bake_shore_rocks_get_scatter_recipe() {
        let world = MINIMAL_WORLD.replace(
            r#"<Lake at="-30 20" radius="8" depth="4" water-offset="0.25" />"#,
            r#"<Lake at="-30 20" radius="8" depth="4" water-offset="0.25" rocks="1" rocks-density="0.3" />"#,
        );
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("world.xml"), world).unwrap();
        std::fs::create_dir_all(dir.path().join("assets/meshes/test")).unwrap();
        write_test_config(dir.path());
        let report = run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");

        // O engine group (pedras) tem Scatter + grupo gerado; o count real vem das
        // instâncias que passaram os gates.
        let static_world = dir.path().join("world.xml");
        let xml = std::fs::read_to_string(&static_world).unwrap();
        assert!(
            xml.contains("<Scatter") && xml.contains(r#"id="engine-0""#),
            "receita sintética do grupo da engine"
        );
        assert!(xml.contains(r#"profile="shore-rocks""#));
        assert!(
            xml.contains(r#"<Group name="spawn/engine-0" generated-by="engine-0">"#),
            "grupo com generated-by"
        );
        assert!(xml.contains(r#"name="inst-engine-0-0000""#));
        assert!(xml.contains(r#"rocks="0""#), "o lago não as volta a gerar");

        // O spawner (2.º grupo do pending, 1.º nó XML) usa o SEU id: grp-0 — não o
        // índice global (1) do array de grupos.
        assert!(xml.contains(r#"id="grp-0""#));
        assert!(xml.contains(r#"name="inst-grp-0-0000""#));
        assert!(
            !xml.contains("inst-grp-1-"),
            "o nome segue o id do Scatter, não o índice global do grupo"
        );

        // bake.json com os MESMOS prefixos (engine-0 / grp-0).
        let baked: BakeDocument =
            serde_json::from_str(&std::fs::read_to_string(&report.bake_json_path).unwrap())
                .unwrap();
        assert!(
            baked.spawn_groups[0]
                .instances
                .first()
                .is_some_and(|i| i.name.starts_with("inst-engine-0-")),
            "prefixo do grupo da engine no bake.json"
        );
        assert!(
            baked.spawn_groups[1]
                .instances
                .first()
                .is_some_and(|i| i.name.starts_with("inst-grp-0-")),
            "prefixo do spawner no bake.json"
        );

        // Regenerável: a tool vê a receita da engine no menu.
        let doc = crate::scene::doc::SceneDoc::load(&static_world).unwrap();
        let scatters = crate::scene::tools::list_scatters(&doc);
        assert!(
            scatters.to_string().contains("engine-0"),
            "a receita engine-0 está no menu da tool"
        );
    }

    /// Grupos gerados VAZIOS não se emitem: um spawner cuja região inteira afunda num lago
    /// resolve 0 instâncias — nem `<Scatter>` nem `<Group generated-by>` saem no estático
    /// (o spawner original também não: um `<StaticSpawner>` num mundo estático nunca correria).
    #[test]
    fn test_bake_omits_empty_generated_groups() {
        let world = r#"<?xml version="1.0" encoding="UTF-8"?>
<world>
  <Terrain world-size="128" max-height="40" seed="7" />
  <Lake at="0 0" radius="30" depth="8" water-offset="0.5" />
  <StaticSpawner profile="tree" count="10" seed="11" region-min="-5 0 -5" region-max="5 0 5" avoid-water="1">
    <GameObject role="static"><GLTFLoader url="/assets/meshes/test/cube_lod0.glb" /></GameObject>
  </StaticSpawner>
</world>
"#;
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("world.xml"), world).unwrap();
        std::fs::create_dir_all(dir.path().join("assets/meshes/test")).unwrap();
        write_test_config(dir.path());
        let report = run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");

        let baked: BakeDocument =
            serde_json::from_str(&std::fs::read_to_string(&report.bake_json_path).unwrap())
                .unwrap();
        assert!(
            baked.spawn_groups.iter().all(|g| g.instances.is_empty()),
            "a região afogada não coloca nada"
        );
        let xml = std::fs::read_to_string(dir.path().join("world.xml")).unwrap();
        assert!(
            !xml.contains("<StaticSpawner"),
            "o spawner saiu do estático"
        );
        assert!(!xml.contains("generated-by"), "sem grupo gerado vazio");
        assert!(!xml.contains("<Scatter"), "sem receita de grupo vazio");
        // O mundo estático continua a carregar limpo (parse sem tags estranhas).
        let loaded =
            crate::xml::include::load_world(dir.path().join("world.xml").as_path()).unwrap();
        let parsed = crate::recipes::parse_world(&loaded.root_attrs, &loaded.nodes).unwrap();
        assert!(parsed.skipped_tags.is_empty());
    }

    /// Pincéis de terreno como operações: traço agrupado num undo, persistência no `.ahgt`,
    /// undo byte a byte e sync para o terreno vivo.
    #[test]
    fn test_terrain_brush_ops_persist_undo_and_sync() {
        use crate::scene::ops::{EditOp, History};
        use crate::scene::terrain::BrushMode;
        let dir = write_minimal_world();
        run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");
        let static_world = dir.path().join("world.xml");
        let ahgt = dir.path().join("world.data").join(BAKE_AHGT);
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
        run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");
        let static_world = dir.path().join("world.xml");
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
        run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");
        let static_world = dir.path().join("world.xml");
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
        run_bake(
            &dir.path().join("world.xml"),
            &dir.path().join("world.data"),
        )
        .expect("bake");
        let doc = crate::scene::doc::SceneDoc::load(&dir.path().join("world.xml")).unwrap();
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
