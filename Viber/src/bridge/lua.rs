//! Método `viber.lua` — executa Luau arbitrário no contexto da engine, é o
//! "evaluate script" do debug bridge (equivalente do `Runtime.evaluate` do
//! Chrome DevTools). Cliente: `viber debug lua 'return 1+1'`.
//!
//! Desenho (mesmo padrão do runtime de scripts de jogo, `src/luau.rs`):
//! - As closures Lua NÃO tocam no `World` — as leituras vêm de um snapshot
//!   ([`DebugView`]) construído no início da chamada e as escritas enfileiram
//!   [`DebugOp`]s aplicadas logo a seguir ao chunk, ainda no handler (PreUpdate)
//!   — os sistemas de gameplay vê-nas no MESMO frame.
//! - O código corre numa env persistente (REPL: globals sobrevivem entre
//!   chamadas), isolada dos scripts de jogo; para devolver um valor usa-se
//!   `return` no fim do chunk.
//! - O [`crate::luau::ScriptCtx`] é semeado com o player como "self": a API
//!   `viber.*` dos scripts (log, quest_*, toast, teleport_player, …) funciona
//!   na REPL sem código extra.
//! - Sem guard de instruções (Luau/mlua não expõe hooks): um `while true do
//!   end` na REPL congela o frame — igual ao que um script de página faz ao
//!   Chrome. Ferramenta de debug, risco aceite.

use std::collections::HashMap;
use std::sync::Arc;

use bevy::ecs::entity_disabling::Disabled;
use bevy::ecs::message::Messages;
use bevy::ecs::system::In;
use bevy::math::primitives::{Cuboid, Sphere};
use bevy::prelude::*;
use bevy_rapier3d::prelude::{Collider, RapierContextSimulation, RigidBody};

use crate::terrain::TerrainChunkMaterial;
use mlua::{FromLua, Lua, Table, Value};
use serde::Deserialize;
use serde_json::{Value as Json, json};

use super::{BrpResult, invalid, parse_params};
use crate::luau::LuaScriptHost;
use crate::player::Player;
use crate::recipes::spawn::OrbitCamera;
use crate::vitals::{Health, Xp};

/// Número máximo de entidades no snapshot (`entities()`/`find`), ordenadas
/// por distância ao player — mundos com terreno têm milhares de chunks.
const SNAPSHOT_CAP: usize = 4096;

// ---------------------------------------------------------------- snapshot

/// Uma entidade no snapshot de leitura (`viber.debug.entities`/`find`/`pos`).
pub struct EntityInfo {
    pub id: Entity,
    pub name: Option<String>,
    pub position: Option<Vec3>,
    pub disabled: bool,
    /// Nomes dos componentes (partilhados por arquétipo — barato).
    pub components: Arc<Vec<String>>,
    pub transform: Option<TransformInfo>,
    pub parent: Option<Entity>,
    pub children: Vec<Entity>,
    /// `Some(escondida?)` = tem `Visibility`.
    pub hidden: Option<bool>,
    pub collider: Option<ColliderSummary>,
    pub rigidbody: Option<String>,
    pub mesh: Option<MeshSummary>,
    pub material: Option<MaterialSummary>,
    pub light: Option<LightInfo>,
    /// Tem `LuaScriptRef` (script de jogo na entidade).
    pub scripted: bool,
}

/// Luz na entidade (`viber.debug.lights`/`stats`) — sombras são o maior
/// custo de render por luz, por isso vêm destacadas.
pub struct LightInfo {
    /// "point" | "spot" | "directional"
    pub kind: String,
    pub intensity: f32,
    pub shadows: bool,
    pub range: Option<f32>,
    pub color: Option<[f32; 3]>,
}

/// Tempos do ÚLTIMO step de física (`viber.debug.physics`) — contadores do
/// Rapier (`RapierContextSimulation.pipeline.counters`).
pub struct PhysicsInfo {
    pub enabled: bool,
    pub step_ms: f64,
    pub collision_detection_ms: f64,
    pub solver_ms: f64,
    pub ccd_ms: f64,
    pub islands_ms: f64,
    pub ncontacts: usize,
    pub nconstraints: usize,
}

/// Agregados do mundo inteiro (`viber.debug.stats`) — contados sobre TODAS
/// as entidades (sem o cap de 4096 do snapshot).
#[derive(Default)]
pub struct WorldStats {
    pub entities: usize,
    pub meshes: usize,
    pub colliders_total: usize,
    pub colliders_cuboid: usize,
    pub colliders_ball: usize,
    pub colliders_trimesh: usize,
    pub colliders_compound: usize,
    pub rigidbodies: usize,
    pub rigidbodies_dynamic: usize,
    pub rigidbodies_kinematic: usize,
    pub lights_point: usize,
    pub lights_spot: usize,
    pub lights_directional: usize,
    pub lights_with_shadows: usize,
    pub emitters: usize,
    pub scripted: usize,
    pub disabled: usize,
    /// Instâncias com [`crate::render_lod::CullDistance`] (props/erva de
    /// spawner) e quantas delas o culling por distância tem escondidas.
    pub cullable: usize,
    pub culled: usize,
    /// Instâncias com ladder de LOD, por tier ativo (0 = malha hero).
    pub lod_tier0: usize,
    pub lod_tier1: usize,
    pub lod_tier2: usize,
    /// Trocas de cena feitas no último frame e instâncias ainda em fila.
    /// `lod_pending` preso acima de zero = orçamento saturado.
    pub lod_swaps: usize,
    pub lod_pending: usize,
    /// Entradas dos ASSET STORES (`Assets::<T>::len()`), não entidades: é o
    /// número que denuncia leaks/churn de assets (o Bevy não faz GC — o
    /// `meshes` acima conta INSTÂNCIAS e não vê um material clonado que
    /// ninguém libertou). `assets_*` a subir monotonicamente numa sessão =
    /// algo está a criar assets por evento sem os devolver.
    pub assets_meshes: usize,
    pub assets_materials: usize,
    pub assets_images: usize,
}

/// Transform local + global (`viber.debug.transform`/`info`).
pub struct TransformInfo {
    pub translation: [f32; 3],
    /// Euler YXZ em graus `[pitch, yaw, roll]` (só leitura; a engine usa quats).
    pub euler: [f32; 3],
    pub scale: [f32; 3],
    /// Translation GLOBAL (pós-hierarquia), quando há `GlobalTransform`.
    pub global: Option<[f32; 3]>,
}

/// Resumo do shape Rapier (`viber.debug.collider`).
pub struct ColliderSummary {
    /// "cuboid" | "ball" | "trimesh" | "compound" | "outro"
    pub shape: String,
    pub half_extents: Option<[f32; 3]>,
    pub radius: Option<f32>,
    pub vertices: Option<u32>,
    pub shapes: Option<u32>,
}

/// Resumo do `Mesh3d` (`viber.debug.mesh`) — dados resolvidos de
/// `Assets<Mesh>` no snapshot (o chunk não tem acesso aos assets).
pub struct MeshSummary {
    pub topology: String,
    pub vertices: u32,
    pub indices: Option<u32>,
    pub has_normals: bool,
    pub has_uvs: bool,
    pub uv_count: u32,
    /// Bounds de `UV_0` (Float32x2) — QA de texturas/atlas.
    pub uv_min: Option<[f32; 2]>,
    pub uv_max: Option<[f32; 2]>,
}

/// Resumo do material PBR (`viber.debug.material`).
pub struct MaterialSummary {
    pub base_color: [f32; 4],
    pub metallic: f32,
    pub roughness: f32,
    pub unlit: bool,
    /// Textura resolvida de `Assets<Image>`: `[w, h]` ou None.
    pub base_color_texture: Option<[i64; 2]>,
    pub normal_map: Option<[i64; 2]>,
}

/// Estado do player no snapshot (`viber.debug.player`).
pub struct PlayerInfo {
    pub entity: Entity,
    pub position: Vec3,
    pub health: Option<(f32, f32)>,
    pub xp: Option<(u32, u32)>,
    pub speed: f32,
}

/// Estado da câmara orbital no snapshot (`viber.debug.camera`).
pub struct CameraInfo {
    pub position: Vec3,
    pub distance: f32,
    pub pitch: f32,
    pub yaw: f32,
    pub target: Option<String>,
}

/// Estado do relógio dia/noite no snapshot (`viber.debug.clock`).
pub struct ClockInfo {
    pub minute: f32,
    pub dawn: f32,
    pub dusk: f32,
    pub minutes_per_real_second: f32,
}

/// Vault no snapshot (`viber.debug.vault`).
pub struct VaultInfo {
    pub gold: u32,
    pub wood: u32,
    pub stone: u32,
    pub items: Vec<(String, u32)>,
}

/// Snapshot de leitura passado às closures como app data — fresco por cada
/// chamada (escritas NÃO são visíveis dentro do mesmo chunk, só no mundo).
#[derive(Default)]
pub struct DebugView {
    pub entities: Vec<EntityInfo>,
    pub player: Option<PlayerInfo>,
    /// Primeira entidade por nome exato (atalho de resolução).
    pub by_name: HashMap<String, Entity>,
    pub time_scale: f32,
    pub camera: Option<CameraInfo>,
    pub clock: Option<ClockInfo>,
    pub vault: Option<VaultInfo>,
    /// `{id → estado}` das quests (snapshot).
    pub quests: Vec<(String, String)>,
    /// Snapshot do profiler no início da chamada (`viber.debug.prof`/`fps`).
    pub prof: Json,
    /// Tempos do último step de física (Rapier) ou None sem física.
    pub physics: Option<PhysicsInfo>,
    /// Agregados do mundo inteiro (sem cap do snapshot).
    pub stats: WorldStats,
    /// Chão ao vivo: tuning de splat + pele das paredes do primeiro chunk
    /// (snapshot de `viber.debug.ground()`).
    pub ground: Option<GroundInfo>,
}

/// Estado do chão ao vivo (`viber.debug.ground{...}`): o tuning de splat
/// acumula entre chamadas — cada uma só passa o que muda.
#[derive(Resource, Debug, Clone, Copy, Default)]
struct GroundState {
    tuning: crate::terrain::splat::SplatTuning,
}

/// Snapshot do chão ao vivo (leitura de `viber.debug.ground()`).
#[derive(Debug, Clone, Copy)]
pub struct GroundInfo {
    pub tuning: crate::terrain::splat::SplatTuning,
    /// `walls_a` do primeiro chunk: tri_slope, tri_soft, strata_spacing,
    /// strata_strength.
    pub walls_a: [f32; 4],
    /// `walls_b`: rock_darken, streaks, moss, livre.
    pub walls_b: [f32; 4],
}

/// Fila de escritas enfileiradas pelas closures `viber.debug.*`, drenada
/// pelo handler após o chunk (`apply_ops`).
#[derive(Default)]
pub struct DebugOps(pub Vec<DebugOp>);

pub enum DebugOp {
    /// Posição absoluta, sem snap (o que o script escreve é o que fica).
    SetPos(Entity, Vec3),
    /// Teleporte do player: Y explícito.
    Teleport(Entity, Vec3),
    /// Teleporte com Y sentado no terreno (`viber.debug.tp(x, z)`).
    TeleportSnap(Entity, Vec3),
    /// Delta XZ em metros com Y no terreno (`viber.debug.move_player`).
    MoveBySnap(Entity, Vec2),
    Face(Entity, Vec3),
    Hide(Entity),
    Show(Entity),
    ToggleVis(Entity),
    Disable(Entity),
    Enable(Entity),
    Despawn(Entity),
    Toast(String),
    Heal(f32),
    Damage(f32),
    AddXp(u32),
    /// Deposita no vault — recurso (gold/wood/stone) ou item, pela mesma porta.
    Give(String, u32),
    SetSpeed(Entity, f32),
    SetTimeScale(f32),
    SpawnMarker {
        sphere: bool,
        pos: Vec3,
        size: Vec3,
        color: [f32; 3],
        name: String,
    },
    /// Remove todos os markers `debug:sphere:*`/`debug:box:*`.
    ClearMarkers,
    /// HP da entidade a zero (sem i-frames nem feedback — debug cru).
    Kill(Entity),
    /// HP absoluto do player (clamp a [0, max]).
    SetHp(f32),
    /// Soma yaw em graus em torno do Y.
    Rotate(Entity, f32),
    /// Escala uniforme.
    SetScale(Entity, f32),
    /// Câmara orbital: distância, pitch (graus) e/ou alvo (nome de entidade).
    SetCamera {
        distance: Option<f32>,
        pitch: Option<f32>,
        target: Option<String>,
    },
    /// Minuto do dia (0–1440, wrap).
    SetClock(f32),
    /// Redimensiona a janela primária (píxeis físicos) — QA de layouts
    /// responsivos: `viber.debug.set_window(900, 1300)` e o `@media` troca.
    SetWindow {
        width: f32,
        height: f32,
    },
    /// Sol AO VIVO (`viber.debug.sun{...}`): yaw/pitch rodam a
    /// DirectionalLight da cena E a direção publicada no shader do terreno
    /// (o chão tem sol próprio no uniform); illuminance/shadows tocam o
    /// componente.
    Sun {
        yaw: Option<f32>,
        pitch: Option<f32>,
        illuminance: Option<f32>,
        shadows: Option<bool>,
    },
    /// Chão AO VIVO (`viber.debug.ground{...}`): pele das paredes nos
    /// materiais de chunk (moss/streaks/rock_darken/tri_slope/tri_soft/
    /// strata_strength) + rebake dos splats com tuning novo
    /// (patchiness/gravel/dirt/forest/shore_width).
    Ground {
        moss: Option<f32>,
        vale_soft: Option<f32>,
        streaks: Option<f32>,
        rock_darken: Option<f32>,
        tri_slope: Option<f32>,
        tri_soft: Option<f32>,
        strata_strength: Option<f32>,
        patchiness: Option<f32>,
        gravel: Option<f32>,
        dirt: Option<f32>,
        forest: Option<f32>,
        shore_width: Option<f32>,
    },
}

/// Opções de `viber.debug.sun{...}` (tabela; campos ausentes = sem mudança).
#[derive(Default)]
pub struct SunOpts {
    pub yaw: Option<f32>,
    pub pitch: Option<f32>,
    pub illuminance: Option<f32>,
    pub shadows: Option<bool>,
}

impl FromLua for SunOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Ok(Self::default()),
            Value::Table(t) => Ok(Self {
                yaw: t.get("yaw")?,
                pitch: t.get("pitch")?,
                illuminance: t.get("illuminance")?,
                shadows: t.get("shadows")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "sun opts {yaw=?, pitch=?, illuminance=?, shadows=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.ground{...}` (tabela; campos ausentes = sem
/// mudança).
#[derive(Default)]
pub struct GroundOpts {
    pub moss: Option<f32>,
    pub vale_soft: Option<f32>,
    pub streaks: Option<f32>,
    pub rock_darken: Option<f32>,
    pub tri_slope: Option<f32>,
    pub tri_soft: Option<f32>,
    pub strata_strength: Option<f32>,
    pub patchiness: Option<f32>,
    pub gravel: Option<f32>,
    pub dirt: Option<f32>,
    pub forest: Option<f32>,
    pub shore_width: Option<f32>,
}

impl FromLua for GroundOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Err(mlua::Error::runtime(
                "ground{}: passa pelo menos um campo (moss, vale_soft, streaks, rock_darken, \
                 tri_slope, tri_soft, strata_strength, patchiness, gravel, dirt, forest, \
                 shore_width)",
            )),
            Value::Table(t) => Ok(Self {
                moss: t.get("moss")?,
                vale_soft: t.get("vale_soft")?,
                streaks: t.get("streaks")?,
                rock_darken: t.get("rock_darken")?,
                tri_slope: t.get("tri_slope")?,
                tri_soft: t.get("tri_soft")?,
                strata_strength: t.get("strata_strength")?,
                patchiness: t.get("patchiness")?,
                gravel: t.get("gravel")?,
                dirt: t.get("dirt")?,
                forest: t.get("forest")?,
                shore_width: t.get("shore_width")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "ground opts {moss=?, vale_soft=?, streaks=?, …}".into(),
                message: None,
            }),
        }
    }
}

/// Opções opcionais de `viber.debug.set_camera{...}`.
#[derive(Default)]
pub struct CameraOpts {
    pub distance: Option<f32>,
    pub pitch: Option<f32>,
    pub target: Option<String>,
}

impl FromLua for CameraOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Ok(Self::default()),
            Value::Table(t) => Ok(Self {
                distance: t.get("distance")?,
                pitch: t.get("pitch")?,
                target: t.get("target")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "camera opts {distance=?, pitch=?, target=?}".into(),
                message: None,
            }),
        }
    }
}

/// Argumento de entidade: bits numéricos (dos snapshots/`viber.tree` via
/// `find`) ou nome (exato primeiro, depois substring case-insensitive).
pub enum EntityArg {
    Id(u64),
    Name(String),
}

impl FromLua for EntityArg {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Integer(i) if i >= 0 => Ok(Self::Id(i as u64)),
            Value::Number(n) if n.fract() == 0.0 && n >= 0.0 => Ok(Self::Id(n as u64)),
            Value::String(s) => Ok(Self::Name(s.to_str()?.to_owned())),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "entity (bits numérico ou nome)".into(),
                message: None,
            }),
        }
    }
}

/// Constrói o snapshot de leitura a partir do mundo. Usa `iter_entities`
/// (entidades `Disabled` continuam visíveis — é precisamente o estado que
/// se quer inspecionar) em vez de queries (que as escondem por omissão).
fn build_view(world: &mut World) -> DebugView {
    let player = find_player(world);
    let origin = player.as_ref().map(|p| p.position);
    // A base (não o valor composto com o hit-stop) é o que o QA configura.
    let time_scale = world
        .get_resource::<crate::combat::BaseTimeScale>()
        .map(|b| b.0)
        .unwrap_or_else(|| world.resource::<Time<Virtual>>().relative_speed());
    let prof = crate::profiler::snapshot(world);
    // Sem GroundState (nunca chamado) mostra os DEFAULTS — o getter nunca é
    // nil num mundo com terreno.
    let ground = {
        let (walls_a, walls_b) = world
            .get_resource::<Assets<TerrainChunkMaterial>>()
            .and_then(|assets| assets.iter().next())
            .map(|(_, m)| {
                (
                    [
                        m.params.walls_a.x,
                        m.params.walls_a.y,
                        m.params.walls_a.z,
                        m.params.walls_a.w,
                    ],
                    [
                        m.params.walls_b.x,
                        m.params.walls_b.y,
                        m.params.walls_b.z,
                        m.params.walls_b.w,
                    ],
                )
            })
            .unwrap_or(([0.0; 4], [0.0; 4]));
        let tuning = world
            .get_resource::<GroundState>()
            .map(|s| s.tuning)
            .unwrap_or_default();
        let has_terrain = world.get_resource::<crate::terrain::runtime::TerrainRuntime>().is_some();
        has_terrain.then_some(GroundInfo {
            tuning,
            walls_a,
            walls_b,
        })
    };
    let camera = world.iter_entities().find_map(|e| {
        let cam = e.get::<OrbitCamera>()?;
        Some(CameraInfo {
            position: e
                .get::<GlobalTransform>()
                .map(|t| t.translation())
                .unwrap_or_default(),
            distance: cam.distance,
            pitch: cam.pitch_state_deg,
            yaw: cam.yaw_deg,
            target: cam.target.clone(),
        })
    });
    let clock = world
        .get_resource::<crate::worldsys::DayCycleState>()
        .map(|c| ClockInfo {
            minute: c.minute_of_day,
            dawn: c.dawn_minute,
            dusk: c.dusk_minute,
            minutes_per_real_second: c.minutes_per_real_second,
        });
    let vault = world
        .get_resource::<crate::economy::Vault>()
        .map(|v| VaultInfo {
            gold: v.gold,
            wood: v.wood,
            stone: v.stone,
            items: {
                let mut items: Vec<(String, u32)> =
                    v.items.iter().map(|(k, n)| (k.clone(), *n)).collect();
                items.sort();
                items
            },
        });
    let quests = {
        let vault_ref = world.get_resource::<crate::economy::Vault>();
        world
            .get_resource::<crate::quests::QuestLog>()
            .map(|log| {
                let mut list: Vec<(String, String)> = log
                    .defs
                    .iter()
                    .map(|d| {
                        (
                            d.id.clone(),
                            crate::quests::status_name(log.status(&d.id, vault_ref)).to_string(),
                        )
                    })
                    .collect();
                list.sort();
                list
            })
            .unwrap_or_default()
    };

    let mut infos: Vec<EntityInfo> = Vec::new();
    let mut by_name = HashMap::new();
    let mut stats = WorldStats::default();
    // Contadores da ladder de LOD: recurso, não varrimento de entidades.
    if let Some(lod) = world.get_resource::<crate::render_lod::MeshLodStats>() {
        stats.lod_swaps = lod.swaps_last_frame;
        stats.lod_pending = lod.pending;
    }
    // Física: `RapierContextSimulation` é COMPONENTE (contexto default).
    let physics = world.iter_entities().find_map(|e| {
        let sim = e.get::<RapierContextSimulation>()?;
        let counters = &sim.pipeline.counters;
        Some(PhysicsInfo {
            enabled: counters.enabled(),
            step_ms: counters.step_time.time_ms(),
            collision_detection_ms: counters.stages.collision_detection_time.time_ms(),
            solver_ms: counters.stages.solver_time.time_ms(),
            ccd_ms: counters.stages.ccd_time.time_ms(),
            islands_ms: counters.stages.island_construction_time.time_ms(),
            ncontacts: counters.solver.ncontacts,
            nconstraints: counters.solver.nconstraints,
        })
    });
    // Assets e cache de arquétipos: os nomes de componentes repetem-se por
    // arquétipo (partilhados via Arc); meshes/materiais resolvem-se já no
    // snapshot — os closures Lua não têm acesso a `Assets`.
    let meshes = world.get_resource::<bevy::asset::Assets<Mesh>>();
    let materials = world.get_resource::<bevy::asset::Assets<StandardMaterial>>();
    let images = world.get_resource::<bevy::asset::Assets<Image>>();
    stats.assets_meshes = meshes.map(bevy::asset::Assets::len).unwrap_or(0);
    stats.assets_materials = materials.map(bevy::asset::Assets::len).unwrap_or(0);
    stats.assets_images = images.map(bevy::asset::Assets::len).unwrap_or(0);
    let mut archetype_cache: HashMap<usize, Arc<Vec<String>>> = HashMap::new();
    for e in world.iter_entities() {
        let entity = e.id();
        let name = e.get::<Name>().map(|n| n.to_string());
        let position = e
            .get::<Transform>()
            .map(|t| t.translation)
            .or_else(|| e.get::<GlobalTransform>().map(|t| t.translation()));
        let disabled = e.get::<Disabled>().is_some();
        if let Some(name) = &name {
            by_name.entry(name.clone()).or_insert(entity);
        }

        // Componentes por arquétipo (cache pelo endereço do arquétipo, que
        // não muda durante a leitura).
        let archetype = e.archetype();
        let components = archetype_cache
            .entry(archetype as *const _ as usize)
            .or_insert_with(|| {
                Arc::new(
                    archetype
                        .components()
                        .iter()
                        .filter_map(|component_id| {
                            world
                                .components()
                                .get_info(*component_id)
                                .map(|info| info.name().to_string())
                        })
                        .collect(),
                )
            })
            .clone();

        let transform = e.get::<Transform>().map(|t| TransformInfo {
            translation: t.translation.to_array(),
            euler: {
                let (yaw, pitch, roll) = t.rotation.to_euler(EulerRot::YXZ);
                [pitch.to_degrees(), yaw.to_degrees(), roll.to_degrees()]
            },
            scale: t.scale.to_array(),
            global: e
                .get::<GlobalTransform>()
                .map(|g| g.translation().to_array()),
        });
        let parent = e.get::<ChildOf>().map(|c| c.0);
        let children = e.get::<Children>().map(|c| c.to_vec()).unwrap_or_default();
        let hidden = e.get::<Visibility>().map(|v| *v == Visibility::Hidden);
        let collider = e.get::<Collider>().map(collider_summary);
        let rigidbody = e.get::<RigidBody>().map(|rb| format!("{rb:?}"));
        let mesh = e
            .get::<Mesh3d>()
            .and_then(|m| meshes.as_ref()?.get(&m.0).map(mesh_summary));
        let material = e.get::<MeshMaterial3d<StandardMaterial>>().and_then(|m| {
            materials
                .as_ref()?
                .get(&m.0)
                .map(|mat| material_summary(mat, images))
        });
        let light = if let Some(point) = e.get::<PointLight>() {
            Some(LightInfo {
                kind: "point".into(),
                intensity: point.intensity,
                shadows: point.shadow_maps_enabled,
                range: Some(point.range),
                color: Some(color_rgb(point.color)),
            })
        } else if let Some(spot) = e.get::<SpotLight>() {
            Some(LightInfo {
                kind: "spot".into(),
                intensity: spot.intensity,
                shadows: spot.shadow_maps_enabled,
                range: Some(spot.range),
                color: Some(color_rgb(spot.color)),
            })
        } else {
            e.get::<DirectionalLight>().map(|directional| LightInfo {
                kind: "directional".into(),
                intensity: directional.illuminance,
                shadows: directional.shadow_maps_enabled,
                range: None,
                color: Some(color_rgb(directional.color)),
            })
        };
        let scripted = e.get::<crate::luau::LuaScriptRef>().is_some();

        // Agregados sobre o mundo INTEIRO (antes do cap do snapshot).
        stats.entities += 1;
        stats.disabled += usize::from(disabled);
        stats.scripted += usize::from(scripted);
        stats.meshes += usize::from(mesh.is_some());
        if let Some(collider) = &collider {
            stats.colliders_total += 1;
            match collider.shape.as_str() {
                "cuboid" => stats.colliders_cuboid += 1,
                "ball" => stats.colliders_ball += 1,
                "trimesh" => stats.colliders_trimesh += 1,
                "compound" => stats.colliders_compound += 1,
                _ => {}
            }
        }
        if let Some(rigidbody) = &rigidbody {
            stats.rigidbodies += 1;
            if rigidbody.contains("Dynamic") {
                stats.rigidbodies_dynamic += 1;
            } else if rigidbody.contains("Kinematic") {
                stats.rigidbodies_kinematic += 1;
            }
        }
        if let Some(light) = &light {
            match light.kind.as_str() {
                "point" => stats.lights_point += 1,
                "spot" => stats.lights_spot += 1,
                _ => stats.lights_directional += 1,
            }
            stats.lights_with_shadows += usize::from(light.shadows);
        }
        stats.emitters += usize::from(e.get::<crate::particles::ParticleEmitter>().is_some());

        // LOD de render: quanto do mundo o culling está mesmo a poupar.
        if e.get::<crate::render_lod::CullDistance>().is_some() {
            stats.cullable += 1;
            if e.get::<bevy::prelude::Visibility>() == Some(&bevy::prelude::Visibility::Hidden) {
                stats.culled += 1;
            }
        }
        if let Some(lod) = e.get::<crate::render_lod::MeshLod>() {
            match lod.current {
                0 => stats.lod_tier0 += 1,
                1 => stats.lod_tier1 += 1,
                _ => stats.lod_tier2 += 1,
            }
        }

        infos.push(EntityInfo {
            id: entity,
            name,
            position,
            disabled,
            components,
            transform,
            parent,
            children,
            hidden,
            collider,
            rigidbody,
            mesh,
            material,
            light,
            scripted,
        });
    }
    // Cap nearest-first: sem player, fica a ordem natural do mundo.
    if let Some(origin) = origin {
        infos.sort_by(|a, b| {
            let da = a
                .position
                .map(|p| p.distance_squared(origin))
                .unwrap_or(f32::MAX);
            let db = b
                .position
                .map(|p| p.distance_squared(origin))
                .unwrap_or(f32::MAX);
            da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
        });
    }
    infos.truncate(SNAPSHOT_CAP);

    DebugView {
        entities: infos,
        player,
        by_name,
        time_scale,
        camera,
        clock,
        vault,
        quests,
        prof,
        physics,
        stats,
        ground,
    }
}

/// RGB de um `Color` (sRGB, 0..1).
fn color_rgb(color: Color) -> [f32; 3] {
    let srgba = color.to_srgba();
    [srgba.red, srgba.green, srgba.blue]
}

/// Resumo do shape de um `Collider` Rapier (downcasts parry).
fn collider_summary(collider: &Collider) -> ColliderSummary {
    let shape = &collider.raw;
    let mut summary = ColliderSummary {
        shape: "outro".into(),
        half_extents: None,
        radius: None,
        vertices: None,
        shapes: None,
    };
    if let Some(cuboid) = shape.as_cuboid() {
        summary.shape = "cuboid".into();
        summary.half_extents = Some(cuboid.half_extents.to_array());
    } else if let Some(ball) = shape.as_ball() {
        summary.shape = "ball".into();
        summary.radius = Some(ball.radius);
    } else if let Some(trimesh) = shape.as_trimesh() {
        summary.shape = "trimesh".into();
        summary.vertices = Some(trimesh.vertices().len() as u32);
    } else if let Some(compound) = shape.as_compound() {
        summary.shape = "compound".into();
        summary.shapes = Some(compound.shapes().len() as u32);
    }
    summary
}

/// Resumo do `Mesh` (contagens + bounds de UV_0).
fn mesh_summary(mesh: &Mesh) -> MeshSummary {
    let uvs = mesh.attribute(Mesh::ATTRIBUTE_UV_0);
    let mut summary = MeshSummary {
        topology: format!("{:?}", mesh.primitive_topology()),
        vertices: mesh.count_vertices() as u32,
        indices: mesh.indices().map(|indices| indices.len() as u32),
        has_normals: mesh.attribute(Mesh::ATTRIBUTE_NORMAL).is_some(),
        has_uvs: uvs.is_some(),
        uv_count: uvs.map(|uvs| uvs.len() as u32).unwrap_or(0),
        uv_min: None,
        uv_max: None,
    };
    if let Some(uvs) = uvs {
        let mut min = [f32::MAX; 2];
        let mut max = [f32::MIN; 2];
        let mut seen = false;
        let scan =
            |values: &[[f32; 2]], min: &mut [f32; 2], max: &mut [f32; 2], seen: &mut bool| {
                for uv in values {
                    *seen = true;
                    for axis in 0..2 {
                        min[axis] = min[axis].min(uv[axis]);
                        max[axis] = max[axis].max(uv[axis]);
                    }
                }
            };
        match uvs {
            bevy::mesh::VertexAttributeValues::Float32x2(values) => {
                scan(values, &mut min, &mut max, &mut seen)
            }
            bevy::mesh::VertexAttributeValues::Unorm8x2(values) => {
                // u8 normalizado → 0..1
                let scaled: Vec<[f32; 2]> = values
                    .iter()
                    .map(|uv| [uv[0] as f32 / 255.0, uv[1] as f32 / 255.0])
                    .collect();
                scan(&scaled, &mut min, &mut max, &mut seen);
            }
            bevy::mesh::VertexAttributeValues::Unorm16x2(values) => {
                let scaled: Vec<[f32; 2]> = values
                    .iter()
                    .map(|uv| [uv[0] as f32 / 65535.0, uv[1] as f32 / 65535.0])
                    .collect();
                scan(&scaled, &mut min, &mut max, &mut seen);
            }
            _ => {}
        }
        if seen {
            summary.uv_min = Some(min);
            summary.uv_max = Some(max);
        }
    }
    summary
}

/// Resumo do material PBR + texturas resolvidas (`{w,h,format}` como string).
fn material_summary(
    material: &StandardMaterial,
    images: Option<&bevy::asset::Assets<Image>>,
) -> MaterialSummary {
    let mut summary = MaterialSummary {
        base_color: {
            let srgba = material.base_color.to_srgba();
            [srgba.red, srgba.green, srgba.blue, srgba.alpha]
        },
        metallic: material.metallic,
        roughness: material.perceptual_roughness,
        unlit: material.unlit,
        base_color_texture: None,
        normal_map: None,
    };
    let texture_dims = |handle: Option<&Handle<Image>>| -> Option<[i64; 2]> {
        let image = images?.get(handle?)?;
        Some([image.width() as i64, image.height() as i64])
    };
    summary.base_color_texture = texture_dims(material.base_color_texture.as_ref());
    summary.normal_map = texture_dims(material.normal_map_texture.as_ref());
    summary
}

/// Player no snapshot: direto por `iter_entities` (visível mesmo Disabled).
fn find_player(world: &mut World) -> Option<PlayerInfo> {
    for e in world.iter_entities() {
        let Some(player) = e.get::<Player>() else {
            continue;
        };
        let position = e
            .get::<Transform>()
            .map(|t| t.translation)
            .or_else(|| e.get::<GlobalTransform>().map(|t| t.translation()))
            .unwrap_or_default();
        return Some(PlayerInfo {
            entity: e.id(),
            position,
            health: e.get::<Health>().map(|h| (h.current, h.max)),
            xp: e.get::<Xp>().map(|x| (x.current, x.next)),
            speed: player.speed,
        });
    }
    None
}

// ---------------------------------------------------------------- handler

/// Método BRP `viber.lua` — params `{ "code": "..." }`, resposta
/// `{ ok, result|error, applied, warnings }`.
pub fn eval(params: In<Option<Json>>, world: &mut World) -> BrpResult {
    #[derive(Deserialize)]
    struct Params {
        code: String,
    }
    let params: Params = parse_params(params.0)?;
    if world.get_resource::<LuaScriptHost>().is_none() {
        return Err(invalid(
            "runtime Luau inativo — `viber run` adiciona sempre o LuauScriptPlugin; \
             a testar contra outra App?"
                .into(),
        ));
    }

    let view = build_view(world);
    let elapsed = world.resource::<Time>().elapsed_secs_f64();
    let mut warnings: Vec<String> = Vec::new();
    // `viber.ground_below` na REPL: handle de leitura do terreno, lido
    // ANTES do empréstimo mutável do host.
    let terrain_reader = world
        .get_resource::<crate::terrain::runtime::TerrainRuntime>()
        .map(|rt| rt.reader());

    let (ok, result, error, ops) = {
        let host = world.resource_mut::<LuaScriptHost>();
        // Self = player: a API `viber.*` dos scripts funciona na REPL (log,
        // quest_*, toast, heal_player, teleport_player…). dt = 1.0 para que
        // move_towards/move_by sejam 1:1 em metros por chamada.
        if let Some(mut ctx) = host.lua.app_data_mut::<crate::luau::ScriptCtx>() {
            ctx.entity = view.player.as_ref().map(|p| p.entity);
            ctx.origin = view.player.as_ref().map(|p| p.position).unwrap_or_default();
            ctx.player = view.player.as_ref().map(|p| p.position);
            ctx.dt = 1.0;
            ctx.elapsed = elapsed;
            ctx.terrain = terrain_reader;
        }
        host.lua.set_app_data(view);
        host.lua.set_app_data(DebugOps::default());
        if let Err(e) = ensure_debug_api(&host.lua) {
            return Err(invalid(format!("falha a instalar viber.debug: {e}")));
        }

        let env: Table = match host.lua.named_registry_value("viber_debug_env") {
            Ok(env) => env,
            Err(_) => {
                let env = host
                    .lua
                    .create_table()
                    .and_then(|env| {
                        let mt = host.lua.create_table()?;
                        mt.set("__index", host.lua.globals())?;
                        env.set_metatable(Some(mt));
                        Ok(env)
                    })
                    .map_err(|e| invalid(format!("falha a criar env da REPL: {e}")))?;
                let _ = host
                    .lua
                    .set_named_registry_value("viber_debug_env", env.clone());
                env
            }
        };

        let run = host
            .lua
            .load(&params.code)
            .set_name("=bridge")
            .set_environment(env)
            .into_function()
            .and_then(|chunk| chunk.call::<Value>(()));
        let (ok, result, error) = match run {
            Ok(value) => (true, Some(value_to_json(&value, 0)), None),
            Err(e) => (false, None, Some(e.to_string())),
        };
        // Ops enfileiradas (mesmo com erro — os efeitos antes do throw ficam).
        let ops = host
            .lua
            .app_data_mut::<DebugOps>()
            .map(|mut ops| std::mem::take(&mut ops.0))
            .unwrap_or_default();
        // Limpa o snapshot: uma closure de SCRIPT DE JOGO que chamasse
        // viber.debug veria dados podres da última REPL — melhor falhar.
        host.lua.set_app_data(DebugView::default());
        (ok, result, error, ops)
    };

    let applied = apply_ops(world, ops, &mut warnings);
    let mut response = json!({ "ok": ok, "applied": applied, "warnings": warnings });
    if let Some(result) = result {
        response["result"] = result;
    }
    if let Some(error) = error {
        response["error"] = json!(error);
    }
    Ok(response)
}

// ---------------------------------------------------------------- API Lua

/// Instala `viber.debug` uma vez (idempotente) — leituras do [`DebugView`],
/// escritas para a fila [`DebugOps`].
fn ensure_debug_api(lua: &Lua) -> mlua::Result<()> {
    let viber: Table = lua.globals().get("viber")?;
    if viber.get::<Table>("debug").is_ok() {
        return Ok(());
    }

    fn push(lua: &Lua, op: DebugOp) -> mlua::Result<()> {
        lua.app_data_mut::<DebugOps>()
            .expect("DebugOps semeado por eval")
            .0
            .push(op);
        Ok(())
    }

    /// Resolve o argumento de entidade contra o snapshot.
    fn resolve(lua: &Lua, arg: EntityArg) -> mlua::Result<Entity> {
        let view = lua
            .app_data_ref::<DebugView>()
            .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
        match arg {
            EntityArg::Id(bits) => Ok(Entity::from_bits(bits)),
            EntityArg::Name(name) => {
                if let Some(id) = view.by_name.get(&name) {
                    return Ok(*id);
                }
                let needle = name.to_ascii_lowercase();
                view.entities
                    .iter()
                    .find(|info| {
                        info.name
                            .as_deref()
                            .is_some_and(|n| n.to_ascii_lowercase().contains(&needle))
                    })
                    .map(|info| info.id)
                    .ok_or_else(|| {
                        mlua::Error::runtime(format!("entidade '{name}' não encontrada"))
                    })
            }
        }
    }

    fn player_entity(lua: &Lua) -> mlua::Result<Entity> {
        lua.app_data_ref::<DebugView>()
            .as_ref()
            .and_then(|view| view.player.as_ref())
            .map(|p| p.entity)
            .ok_or_else(|| mlua::Error::runtime("sem player no mundo"))
    }

    let api = lua.create_table()?;

    // ── Leitura (snapshot do início da chamada) ─────────────────────────
    api.set(
        "entities",
        lua.create_function(|lua, radius: Option<f32>| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let origin = view.player.as_ref().map(|p| p.position);
            let mut out = Vec::new();
            for info in &view.entities {
                if let (Some(radius), Some(origin)) = (radius, origin) {
                    match info.position {
                        Some(pos) if pos.distance(origin) <= radius => {}
                        _ => continue,
                    }
                }
                let entry = lua.create_table()?;
                entry.raw_set("id", info.id.to_bits() as i64)?;
                if let Some(name) = &info.name {
                    entry.raw_set("name", name.as_str())?;
                }
                if let Some(pos) = info.position {
                    entry.raw_set("x", pos.x)?;
                    entry.raw_set("y", pos.y)?;
                    entry.raw_set("z", pos.z)?;
                }
                entry.raw_set("disabled", info.disabled)?;
                out.push(entry);
            }
            lua.create_sequence_from(out)
        })?,
    )?;

    api.set(
        "find",
        lua.create_function(|lua, name: String| {
            resolve(lua, EntityArg::Name(name)).map(|e| e.to_bits() as i64)
        })?,
    )?;

    api.set(
        "find_all",
        lua.create_function(|lua, name: String| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let needle = name.to_ascii_lowercase();
            let ids: Vec<i64> = view
                .entities
                .iter()
                .filter(|info| {
                    info.name
                        .as_deref()
                        .is_some_and(|n| n.to_ascii_lowercase().contains(&needle))
                })
                .map(|info| info.id.to_bits() as i64)
                .collect();
            Ok(ids)
        })?,
    )?;

    api.set(
        "pos",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            view.entities
                .iter()
                .find(|info| info.id == entity)
                .and_then(|info| info.position)
                .map(|p| (p.x, p.y, p.z))
                .ok_or_else(|| mlua::Error::runtime("entidade fora do snapshot (ou sem posição)"))
        })?,
    )?;

    // ── Introspeção (snapshot: transform, mesh, material, collider, ids) ─
    api.set(
        "info",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let info = view
                .entities
                .iter()
                .find(|info| info.id == entity)
                .ok_or_else(|| mlua::Error::runtime("entidade fora do snapshot"))?;
            info_table(lua, info)
        })?,
    )?;
    api.set(
        "transform",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            view.entities
                .iter()
                .find(|info| info.id == entity)
                .and_then(|info| info.transform.as_ref())
                .map(|t| transform_table(lua, t))
                .transpose()?
                .ok_or_else(|| mlua::Error::runtime("entidade sem Transform (ou fora do snapshot)"))
        })?,
    )?;
    api.set(
        "mesh",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            view.entities
                .iter()
                .find(|info| info.id == entity)
                .and_then(|info| info.mesh.as_ref())
                .map(|m| mesh_table(lua, m))
                .transpose()?
                .ok_or_else(|| {
                    mlua::Error::runtime("entidade sem Mesh3d (ou mesh fora dos assets)")
                })
        })?,
    )?;
    api.set(
        "material",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            view.entities
                .iter()
                .find(|info| info.id == entity)
                .and_then(|info| info.material.as_ref())
                .map(|m| material_table(lua, m))
                .transpose()?
                .ok_or_else(|| mlua::Error::runtime("entidade sem StandardMaterial"))
        })?,
    )?;
    api.set(
        "collider",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            view.entities
                .iter()
                .find(|info| info.id == entity)
                .and_then(|info| info.collider.as_ref())
                .map(|c| collider_table(lua, c))
                .transpose()?
                .ok_or_else(|| mlua::Error::runtime("entidade sem Collider"))
        })?,
    )?;
    api.set(
        "components",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let info = view
                .entities
                .iter()
                .find(|info| info.id == entity)
                .ok_or_else(|| mlua::Error::runtime("entidade fora do snapshot"))?;
            lua.create_sequence_from(info.components.iter().map(String::as_str))
                .map(Value::Table)
        })?,
    )?;

    // ── Bulk / profiling (dados em volume à volta do player) ────────────
    api.set(
        "physics",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(physics) = view.physics.as_ref() else {
                return Ok(Value::Nil);
            };
            let table = lua.create_table()?;
            table.raw_set("enabled", physics.enabled)?;
            table.raw_set("step_ms", physics.step_ms)?;
            table.raw_set("collision_detection_ms", physics.collision_detection_ms)?;
            table.raw_set("solver_ms", physics.solver_ms)?;
            table.raw_set("ccd_ms", physics.ccd_ms)?;
            table.raw_set("islands_ms", physics.islands_ms)?;
            table.raw_set("ncontacts", physics.ncontacts)?;
            table.raw_set("nconstraints", physics.nconstraints)?;
            Ok(Value::Table(table))
        })?,
    )?;
    api.set(
        "stats",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let s = &view.stats;
            let table = lua.create_table()?;
            table.raw_set("entities", s.entities)?;
            table.raw_set("meshes", s.meshes)?;
            table.raw_set(
                "assets",
                {
                    let t = lua.create_table()?;
                    t.raw_set("meshes", s.assets_meshes)?;
                    t.raw_set("materials", s.assets_materials)?;
                    t.raw_set("images", s.assets_images)?;
                    t
                },
            )?;
            table.raw_set("colliders", s.colliders_total)?;
            table.raw_set("colliders_cuboid", s.colliders_cuboid)?;
            table.raw_set("colliders_ball", s.colliders_ball)?;
            table.raw_set("colliders_trimesh", s.colliders_trimesh)?;
            table.raw_set("colliders_compound", s.colliders_compound)?;
            table.raw_set("rigidbodies", s.rigidbodies)?;
            table.raw_set("rigidbodies_dynamic", s.rigidbodies_dynamic)?;
            table.raw_set("rigidbodies_kinematic", s.rigidbodies_kinematic)?;
            table.raw_set("lights_point", s.lights_point)?;
            table.raw_set("lights_spot", s.lights_spot)?;
            table.raw_set("lights_directional", s.lights_directional)?;
            table.raw_set("lights_with_shadows", s.lights_with_shadows)?;
            table.raw_set("emitters", s.emitters)?;
            table.raw_set("cullable", s.cullable)?;
            table.raw_set("culled", s.culled)?;
            table.raw_set("lod_tier0", s.lod_tier0)?;
            table.raw_set("lod_tier1", s.lod_tier1)?;
            table.raw_set("lod_tier2", s.lod_tier2)?;
            table.raw_set("lod_swaps", s.lod_swaps)?;
            table.raw_set("lod_pending", s.lod_pending)?;
            table.raw_set("scripted", s.scripted)?;
            table.raw_set("disabled", s.disabled)?;
            // Do profiler: render/chunks/scripts ativos — os melhores
            // indicadores de custo por frame.
            if let Some(scripts) = view.prof.get("scripts") {
                table.raw_set(
                    "scripts_total",
                    json_to_lua(lua, scripts.get("total").unwrap_or(&Json::Null))?,
                )?;
                table.raw_set(
                    "scripts_active",
                    json_to_lua(lua, scripts.get("active").unwrap_or(&Json::Null))?,
                )?;
            }
            if let Some(fps) = view.prof.get("fps").and_then(Json::as_f64) {
                table.raw_set("fps", fps)?;
            }
            if let Some(frame_ms) = view
                .prof
                .get("frame_ms")
                .and_then(|f| f.get("avg"))
                .and_then(Json::as_f64)
            {
                table.raw_set("frame_ms_avg", frame_ms)?;
            }
            if let Some(chunks) = view.prof.get("terrain_chunks").and_then(Json::as_u64) {
                table.raw_set("terrain_chunks", chunks)?;
            }
            Ok(Value::Table(table))
        })?,
    )?;
    api.set(
        "colliders",
        lua.create_function(|lua, radius: Option<f32>| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let origin = view.player.as_ref().map(|p| p.position);
            let mut out = Vec::new();
            for info in view.entities.iter().filter(|info| info.collider.is_some()) {
                if let (Some(radius), Some(origin)) = (radius, origin) {
                    match info.position {
                        Some(pos) if pos.distance(origin) <= radius => {}
                        _ => continue,
                    }
                }
                let Some(collider) = info.collider.as_ref() else {
                    continue;
                };
                let table = lua.create_table()?;
                table.raw_set("id", info.id.to_bits() as i64)?;
                if let Some(name) = &info.name {
                    table.raw_set("name", name.as_str())?;
                }
                if let Some(pos) = info.position {
                    table.raw_set("x", pos.x)?;
                    table.raw_set("y", pos.y)?;
                    table.raw_set("z", pos.z)?;
                }
                table.raw_set("shape", collider.shape.as_str())?;
                if let Some(he) = collider.half_extents {
                    table.raw_set("hx", he[0])?;
                    table.raw_set("hy", he[1])?;
                    table.raw_set("hz", he[2])?;
                }
                if let Some(radius) = collider.radius {
                    table.raw_set("radius", radius)?;
                }
                if let Some(vertices) = collider.vertices {
                    table.raw_set("vertices", vertices)?;
                }
                if let Some(rigidbody) = &info.rigidbody {
                    table.raw_set("rigidbody", rigidbody.as_str())?;
                }
                out.push(table);
                if out.len() >= 256 {
                    break;
                }
            }
            lua.create_sequence_from(out).map(Value::Table)
        })?,
    )?;
    api.set(
        "lights",
        lua.create_function(|lua, radius: Option<f32>| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let origin = view.player.as_ref().map(|p| p.position);
            let mut out = Vec::new();
            for info in view.entities.iter().filter(|info| info.light.is_some()) {
                if let (Some(radius), Some(origin)) = (radius, origin) {
                    match info.position {
                        Some(pos) if pos.distance(origin) <= radius => {}
                        _ => continue,
                    }
                }
                let Some(light) = info.light.as_ref() else {
                    continue;
                };
                let table = lua.create_table()?;
                table.raw_set("id", info.id.to_bits() as i64)?;
                if let Some(name) = &info.name {
                    table.raw_set("name", name.as_str())?;
                }
                if let Some(pos) = info.position {
                    table.raw_set("x", pos.x)?;
                    table.raw_set("y", pos.y)?;
                    table.raw_set("z", pos.z)?;
                }
                table.raw_set("kind", light.kind.as_str())?;
                table.raw_set("intensity", light.intensity)?;
                table.raw_set("shadows", light.shadows)?;
                if let Some(range) = light.range {
                    table.raw_set("range", range)?;
                }
                out.push(table);
            }
            lua.create_sequence_from(out).map(Value::Table)
        })?,
    )?;
    api.set(
        "around",
        lua.create_function(|lua, (radius, limit): (f32, Option<f64>)| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let limit = limit.unwrap_or(64.0).clamp(1.0, 128.0) as usize;
            let Some(origin) = view.player.as_ref().map(|p| p.position) else {
                return Err(mlua::Error::runtime(
                    "sem player — around precisa do player",
                ));
            };
            let mut out = Vec::new();
            // infos já vêm ordenadas por distância ao player.
            for info in &view.entities {
                let Some(pos) = info.position else {
                    continue;
                };
                let distance = pos.distance(origin);
                if distance > radius {
                    break; // ordenado: o resto só pode estar mais longe
                }
                let table = lua.create_table()?;
                table.raw_set("id", info.id.to_bits() as i64)?;
                if let Some(name) = &info.name {
                    table.raw_set("name", name.as_str())?;
                }
                table.raw_set("distance", distance)?;
                table.raw_set("x", pos.x)?;
                table.raw_set("y", pos.y)?;
                table.raw_set("z", pos.z)?;
                table.raw_set("disabled", info.disabled)?;
                table.raw_set("scripted", info.scripted)?;
                if let Some(collider) = &info.collider {
                    table.raw_set("collider", collider.shape.as_str())?;
                }
                if let Some(mesh) = &info.mesh {
                    table.raw_set("mesh_vertices", mesh.vertices)?;
                }
                if let Some(light) = &info.light {
                    table.raw_set("light", light.kind.as_str())?;
                    table.raw_set("light_shadows", light.shadows)?;
                }
                if let Some(rigidbody) = &info.rigidbody {
                    table.raw_set("rigidbody", rigidbody.as_str())?;
                }
                out.push(table);
                if out.len() >= limit {
                    break;
                }
            }
            lua.create_sequence_from(out).map(Value::Table)
        })?,
    )?;

    api.set(
        "player",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(p) = view.player.as_ref() else {
                return Ok(Value::Nil);
            };
            let table = lua.create_table()?;
            table.raw_set("id", p.entity.to_bits() as i64)?;
            table.raw_set("x", p.position.x)?;
            table.raw_set("y", p.position.y)?;
            table.raw_set("z", p.position.z)?;
            if let Some((current, max)) = p.health {
                table.raw_set("hp", current)?;
                table.raw_set("max_hp", max)?;
            }
            if let Some((current, next)) = p.xp {
                table.raw_set("xp", current)?;
                table.raw_set("xp_next", next)?;
            }
            table.raw_set("speed", p.speed)?;
            Ok(Value::Table(table))
        })?,
    )?;

    api.set(
        "time_scale",
        lua.create_function(|lua, ()| {
            lua.app_data_ref::<DebugView>()
                .map(|view| view.time_scale)
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))
        })?,
    )?;

    // ── Escrita (aplicada no mesmo frame, após o chunk) ─────────────────
    api.set(
        "set_pos",
        lua.create_function(|lua, (arg, x, y, z): (EntityArg, f32, f32, f32)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::SetPos(entity, Vec3::new(x, y, z)))
        })?,
    )?;

    api.set(
        "teleport",
        lua.create_function(|lua, (x, y, z): (f32, f32, f32)| {
            let entity = player_entity(lua)?;
            push(lua, DebugOp::Teleport(entity, Vec3::new(x, y, z)))
        })?,
    )?;

    api.set(
        "tp",
        lua.create_function(|lua, (x, z): (f32, f32)| {
            let entity = player_entity(lua)?;
            push(lua, DebugOp::TeleportSnap(entity, Vec3::new(x, 0.0, z)))
        })?,
    )?;

    // move_to(id, x, z) — tp generalizado: qualquer entidade, Y no terreno.
    api.set(
        "move_to",
        lua.create_function(|lua, (arg, x, z): (EntityArg, f32, f32)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::TeleportSnap(entity, Vec3::new(x, 0.0, z)))
        })?,
    )?;

    api.set(
        "move_player",
        lua.create_function(|lua, (dx, dz): (f32, f32)| {
            let entity = player_entity(lua)?;
            push(lua, DebugOp::MoveBySnap(entity, Vec2::new(dx, dz)))
        })?,
    )?;

    api.set(
        "face",
        lua.create_function(|lua, (x, z): (f32, f32)| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let entity = player_entity(lua)?;
            let y = view.player.as_ref().map(|p| p.position.y).unwrap_or(0.0);
            push(lua, DebugOp::Face(entity, Vec3::new(x, y, z)))
        })?,
    )?;

    api.set(
        "hide",
        lua.create_function(|lua, arg: EntityArg| push(lua, DebugOp::Hide(resolve(lua, arg)?)))?,
    )?;
    api.set(
        "show",
        lua.create_function(|lua, arg: EntityArg| push(lua, DebugOp::Show(resolve(lua, arg)?)))?,
    )?;
    api.set(
        "toggle_vis",
        lua.create_function(|lua, arg: EntityArg| {
            push(lua, DebugOp::ToggleVis(resolve(lua, arg)?))
        })?,
    )?;

    api.set(
        "disable",
        lua.create_function(|lua, arg: EntityArg| push(lua, DebugOp::Disable(resolve(lua, arg)?)))?,
    )?;
    api.set(
        "enable",
        lua.create_function(|lua, arg: EntityArg| push(lua, DebugOp::Enable(resolve(lua, arg)?)))?,
    )?;

    api.set(
        "despawn",
        lua.create_function(|lua, arg: EntityArg| push(lua, DebugOp::Despawn(resolve(lua, arg)?)))?,
    )?;

    api.set(
        "heal",
        lua.create_function(|lua, amount: f32| push(lua, DebugOp::Heal(amount)))?,
    )?;
    api.set(
        "damage",
        lua.create_function(|lua, amount: f32| push(lua, DebugOp::Damage(amount)))?,
    )?;
    api.set(
        "xp",
        lua.create_function(|lua, gain: u32| push(lua, DebugOp::AddXp(gain)))?,
    )?;
    api.set(
        "give",
        lua.create_function(|lua, (what, amount): (String, u32)| {
            push(lua, DebugOp::Give(what, amount))
        })?,
    )?;

    api.set(
        "set_speed",
        lua.create_function(|lua, speed: f32| {
            let entity = player_entity(lua)?;
            push(lua, DebugOp::SetSpeed(entity, speed))
        })?,
    )?;
    api.set(
        "set_time_scale",
        lua.create_function(|lua, scale: f32| push(lua, DebugOp::SetTimeScale(scale)))?,
    )?;

    api.set(
        "toast",
        lua.create_function(|lua, msg: String| push(lua, DebugOp::Toast(msg)))?,
    )?;

    // ── Combate cru / transform / câmara / relógio ──────────────────────
    api.set(
        "kill",
        lua.create_function(|lua, arg: EntityArg| push(lua, DebugOp::Kill(resolve(lua, arg)?)))?,
    )?;
    api.set(
        "set_hp",
        lua.create_function(|lua, hp: f32| push(lua, DebugOp::SetHp(hp)))?,
    )?;
    api.set(
        "clear_markers",
        lua.create_function(|lua, ()| push(lua, DebugOp::ClearMarkers))?,
    )?;
    api.set(
        "rotate",
        lua.create_function(|lua, (arg, deg): (EntityArg, f32)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::Rotate(entity, deg))
        })?,
    )?;
    api.set(
        "set_scale",
        lua.create_function(|lua, (arg, s): (EntityArg, f32)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::SetScale(entity, s))
        })?,
    )?;
    api.set(
        "set_camera",
        lua.create_function(|lua, opts: CameraOpts| {
            push(
                lua,
                DebugOp::SetCamera {
                    distance: opts.distance,
                    pitch: opts.pitch,
                    target: opts.target,
                },
            )
        })?,
    )?;
    api.set(
        "set_clock",
        lua.create_function(|lua, minute: f32| push(lua, DebugOp::SetClock(minute)))?,
    )?;
    api.set(
        "set_window",
        lua.create_function(|lua, (width, height): (f32, f32)| {
            push(lua, DebugOp::SetWindow { width, height })
        })?,
    )?;
    api.set(
        "sun",
        lua.create_function(|lua, opts: SunOpts| {
            push(
                lua,
                DebugOp::Sun {
                    yaw: opts.yaw,
                    pitch: opts.pitch,
                    illuminance: opts.illuminance,
                    shadows: opts.shadows,
                },
            )
        })?,
    )?;
    api.set(
        "ground",
        lua.create_function(|lua, opts: GroundOpts| {
            push(
                lua,
                DebugOp::Ground {
                    moss: opts.moss,
                    vale_soft: opts.vale_soft,
                    streaks: opts.streaks,
                    rock_darken: opts.rock_darken,
                    tri_slope: opts.tri_slope,
                    tri_soft: opts.tri_soft,
                    strata_strength: opts.strata_strength,
                    patchiness: opts.patchiness,
                    gravel: opts.gravel,
                    dirt: opts.dirt,
                    forest: opts.forest,
                    shore_width: opts.shore_width,
                },
            )
        })?,
    )?;
    api.set(
        "ground_state",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(g) = view.ground else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "patchiness": g.tuning.patchiness,
                    "gravel_shoulder": g.tuning.gravel_shoulder,
                    "vale_soft": g.tuning.vale_soft,
                    "dirt": g.tuning.dirt_density,
                    "forest": g.tuning.forest_density,
                    "shore_width": g.tuning.shore_width,
                    "tri_slope": g.walls_a[0],
                    "tri_soft": g.walls_a[1],
                    "strata_spacing": g.walls_a[2],
                    "strata_strength": g.walls_a[3],
                    "rock_darken": g.walls_b[0],
                    "streaks": g.walls_b[1],
                    "moss": g.walls_b[2],
                }),
            )?)
        })?,
    )?;

    // ── Leituras extra (snapshot) ───────────────────────────────────────
    api.set(
        "distance",
        lua.create_function(|lua, (a, b): (EntityArg, EntityArg)| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let entity_a = resolve(lua, a)?;
            let entity_b = resolve(lua, b)?;
            let pos_of = |entity: Entity| {
                view.entities
                    .iter()
                    .find(|info| info.id == entity)
                    .and_then(|info| info.position)
            };
            let (pa, pb) = (pos_of(entity_a), pos_of(entity_b));
            match (pa, pb) {
                (Some(pa), Some(pb)) => Ok(pa.distance(pb)),
                _ => Err(mlua::Error::runtime("entidade sem posição no snapshot")),
            }
        })?,
    )?;
    api.set(
        "camera",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(cam) = view.camera.as_ref() else {
                return Ok(Value::Nil);
            };
            let table = lua.create_table()?;
            table.raw_set("x", cam.position.x)?;
            table.raw_set("y", cam.position.y)?;
            table.raw_set("z", cam.position.z)?;
            table.raw_set("distance", cam.distance)?;
            table.raw_set("pitch", cam.pitch)?;
            table.raw_set("yaw", cam.yaw)?;
            if let Some(target) = &cam.target {
                table.raw_set("target", target.as_str())?;
            }
            Ok(Value::Table(table))
        })?,
    )?;
    api.set(
        "clock",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(clock) = view.clock.as_ref() else {
                return Ok(Value::Nil);
            };
            let table = lua.create_table()?;
            table.raw_set("minute", clock.minute)?;
            table.raw_set("dawn", clock.dawn)?;
            table.raw_set("dusk", clock.dusk)?;
            table.raw_set("minutes_per_real_second", clock.minutes_per_real_second)?;
            Ok(Value::Table(table))
        })?,
    )?;
    api.set(
        "vault",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(vault) = view.vault.as_ref() else {
                return Ok(Value::Nil);
            };
            let table = lua.create_table()?;
            table.raw_set("gold", vault.gold)?;
            table.raw_set("wood", vault.wood)?;
            table.raw_set("stone", vault.stone)?;
            let items = lua.create_table()?;
            for (id, n) in &vault.items {
                items.raw_set(id.as_str(), *n)?;
            }
            table.raw_set("items", items)?;
            Ok(Value::Table(table))
        })?,
    )?;
    api.set(
        "quests",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let table = lua.create_table()?;
            for (id, state) in &view.quests {
                table.raw_set(id.as_str(), state.as_str())?;
            }
            Ok(table)
        })?,
    )?;
    api.set(
        "prof",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            json_to_lua(lua, &view.prof)
        })?,
    )?;
    api.set(
        "fps",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            Ok(view.prof.get("fps").and_then(Json::as_f64))
        })?,
    )?;

    let spawn_marker = |lua: &Lua, sphere: bool| {
        lua.create_function(
            move |lua, (x, y, z, size, color): (f32, f32, f32, f32, Option<String>)| {
                let color = match color {
                    Some(hex) => crate::xml::values::parse_color(&hex, "viber.debug.spawn")
                        .map_err(mlua::Error::runtime)?,
                    None => [1.0, 0.55, 0.1],
                };
                let name = if sphere { "debug:sphere" } else { "debug:box" };
                push(
                    lua,
                    DebugOp::SpawnMarker {
                        sphere,
                        pos: Vec3::new(x, y, z),
                        size: Vec3::splat(size),
                        color,
                        name: name.to_string(),
                    },
                )
            },
        )
    };
    api.set("spawn_sphere", spawn_marker(lua, true)?)?;
    api.set("spawn_box", spawn_marker(lua, false)?)?;

    viber.set("debug", api)?;
    Ok(())
}

// ---------------------------------------------------------------- apply

/// `Some(nome)` quando a op carrega um f32 não finito (NaN/inf) — o chamador
/// rejeita-a com warning em vez de propagar o NaN para o mundo.
fn op_non_finite(op: &DebugOp) -> Option<&'static str> {
    let finite3 = |v: Vec3| v.x.is_finite() && v.y.is_finite() && v.z.is_finite();
    let finite2 = |v: Vec2| v.x.is_finite() && v.y.is_finite();
    match op {
        DebugOp::SetPos(_, p) | DebugOp::Teleport(_, p) | DebugOp::TeleportSnap(_, p)
            if !finite3(*p) =>
        {
            Some("set_pos/teleport/move_to")
        }
        DebugOp::MoveBySnap(_, d) if !finite2(*d) => Some("move_player"),
        DebugOp::Face(_, t) if !finite3(*t) => Some("face"),
        DebugOp::Heal(a) | DebugOp::Damage(a) if !a.is_finite() => Some("heal/damage"),
        DebugOp::SetSpeed(_, s) if !s.is_finite() => Some("set_speed"),
        DebugOp::SetTimeScale(s) if !s.is_finite() => Some("set_time_scale"),
        DebugOp::SetHp(h) if !h.is_finite() => Some("set_hp"),
        DebugOp::Rotate(_, deg) if !deg.is_finite() => Some("rotate"),
        DebugOp::SetScale(_, s) if !s.is_finite() => Some("set_scale"),
        DebugOp::SetCamera {
            distance, pitch, ..
        } if distance.is_some_and(|d| !d.is_finite()) || pitch.is_some_and(|p| !p.is_finite()) => {
            Some("set_camera")
        }
        DebugOp::SetClock(minute) if !minute.is_finite() => Some("set_clock"),
        DebugOp::SetWindow { width, height } if !width.is_finite() || !height.is_finite() => {
            Some("set_window")
        }
        DebugOp::Sun {
            yaw,
            pitch,
            illuminance,
            ..
        } if yaw.is_some_and(|v| !v.is_finite())
            || pitch.is_some_and(|v| !v.is_finite())
            || illuminance.is_some_and(|v| !v.is_finite()) =>
        {
            Some("sun")
        }
        DebugOp::Ground {
            moss,
            vale_soft,
            streaks,
            rock_darken,
            tri_slope,
            tri_soft,
            strata_strength,
            patchiness,
            gravel,
            dirt,
            forest,
            shore_width,
        } if [moss, vale_soft, streaks, rock_darken, tri_slope, tri_soft, strata_strength, patchiness, gravel, dirt, forest, shore_width]
            .into_iter()
            .any(|v| v.is_some_and(|x| !x.is_finite())) =>
        {
            Some("ground")
        }
        DebugOp::SpawnMarker { pos, size, .. } if !finite3(*pos) || !finite3(*size) => {
            Some("spawn_marker")
        }
        _ => None,
    }
}

/// Aplica as ops enfileiradas ao mundo (ainda no handler — os sistemas de
/// gameplay correm depois, no mesmo frame). Falhas individuais viram warnings
/// na resposta; devolve o nº de ops aplicadas.
fn apply_ops(world: &mut World, ops: Vec<DebugOp>, warnings: &mut Vec<String>) -> usize {
    let mut applied = 0;
    for op in ops {
        if apply_one(world, op, warnings) {
            applied += 1;
        }
    }
    applied
}

fn apply_one(world: &mut World, op: DebugOp, warnings: &mut Vec<String>) -> bool {
    // NaN/inf envenenavam estado PERMANENTEMENTE (f32::clamp com NaN → NaN no
    // `minute_of_day`, translation/scale NaN no transform) — rejeita a op
    // antes de tocar no mundo, com warning na resposta da REPL.
    if let Some(name) = op_non_finite(&op) {
        warnings.push(format!(
            "{name}: valor numérico não finito (NaN/inf) — operação ignorada"
        ));
        return false;
    }
    match op {
        DebugOp::SetPos(entity, pos) => set_translation(world, entity, pos, warnings),
        DebugOp::Teleport(entity, pos) => set_translation(world, entity, pos, warnings),
        DebugOp::TeleportSnap(entity, mut pos) => {
            if let Some(terrain) = world.get_resource::<crate::terrain::runtime::TerrainRuntime>() {
                pos.y = terrain.sample(pos.x, pos.z);
            }
            set_translation(world, entity, pos, warnings)
        }
        DebugOp::MoveBySnap(entity, delta) => {
            let current = with_entity(world, entity, warnings, |e| {
                e.get::<Transform>().map(|t| t.translation)
            })
            .flatten();
            let Some(current) = current else {
                warnings.push(format!("{entity}: move sem Transform"));
                return false;
            };
            let mut target = Vec3::new(current.x + delta.x, current.y, current.z + delta.y);
            if let Some(terrain) = world.get_resource::<crate::terrain::runtime::TerrainRuntime>() {
                // Piso SOB a entidade (Y conhecido): o perfil de encosta do QA
                // segue o chão onde se anda, não o topo do mundo.
                target.y = terrain
                    .surface_below(target.x, target.z, current.y + crate::player::GROUND_PROBE)
                    .unwrap_or_else(|| terrain.sample(target.x, target.z));
            }
            set_translation(world, entity, target, warnings)
        }
        DebugOp::Face(entity, target) => with_entity(world, entity, warnings, |e| {
            let Some(mut transform) = e.get_mut::<Transform>() else {
                return false;
            };
            let dir = Vec3::new(
                target.x - transform.translation.x,
                0.0,
                target.z - transform.translation.z,
            );
            if dir.length_squared() > 1e-6 {
                transform.rotation = crate::player::facing_rotation(dir.normalize());
            }
            true
        })
        .unwrap_or(false),
        DebugOp::Hide(entity) => set_visibility(world, entity, Some(Visibility::Hidden), warnings),
        DebugOp::Show(entity) => set_visibility(world, entity, Some(Visibility::Visible), warnings),
        DebugOp::ToggleVis(entity) => set_visibility(world, entity, None, warnings),
        DebugOp::Disable(entity) => with_entity(world, entity, warnings, |e| {
            e.insert(Disabled);
            true
        })
        .unwrap_or(false),
        DebugOp::Enable(entity) => with_entity(world, entity, warnings, |e| {
            e.remove::<Disabled>();
            true
        })
        .unwrap_or(false),
        DebugOp::Despawn(entity) => {
            if world.despawn(entity) {
                true
            } else {
                warnings.push(format!("{entity}: despawn falhou"));
                false
            }
        }
        DebugOp::Toast(msg) => {
            match world.get_resource_mut::<Messages<crate::luau::ScriptToast>>() {
                Some(mut messages) => {
                    info!(target: "viber::luau", "[toast/debug] {msg}");
                    messages.write(crate::luau::ScriptToast(msg));
                }
                None => {
                    warnings.push("plugin de toasts indisponível — mensagem só no log".into());
                    info!(target: "viber::luau", "[toast/debug] {msg}");
                }
            }
            true
        }
        DebugOp::Heal(amount) => change_player_health(world, amount, warnings),
        DebugOp::Damage(amount) => change_player_health(world, -amount, warnings),
        DebugOp::AddXp(gain) => match player_entity_mut(world) {
            Some(mut entity) => match entity.get_mut::<Xp>() {
                Some(mut xp) => {
                    crate::vitals::gain_xp(&mut xp, gain);
                    true
                }
                None => {
                    warnings.push("player sem Xp".into());
                    false
                }
            },
            None => {
                warnings.push("sem player — xp ignorado".into());
                false
            }
        },
        DebugOp::Give(kind, amount) => match world.get_resource_mut::<crate::economy::Vault>() {
            Some(mut vault) => {
                if !vault.add_resource(&kind, amount) {
                    vault.item_add(&kind, amount);
                }
                true
            }
            None => {
                warnings.push(format!("vault indisponível — '{kind}' perdido"));
                false
            }
        },
        DebugOp::SetSpeed(entity, speed) => {
            let applied = with_entity(world, entity, warnings, |e| {
                e.get_mut::<Player>()
                    .map(|mut player| {
                        player.speed = speed;
                    })
                    .is_some()
            })
            .unwrap_or(false);
            if applied {
                true
            } else {
                warnings.push(format!("{entity}: set_speed — sem Player"));
                false
            }
        }
        DebugOp::SetTimeScale(scale) => {
            let scale = scale.max(0.0);
            // Base para o hit-stop compor (senão o hit_stop_system com timer
            // inativo deixava o slow-mo morto no frame seguinte).
            if let Some(mut base) = world.get_resource_mut::<crate::combat::BaseTimeScale>() {
                base.0 = scale;
            }
            world
                .resource_mut::<Time<Virtual>>()
                .set_relative_speed(scale);
            true
        }
        DebugOp::SpawnMarker {
            sphere,
            pos,
            size,
            color,
            name,
        } => spawn_marker(world, sphere, pos, size, color, name, warnings),
        DebugOp::ClearMarkers => {
            let markers: Vec<Entity> = world
                .iter_entities()
                .filter(|e| {
                    e.get::<Name>().is_some_and(|n| {
                        let n = n.as_str();
                        n.starts_with("debug:sphere:") || n.starts_with("debug:box:")
                    })
                })
                .map(|e| e.id())
                .collect();
            for marker in markers {
                world.despawn(marker);
            }
            true
        }
        DebugOp::Kill(entity) => {
            let applied = with_entity(world, entity, warnings, |e| {
                e.get_mut::<Health>()
                    .map(|mut health| health.current = 0.0)
                    .is_some()
            })
            .unwrap_or(false);
            if applied {
                true
            } else {
                warnings.push(format!("{entity}: kill — sem Health"));
                false
            }
        }
        DebugOp::SetHp(hp) => match player_entity_mut(world) {
            Some(mut entity) => match entity.get_mut::<Health>() {
                Some(mut health) => {
                    health.current = hp.clamp(0.0, health.max);
                    true
                }
                None => {
                    warnings.push("player sem Health — set_hp ignorado".into());
                    false
                }
            },
            None => {
                warnings.push("sem player — set_hp ignorado".into());
                false
            }
        },
        DebugOp::Rotate(entity, deg) => with_entity(world, entity, warnings, |e| {
            let Some(mut transform) = e.get_mut::<Transform>() else {
                return false;
            };
            let (yaw, _, _) = transform.rotation.to_euler(EulerRot::YXZ);
            transform.rotation = Quat::from_rotation_y(yaw + deg.to_radians());
            true
        })
        .unwrap_or(false),
        DebugOp::SetScale(entity, s) => with_entity(world, entity, warnings, |e| {
            let Some(mut transform) = e.get_mut::<Transform>() else {
                return false;
            };
            transform.scale = Vec3::splat(s.max(0.001));
            true
        })
        .unwrap_or(false),
        DebugOp::SetCamera {
            distance,
            pitch,
            target,
        } => {
            let cam_entity = world
                .iter_entities()
                .find(|e| e.get::<OrbitCamera>().is_some())
                .map(|e| e.id());
            let Some(cam_entity) = cam_entity else {
                warnings.push("sem câmara OrbitCamera no mundo".into());
                return false;
            };
            with_entity(world, cam_entity, warnings, |e| {
                let Some(mut cam) = e.get_mut::<OrbitCamera>() else {
                    return false;
                };
                if let Some(distance) = distance {
                    cam.distance = distance.max(0.5);
                }
                if let Some(pitch) = pitch {
                    cam.pitch_deg = Some(pitch);
                    cam.pitch_state_deg = pitch;
                }
                if let Some(target) = target {
                    cam.target = Some(target);
                }
                true
            })
            .unwrap_or(false)
        }
        DebugOp::SetClock(minute) => {
            match world.get_resource_mut::<crate::worldsys::DayCycleState>() {
                Some(mut clock) => {
                    clock.minute_of_day = minute.clamp(0.0, 1440.0).rem_euclid(1440.0);
                    true
                }
                None => {
                    warnings.push("sem DayCycleState — set_clock ignorado".into());
                    false
                }
            }
        }
        DebugOp::Sun {
            yaw,
            pitch,
            illuminance,
            shadows,
        } => {
            // Direção ATUAL do sol (para defaults quando só um ângulo vem):
            // a posição do sol no céu = -direção de viagem.
            let light = world
                .iter_entities()
                .find(|e| e.get::<DirectionalLight>().is_some())
                .map(|e| e.id());
            let (mut sun_yaw, mut sun_pitch) = (0.0_f32, 45.0_f32);
            if let Some(id) = light
                && let Some(t) = world.get::<Transform>(id)
            {
                let travel = t.rotation * Vec3::NEG_Z;
                let pos = -travel;
                sun_pitch = pos.y.asin().to_degrees();
                sun_yaw = pos.z.atan2(pos.x).to_degrees();
            }
            if let Some(v) = yaw {
                sun_yaw = v;
            }
            if let Some(v) = pitch {
                sun_pitch = v;
            }
            let (yaw_r, pitch_r) = (sun_yaw.to_radians(), sun_pitch.clamp(-89.0, 89.0).to_radians());
            // Posição do sol no céu (azimute a partir de +X, altura = pitch)
            // → direção de VIAGEM = -posição.
            let pos = Vec3::new(
                yaw_r.cos() * pitch_r.cos(),
                pitch_r.sin(),
                yaw_r.sin() * pitch_r.cos(),
            );
            let travel = -pos;
            // O TERRENO tem sol próprio (uniform `sun_dir` dos chunks) — sem
            // publicar aqui, rodar a luz da cena não mudava um píxel de chão.
            if let Some(mut assets) = world.get_resource_mut::<Assets<TerrainChunkMaterial>>() {
                for (_, mat) in assets.iter_mut() {
                    mat.params.sun_dir = Vec4::new(travel.x, travel.y, travel.z, 1.0);
                }
            }
            let Some(light) = light else {
                warnings
                    .push("sun: sem DirectionalLight no mundo — só o sol do terreno mudou".into());
                return true;
            };
            if yaw.is_some() || pitch.is_some() {
                if let Some(mut transform) = world.get_mut::<Transform>(light) {
                    transform.rotation =
                        Quat::from_rotation_arc(Vec3::NEG_Z, travel.normalize_or_zero());
                }
            }
            if let Some(mut light) = world.get_mut::<DirectionalLight>(light) {
                if let Some(v) = illuminance {
                    light.illuminance = v.max(0.0);
                }
                if let Some(v) = shadows {
                    light.shadow_maps_enabled = v;
                }
            }
            true
        }
        DebugOp::Ground {
            moss,
            vale_soft,
            streaks,
            rock_darken,
            tri_slope,
            tri_soft,
            strata_strength,
            patchiness,
            gravel,
            dirt,
            forest,
            shore_width,
        } => {
            // 1) Pele das paredes: TODOS os materiais de chunk partilham os
            //    valores (a mesa de estilo é global).
            if moss.is_some()
                || streaks.is_some()
                || rock_darken.is_some()
                || tri_slope.is_some()
                || tri_soft.is_some()
                || strata_strength.is_some()
            {
                if let Some(mut assets) = world.get_resource_mut::<Assets<TerrainChunkMaterial>>() {
                    for (_, mat) in assets.iter_mut() {
                        if let Some(v) = tri_slope {
                            mat.params.walls_a.x = v.clamp(0.0, 1.0);
                        }
                        if let Some(v) = tri_soft {
                            mat.params.walls_a.y = v.clamp(0.01, 1.0);
                        }
                        if let Some(v) = strata_strength {
                            mat.params.walls_a.w = v.clamp(0.0, 1.0);
                        }
                        if let Some(v) = rock_darken {
                            mat.params.walls_b.x = v.clamp(0.0, 2.0);
                        }
                        if let Some(v) = streaks {
                            mat.params.walls_b.y = v.clamp(0.0, 1.0);
                        }
                        if let Some(v) = moss {
                            mat.params.walls_b.z = v.clamp(0.0, 1.0);
                        }
                    }
                }
            }
            // 2) Splat: acumula o tuning (cada chamada passa só o que muda)
            //    e re-cozinha os planos sobre a grid retida.
            let mut tuning = world
                .get_resource::<GroundState>()
                .map(|s| s.tuning)
                .unwrap_or_default();
            if let Some(v) = patchiness {
                tuning.patchiness = v.clamp(0.0, 3.0);
            }
            if let Some(v) = gravel {
                tuning.gravel_shoulder = v.clamp(0.005, 0.3);
            }
            if let Some(v) = dirt {
                tuning.dirt_density = v.clamp(0.0, 2.0);
            }
            if let Some(v) = forest {
                tuning.forest_density = v.clamp(0.0, 2.0);
            }
            if let Some(v) = shore_width {
                tuning.shore_width = Some(v.clamp(0.5, 40.0));
            }
            if let Some(v) = vale_soft {
                tuning.vale_soft = v.clamp(0.0, 1.0);
            }
            let rebaked = crate::terrain::runtime::rebake_chunk_splats(world, tuning);
            world.insert_resource(GroundState { tuning });
            if rebaked == 0
                && (patchiness.is_some()
                    || gravel.is_some()
                    || dirt.is_some()
                    || forest.is_some()
                    || shore_width.is_some()
                    || vale_soft.is_some())
            {
                warnings.push(
                    "ground: splats não re-cozidos (sem terreno com `layers`?) — só as paredes \
                     (se pedidas) se aplicaram"
                        .into(),
                );
            }
            true
        }
        DebugOp::SetWindow { width, height } => {
            // Redimensiona a janela primária. O bevy_winit sincroniza o winit
            // com o componente; os estilos reagem via `WindowResized`.
            let mut query =
                world.query_filtered::<&mut Window, With<bevy::window::PrimaryWindow>>();
            let Some(mut window) = query.single_mut(world).ok() else {
                warnings.push("sem janela primária — set_window ignorado".into());
                return false;
            };
            window
                .resolution
                .set_physical_resolution(width.max(64.0) as u32, height.max(64.0) as u32);
            true
        }
    }
}

/// Escrita absoluta de translation, com warning se a entidade não tiver
/// Transform (ou não existir — nesse caso `with_entity` já avisa).
fn set_translation(
    world: &mut World,
    entity: Entity,
    pos: Vec3,
    warnings: &mut Vec<String>,
) -> bool {
    let applied = with_entity(world, entity, warnings, |e| {
        let moved = match e.get_mut::<Transform>() {
            Some(mut transform) => {
                transform.translation = pos;
                true
            }
            None => false,
        };
        // Chegada limpa, tal como o fast-travel (`travel.rs`): sem isto o
        // `vel_y` acumulado ATRAVESSA o teleport. Um debug `teleport` durante
        // uma queda reentrava com dezenas de m/s, a caixa de motion do frame
        // ultrapassava o shape-cast do controller e o herói atravessava o
        // terreno — queda infinita reproduzida a `y = -123675`.
        if moved && let Some(mut player) = e.get_mut::<crate::player::Player>() {
            player.vel_x = 0.0;
            player.vel_y = 0.0;
            player.vel_z = 0.0;
        }
        moved
    })
    .unwrap_or(false);
    if applied {
        true
    } else {
        warnings.push(format!("{entity}: sem Transform"));
        false
    }
}

/// Acesso direto a uma entidade (`Disabled` inclusive); ausência vira warning.
fn with_entity<T>(
    world: &mut World,
    entity: Entity,
    warnings: &mut Vec<String>,
    f: impl FnOnce(&mut bevy::ecs::world::EntityWorldMut) -> T,
) -> Option<T> {
    match world.get_entity_mut(entity) {
        Ok(mut e) => Some(f(&mut e)),
        Err(_) => {
            warnings.push(format!("{entity}: entidade não existe (despawned?)"));
            None
        }
    }
}

/// `None` = toggle (Hidden ↔ Visible).
fn set_visibility(
    world: &mut World,
    entity: Entity,
    target: Option<Visibility>,
    warnings: &mut Vec<String>,
) -> bool {
    let found = with_entity(world, entity, warnings, |e| {
        e.get_mut::<Visibility>()
            .map(|mut visibility| {
                *visibility = match target {
                    Some(v) => v,
                    None => {
                        if *visibility == Visibility::Hidden {
                            Visibility::Visible
                        } else {
                            Visibility::Hidden
                        }
                    }
                };
                true
            })
            .unwrap_or(false)
    })
    .unwrap_or(false);
    if found {
        true
    } else {
        warnings.push(format!("{entity}: sem Visibility"));
        false
    }
}

fn player_entity_mut(world: &mut World) -> Option<bevy::ecs::world::EntityWorldMut<'_>> {
    let player = find_player(world).map(|p| p.entity)?;
    world.get_entity_mut(player).ok()
}

fn change_player_health(world: &mut World, amount: f32, warnings: &mut Vec<String>) -> bool {
    match player_entity_mut(world) {
        Some(mut entity) => match entity.get_mut::<Health>() {
            Some(mut health) => {
                if amount >= 0.0 {
                    health.current = (health.current + amount).min(health.max);
                } else {
                    health.current = (health.current + amount).max(0.0);
                }
                true
            }
            None => {
                warnings.push("player sem Health".into());
                false
            }
        },
        None => {
            warnings.push("sem player — heal/damage ignorado".into());
            false
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn spawn_marker(
    world: &mut World,
    sphere: bool,
    pos: Vec3,
    size: Vec3,
    color: [f32; 3],
    name: String,
    warnings: &mut Vec<String>,
) -> bool {
    use bevy::asset::Assets;
    let Some(mut meshes) = world.get_resource_mut::<Assets<Mesh>>() else {
        warnings.push("Assets<Mesh> indisponível — marker não spawna".into());
        return false;
    };
    let mesh = if sphere {
        Mesh::from(Sphere::new(size.x.max(0.01)))
    } else {
        Mesh::from(Cuboid::new(
            size.x.max(0.01),
            size.y.max(0.01),
            size.z.max(0.01),
        ))
    };
    let mesh = meshes.add(mesh);
    let Some(mut materials) = world.get_resource_mut::<Assets<StandardMaterial>>() else {
        warnings.push("Assets<StandardMaterial> indisponível".into());
        return false;
    };
    let material = materials.add(StandardMaterial {
        base_color: Color::srgb(color[0], color[1], color[2]),
        unlit: true,
        ..Default::default()
    });
    // Nome único por sessão para `find` no chunk seguinte.
    static NEXT_ID: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    let n = NEXT_ID.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    world.spawn((
        Name::new(format!("{name}:{n}")),
        Mesh3d(mesh),
        MeshMaterial3d(material),
        Transform::from_translation(pos),
        Visibility::default(),
    ));
    true
}

// ---------------------------------------------------------------- JSON

/// Tabela Lua com o `TransformInfo`.
fn transform_table(lua: &Lua, t: &TransformInfo) -> mlua::Result<Value> {
    let table = lua.create_table()?;
    table.raw_set("x", t.translation[0])?;
    table.raw_set("y", t.translation[1])?;
    table.raw_set("z", t.translation[2])?;
    table.raw_set("pitch", t.euler[0])?;
    table.raw_set("yaw", t.euler[1])?;
    table.raw_set("roll", t.euler[2])?;
    table.raw_set("sx", t.scale[0])?;
    table.raw_set("sy", t.scale[1])?;
    table.raw_set("sz", t.scale[2])?;
    if let Some(g) = t.global {
        table.raw_set("gx", g[0])?;
        table.raw_set("gy", g[1])?;
        table.raw_set("gz", g[2])?;
    }
    Ok(Value::Table(table))
}

/// Tabela Lua com o `MeshSummary`.
fn mesh_table(lua: &Lua, mesh: &MeshSummary) -> mlua::Result<Value> {
    let table = lua.create_table()?;
    table.raw_set("topology", mesh.topology.as_str())?;
    table.raw_set("vertices", mesh.vertices)?;
    if let Some(indices) = mesh.indices {
        table.raw_set("indices", indices)?;
    }
    table.raw_set("has_normals", mesh.has_normals)?;
    table.raw_set("has_uvs", mesh.has_uvs)?;
    table.raw_set("uv_count", mesh.uv_count)?;
    if let Some(min) = mesh.uv_min {
        table.raw_set("uv_min", lua.create_sequence_from([min[0], min[1]])?)?;
    }
    if let Some(max) = mesh.uv_max {
        table.raw_set("uv_max", lua.create_sequence_from([max[0], max[1]])?)?;
    }
    Ok(Value::Table(table))
}

/// Tabela Lua com o `MaterialSummary`.
fn material_table(lua: &Lua, material: &MaterialSummary) -> mlua::Result<Value> {
    let table = lua.create_table()?;
    let color = lua.create_sequence_from([
        material.base_color[0],
        material.base_color[1],
        material.base_color[2],
        material.base_color[3],
    ])?;
    table.raw_set("base_color", color)?;
    table.raw_set("metallic", material.metallic)?;
    table.raw_set("roughness", material.roughness)?;
    table.raw_set("unlit", material.unlit)?;
    if let Some(dims) = material.base_color_texture {
        table.raw_set(
            "base_color_texture",
            lua.create_sequence_from([dims[0], dims[1]])?,
        )?;
    }
    if let Some(dims) = material.normal_map {
        table.raw_set("normal_map", lua.create_sequence_from([dims[0], dims[1]])?)?;
    }
    Ok(Value::Table(table))
}

/// Tabela Lua com o `ColliderSummary`.
fn collider_table(lua: &Lua, collider: &ColliderSummary) -> mlua::Result<Value> {
    let table = lua.create_table()?;
    table.raw_set("shape", collider.shape.as_str())?;
    if let Some(he) = collider.half_extents {
        table.raw_set("hx", he[0])?;
        table.raw_set("hy", he[1])?;
        table.raw_set("hz", he[2])?;
    }
    if let Some(radius) = collider.radius {
        table.raw_set("radius", radius)?;
    }
    if let Some(vertices) = collider.vertices {
        table.raw_set("vertices", vertices)?;
    }
    if let Some(shapes) = collider.shapes {
        table.raw_set("shapes", shapes)?;
    }
    Ok(Value::Table(table))
}

/// Tabela completa `viber.debug.info(id)` — tudo o que o snapshot tem.
fn info_table(lua: &Lua, info: &EntityInfo) -> mlua::Result<Value> {
    let table = lua.create_table()?;
    table.raw_set("id", info.id.to_bits() as i64)?;
    if let Some(name) = &info.name {
        table.raw_set("name", name.as_str())?;
    }
    if let Some(pos) = info.position {
        table.raw_set("x", pos.x)?;
        table.raw_set("y", pos.y)?;
        table.raw_set("z", pos.z)?;
    }
    table.raw_set("disabled", info.disabled)?;
    if let Some(hidden) = info.hidden {
        table.raw_set("hidden", hidden)?;
    }
    if let Some(transform) = &info.transform {
        table.raw_set("transform", transform_table(lua, transform)?)?;
    }
    if let Some(parent) = info.parent {
        table.raw_set("parent", parent.to_bits() as i64)?;
    }
    if !info.children.is_empty() {
        let children: Vec<i64> = info
            .children
            .iter()
            .map(|child| child.to_bits() as i64)
            .collect();
        table.raw_set("children", lua.create_sequence_from(children)?)?;
    }
    if let Some(collider) = &info.collider {
        table.raw_set("collider", collider_table(lua, collider)?)?;
    }
    if let Some(rigidbody) = &info.rigidbody {
        table.raw_set("rigidbody", rigidbody.as_str())?;
    }
    if let Some(mesh) = &info.mesh {
        table.raw_set("mesh", mesh_table(lua, mesh)?)?;
    }
    if let Some(material) = &info.material {
        table.raw_set("material", material_table(lua, material)?)?;
    }
    table.raw_set(
        "components",
        lua.create_sequence_from(info.components.iter().map(String::as_str))?,
    )?;
    Ok(Value::Table(table))
}

/// JSON → Lua (para devolver o snapshot do profiler como tabela).
/// Partilhado com a API `viber.profiler()` (`src/profiler/script.rs`).
pub(crate) fn json_to_lua(lua: &Lua, value: &Json) -> mlua::Result<Value> {
    Ok(match value {
        Json::Null => Value::Nil,
        Json::Bool(b) => Value::Boolean(*b),
        Json::Number(n) => n.as_f64().map(Value::Number).unwrap_or(Value::Nil),
        Json::String(s) => Value::String(lua.create_string(s)?),
        Json::Array(items) => {
            let table = lua.create_table()?;
            for (i, item) in items.iter().enumerate() {
                table.raw_set(i + 1, json_to_lua(lua, item)?)?;
            }
            Value::Table(table)
        }
        Json::Object(map) => {
            let table = lua.create_table()?;
            for (key, item) in map {
                table.raw_set(key.as_str(), json_to_lua(lua, item)?)?;
            }
            Value::Table(table)
        }
    })
}

/// `mlua::Value` → JSON (conversor próprio: o crate não liga a feature
/// `serde` do mlua). Tabelas com chaves 1..n viram arrays; profundidade
/// máxima 8 corta ciclos.
fn value_to_json(value: &Value, depth: usize) -> Json {
    if depth > 8 {
        return json!("…");
    }
    match value {
        Value::Nil => Json::Null,
        Value::Boolean(b) => json!(b),
        Value::Integer(i) => json!(i),
        Value::Number(n) => json!(n),
        Value::Vector(v) => json!([v.x(), v.y(), v.z()]),
        Value::String(s) => match s.to_str() {
            Ok(text) => json!(text.to_owned()),
            Err(_) => json!("<binário>"),
        },
        Value::Table(table) => {
            let mut pairs: Vec<(Json, Json)> = Vec::new();
            for pair in table.clone().pairs::<Value, Value>() {
                let Ok((key, value)) = pair else { continue };
                if matches!(key, Value::Nil) {
                    continue;
                }
                pairs.push((
                    value_to_json(&key, depth + 1),
                    value_to_json(&value, depth + 1),
                ));
            }
            let is_array = !pairs.is_empty()
                && pairs
                    .iter()
                    .enumerate()
                    .all(|(i, (key, _))| key.as_i64() == Some(i as i64 + 1));
            if is_array {
                Json::Array(pairs.into_iter().map(|(_, value)| value).collect())
            } else {
                let mut object = serde_json::Map::new();
                for (key, value) in pairs {
                    let key = match key {
                        Json::String(s) => s,
                        other => serde_json::to_string(&other).unwrap_or_else(|_| "?".into()),
                    };
                    object.insert(key, value);
                }
                Json::Object(object)
            }
        }
        Value::Function(_) => json!("<function>"),
        other => json!(format!("{other:?}")),
    }
}
