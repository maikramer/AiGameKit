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
use bevy_landmass::AgentState;
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
    /// Path do script de jogo (`enemies/wolf.lua`), quando `scripted`.
    pub script: Option<String>,
    /// `(atual, máximo)` do `Health` — de QUALQUER entidade (o snapshot era
    /// hero-cêntrico; criaturas também têm vitals).
    pub health: Option<(f32, f32)>,
    /// Estado de IA da criatura (`EnemyCreature` + `AiLocomotion` +
    /// perfil de nav), quando existe.
    pub ai: Option<AiInfo>,
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
    /// Há collider de terreno SOB os pés do herói
    /// (`physics::TerrainCollisionStatus`) — quando é `false` o
    /// `player_movement` usa o chão analítico. É o número que explica um
    /// herói a atravessar o mundo depois de um teleporte.
    pub terrain_collider_ready: bool,
    /// Estado vertical do herói: no chão? velocidade vertical?
    pub player_grounded: bool,
    pub player_vel_y: f32,
    /// O herói está numa bolsa de interior (`worldsys::InteriorLighting`)?
    pub interior_active: bool,
    /// Brilho actual do `GlobalAmbientLight` (lux).
    pub ambient_brightness: f32,
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

/// IA de uma criatura no snapshot (`viber.debug.ai`/`info`) — FSM da engine
/// (`EnemyCreature`), pedidos de locomoção (`AiLocomotion`) e perfil de nav.
pub struct AiInfo {
    /// "wander" | "chase" — só criaturas da FSM da engine.
    pub state: Option<String>,
    pub speed: f32,
    pub aggro_radius: f32,
    pub attack_radius: f32,
    pub home: Option<[f32; 2]>,
    /// Velocidade pedida este frame (m/s, XZ) — o que o script/FSM pediu.
    pub desired: [f32; 2],
    /// Velocidade integrada atual (m/s, XZ).
    pub velocity: [f32; 2],
    /// Destino declarado pelo produtor (`drive_to`), quando existe.
    pub goal: Option<[f32; 2]>,
    /// "civil" | "wild" — o preço fora-de-estrada que a navmesh cobra.
    pub nav_profile: Option<String>,
}

/// Estado da pilha de navegação no snapshot (`viber.debug.nav`).
pub struct NavInfo {
    pub enabled: bool,
    pub agent_radius: f32,
    pub agent_height: f32,
    pub tile_size: f32,
    pub offroad_cost: f32,
    /// Centro do tile vivo/gerando (XZ), quando há tile.
    pub tile_center: Option<[f32; 2]>,
    pub tile_generating: bool,
    pub tile_generations: u32,
    /// Obstáculos estáticos com que o tile vivo foi cozido.
    pub tile_obstacles: Option<u64>,
    /// Census de estados dos agentes landmass — a resposta a "porque é que
    /// a criatura não anda" (`fora-da-mesh`/`sem-caminho`/`alvo-fora`).
    pub census: Vec<(&'static str, usize)>,
}

/// Uma quest com definição + estado (`viber.debug.quest`).
pub struct QuestInfo {
    pub id: String,
    pub title: String,
    pub npc: String,
    pub biome: String,
    /// "not_taken" | "active" | "ready" | "done".
    pub status: String,
    pub objective_kind: String,
    pub objective_target: String,
    pub objective_count: u32,
    /// Texto "x/y" do objetivo (collect lê o vault).
    pub progress_text: String,
    /// Marcos de visita já alcançados (objetivo visit).
    pub visited: Vec<String>,
    pub rewards_gold: u32,
    pub rewards_xp: u32,
    pub rewards_items: Vec<String>,
}

/// Uma `<BiomeRegion>` no snapshot (`viber.debug.regions`/`biome_at`).
pub struct RegionInfo {
    pub id: String,
    pub display_name: String,
    pub polygon: Vec<[f32; 2]>,
    pub fog_density: f32,
    pub tint: Option<[f32; 3]>,
    pub pp_exposure: Option<f32>,
    pub pp_bloom_strength: Option<f32>,
}

/// Névoa + atmosfera vivos no snapshot (`viber.debug.atmosphere`).
pub struct AtmosphereInfo {
    /// 1 dia pleno, 0 noite plena.
    pub day: f32,
    pub night: f32,
    /// Pico na hora dourada/crepúsculo.
    pub golden: f32,
    /// Densidade corrente da névoa exponencial-quadrática da câmara.
    pub fog_density: Option<f32>,
    /// Cor da névoa (linear).
    pub fog_color: [f32; 3],
    pub exposure_scale: f32,
    pub bloom_boost: f32,
}

/// `<WorldBorder>` no snapshot (`viber.debug.border`).
pub struct BorderInfo {
    pub radius: f32,
    pub warn_seconds: f32,
    pub margin: f32,
}

/// Bolsa de interior no snapshot (`viber.debug.interior`).
pub struct InteriorInfo {
    /// O herói está lá dentro agora (`InteriorLighting.active`).
    pub active: bool,
    /// Retângulo da bolsa (min/max XZ) + grelha de salas, quando declarada.
    pub min: Option<[f32; 2]>,
    pub max: Option<[f32; 2]>,
    pub room_size: [f32; 2],
    pub room_origin: [f32; 2],
    pub camera_distance: f32,
    pub camera_pitch_deg: f32,
    pub camera_yaw_deg: f32,
}

/// Um nó da UI endereçável no snapshot (`viber.debug.ui_tree`).
pub struct UiNodeInfo {
    /// id autoral da UI declarativa OU `Name` (`hud:health`, `chip:gold`…).
    pub id: String,
    /// Rect computado (píxeis físicos) — dá o clique exato via `input.click`.
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    pub visible: bool,
    pub disabled: bool,
    /// Texto próprio + descendentes (spans concatenados).
    pub text: Option<String>,
    /// Classes CSS-like (`UiClasses`).
    pub classes: Vec<String>,
}

/// Skills/cooldowns do herói no snapshot (`viber.debug.skills`).
pub struct SkillsInfo {
    pub learned: Vec<String>,
    pub points: u32,
    pub level: Option<u32>,
    pub level_points: Option<u32>,
    pub cooldowns: Option<[f32; 3]>,
    pub bonus_damage: Option<f32>,
    pub speed_mult: Option<f32>,
    pub max_hp_bonus: Option<f32>,
    pub crit_bonus: Option<f32>,
}

/// Seeds vivos do mundo no snapshot (`viber.debug.seeds`) — a metade da
/// promessa "mesma seed, mesmo mundo" que se pode INSPECIONAR.
#[derive(Default)]
pub struct SeedInfo {
    pub terrain_seed: Option<u64>,
    pub world_size: Option<f32>,
    pub weather_seed: Option<u64>,
}

/// Waypoints/marcos no snapshot (`viber.debug.waypoints`).
#[derive(Default)]
pub struct WaypointInfo {
    /// Marcos da Nota já assinados.
    pub marked: Vec<String>,
    /// Waypoint atual (último assinado).
    pub label: Option<String>,
    pub position: Option<[f32; 3]>,
    /// Catálogo estático dos 12 marcos.
    pub landmarks: Vec<(&'static str, &'static str)>,
}

/// Estado do save no snapshot (`viber.debug.save_info`).
pub struct SaveInfo {
    pub path: String,
    pub exists: bool,
    pub bytes: Option<u64>,
    /// Unix mtime (s) do ficheiro, quando existe.
    pub mtime: Option<f64>,
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
    /// Pilha de navegação (`viber.debug.nav`).
    pub nav: Option<NavInfo>,
    /// Quests com definição + estado (`viber.debug.quest`/`quest_defs`).
    pub quests_deep: Vec<QuestInfo>,
    /// `<BiomeRegion>` do mundo (`viber.debug.regions`/`biome_at`).
    pub regions: Vec<RegionInfo>,
    /// Atmosfera/névoa vivos (`viber.debug.atmosphere`).
    pub atmosphere: Option<AtmosphereInfo>,
    /// Tempo completo, com o scheduler (`viber.debug.weather_full`).
    pub weather: Option<WeatherInfo>,
    /// `<WorldBorder>` (`viber.debug.border`).
    pub border: Option<BorderInfo>,
    /// Bolsa de interior (`viber.debug.interior`).
    pub interior: Option<InteriorInfo>,
    /// UI endereçável com rects (`viber.debug.ui_tree`).
    pub ui: Vec<UiNodeInfo>,
    /// Snapshot de áudio do profiler (`viber.debug.audio`), já em JSON.
    pub audio: Option<Json>,
    /// Seeds (`viber.debug.seeds`).
    pub seeds: SeedInfo,
    /// Skills/cooldowns (`viber.debug.skills`).
    pub skills: Option<SkillsInfo>,
    /// Waypoints/marcos (`viber.debug.waypoints`).
    pub waypoints: WaypointInfo,
    /// Save em disco (`viber.debug.save_info`).
    pub save: Option<SaveInfo>,
    /// Terreno partilhado para queries posicionais ao vivo
    /// (`viber.debug.terrain(x, z)`) — Arc clones, barato.
    pub terrain: Option<crate::terrain::runtime::TerrainReader>,
    /// Registos de estrada/água para as mesmas queries.
    pub surfaces: Option<crate::luau::SurfaceRegistries>,
    /// Hash FNV-1a do conteúdo do mundo (entidades ordenadas) — A/B de
    /// determinismo "mesma seed, mesmo mundo" (`viber.debug.world_hash`).
    pub world_hash: u64,
    /// Cauda do event log estruturado (`viber.debug.events`).
    pub events: Json,
}

/// `viber.debug.weather_full` — `WeatherState` + o scheduler do ciclo.
pub struct WeatherInfo {
    pub wind: [f32; 2],
    pub wind_strength: f32,
    pub clouds: f32,
    pub rain: f32,
    pub cycle: bool,
    pub scheduler: Option<SchedulerInfo>,
}

/// O scheduler do `<Weather cycle>` — o que o ciclo VAI fazer.
pub struct SchedulerInfo {
    pub seed: u64,
    pub index: u64,
    pub period: f32,
    pub timer: f32,
    pub target: f32,
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
    /// Câmara orbital: distância, pitch, yaw (graus) e/ou alvo (nome).
    SetCamera {
        distance: Option<f32>,
        pitch: Option<f32>,
        yaw: Option<f32>,
        target: Option<String>,
    },
    /// Minuto do dia (0–1440, wrap).
    SetClock(f32),
    /// Look da chuva AO VIVO (`viber.debug.rain_look{...}`): fade junto da
    /// câmara, tecto de alpha, largura do streak e rate. A afinação da chuva
    /// é visual — sem isto cada tentativa custava um rebuild + reboot.
    RainLook {
        near_fade: Option<f32>,
        alpha: Option<f32>,
        width: Option<f32>,
        rate: Option<f32>,
    },
    /// Tempo AO VIVO (`viber.debug.set_weather{rain=…}`): fixa a intensidade
    /// contínua do `<Weather>` e PÁRA o ciclo — sem isso o scheduler voltava
    /// a rolar o alvo a meio do A/B e os dois braços não eram comparáveis.
    SetWeather {
        rain: Option<f32>,
        clouds: Option<f32>,
        wind: Option<f32>,
    },
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
    /// HP absoluto de QUALQUER entidade (clamp a [0, max]) — `set_hp(n)`
    /// continua a ser o atalho do player.
    SetEntityHp(Entity, f32),
    /// HP máximo de qualquer entidade (mín. 1; atual clampado).
    SetMaxHp(Entity, f32),
    /// Força o estado de uma quest: "active" | "ready" | "done" | "not_taken".
    QuestForce(String, String),
    /// Fixa o progresso do objetivo (kill: contador; visit: primeiros N
    /// marcos; collect é vault-driven → warning).
    QuestProgress(String, u32),
    /// Valor ABSOLUTO de um recurso (gold/wood/stone) ou item do vault.
    VaultSet(String, u32),
    /// Tira N do vault (recurso ou item) — `false` vira warning.
    Take(String, u32),
    /// Aprende uma passiva (respeita pré-requisitos/pontos; aplica o delta
    /// ao herói, igual à compra na UI).
    SkillLearn(String),
    /// Pontos disponíveis (absoluto).
    SkillPoints(u32),
    /// Esquece TUDO (devolve os pontos gastos) e reverte os bónus do herói.
    SkillReset,
    /// Estado da FSM da criatura: "wander" | "chase" (a FSM reavalia por
    /// distância a cada frame — o aggro_radius é o lever que persiste).
    AiState(Entity, String),
    /// Raio de aggro da criatura (m).
    AiAggro(Entity, f32),
    /// Todas as criaturas da FSM → Wander.
    AiCalmAll,
    /// Navmesh ao vivo: liga/desliga a pilha, custo fora-de-estrada e lado
    /// do tile (afetam a próxima geração/attach).
    NavSet {
        enabled: Option<bool>,
        offroad_cost: Option<f32>,
        tile_size: Option<f32>,
    },
    /// Gate de postfx FORÇADO ao vivo (`VIBER_NO_<KEY>` sem restart).
    PostFx { key: &'static str, on: bool },
    /// Volumes do mixer (mixer_sync aplica aos buses ao vivo).
    AudioSet {
        master: Option<f32>,
        music: Option<f32>,
        sfx: Option<f32>,
    },
    /// Música de combate: "battle" | "boss" | "off" (A/B de BGM sem esperar
    /// os 8 s de hold).
    CombatMusic(String),
    /// Física ao vivo: gravidade do mundo e/ou pausa do pipeline Rapier.
    PhysicsSet {
        gravity: Option<Vec3Arg>,
        paused: Option<bool>,
    },
    /// Grava/carrega pelo caminho da UI (`UiAction "save"/"load"`).
    Save,
    Load,
    /// Teleporta o PLAYER ao primeiro entidade com esse nome (marco, NPC…).
    TeleportTo(String),
    /// Spawn runtime: primitiva FÍSICA (`box:w,h,d`, `sphere:r`,
    /// `cylinder:r,h`) ou GLB do pool (load assíncrono via GltfScenePending).
    Spawn {
        url: String,
        pos: Vec3,
        yaw: Option<f32>,
        scale: Option<f32>,
        color: Option<[f32; 3]>,
        collider: bool,
        snap: bool,
    },
    /// PointLight de debug (`debug:light:N`).
    SpawnLight {
        pos: Vec3,
        intensity: Option<f32>,
        color: Option<[f32; 3]>,
        shadows: bool,
        range: Option<f32>,
    },
    /// Material PBR AO VIVO (só materiais STANDARD de primitivas/GLB — os
    /// bindless do terreno destruíam o bind group num get_mut).
    SetMaterial {
        entity: Entity,
        base_color: Option<[f32; 4]>,
        metallic: Option<f32>,
        roughness: Option<f32>,
        unlit: Option<bool>,
        emissive: Option<[f32; 3]>,
    },
    /// QA determinístico: pausa, avança EXATAMENTE N frames (a speed 1) e
    /// volta a pausar (`viber.debug.step(n)`).
    StepFrames(u32),
    /// Restaura a speed anterior ao `step` (`viber.debug.play()`).
    ResumePlay,
    /// Luz AO VIVO (PointLight/SpotLight; DirectionalLight só illuminance).
    SetLight {
        entity: Entity,
        intensity: Option<f32>,
        color: Option<[f32; 3]>,
        shadows: Option<bool>,
        range: Option<f32>,
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
    pub yaw: Option<f32>,
    pub target: Option<String>,
}

impl FromLua for CameraOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Ok(Self::default()),
            Value::Table(t) => Ok(Self {
                distance: t.get("distance")?,
                pitch: t.get("pitch")?,
                yaw: t.get("yaw")?,
                target: t.get("target")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "camera opts {distance=?, pitch=?, yaw=?, target=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.nav_set{...}` (campos ausentes = sem mudança).
#[derive(Default)]
pub struct NavSetOpts {
    pub enabled: Option<bool>,
    pub offroad_cost: Option<f32>,
    pub tile_size: Option<f32>,
}

impl FromLua for NavSetOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Err(mlua::Error::runtime(
                "nav_set{}: passa pelo menos um campo (enabled, offroad_cost, tile_size)",
            )),
            Value::Table(t) => Ok(Self {
                enabled: t.get("enabled")?,
                offroad_cost: t.get("offroad_cost")?,
                tile_size: t.get("tile_size")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "nav opts {enabled=?, offroad_cost=?, tile_size=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.postfx{taa=?, bloom=?, ...}` — cada campo presente
/// força o gate desse efeito (true = ligado, false = cortado).
#[derive(Default)]
pub struct PostFxOpts {
    pub autoexposure: Option<bool>,
    pub bloom: Option<bool>,
    pub dof: Option<bool>,
    pub ssao: Option<bool>,
    pub contact_shadows: Option<bool>,
    pub aerial: Option<bool>,
    pub splittone: Option<bool>,
    pub vignette: Option<bool>,
    pub chromatic: Option<bool>,
    pub cas: Option<bool>,
    pub motion_blur: Option<bool>,
    pub taa: Option<bool>,
    pub volumetrics: Option<bool>,
}

impl FromLua for PostFxOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Err(mlua::Error::runtime(
                "postfx{}: passa pelo menos um gate (autoexposure, bloom, dof, ssao,                  contact_shadows, aerial, splittone, vignette, chromatic, cas,                  motion_blur, taa, volumetrics)",
            )),
            Value::Table(t) => Ok(Self {
                autoexposure: t.get("autoexposure")?,
                bloom: t.get("bloom")?,
                dof: t.get("dof")?,
                ssao: t.get("ssao")?,
                contact_shadows: t.get("contact_shadows")?,
                aerial: t.get("aerial")?,
                splittone: t.get("splittone")?,
                vignette: t.get("vignette")?,
                chromatic: t.get("chromatic")?,
                cas: t.get("cas")?,
                motion_blur: t.get("motion_blur")?,
                taa: t.get("taa")?,
                volumetrics: t.get("volumetrics")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "postfx opts {bloom=?, ssao=?, taa=?, ...}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.audio_set{...}` (volumes 0..1).
#[derive(Default)]
pub struct AudioSetOpts {
    pub master: Option<f32>,
    pub music: Option<f32>,
    pub sfx: Option<f32>,
}

impl FromLua for AudioSetOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Err(mlua::Error::runtime(
                "audio_set{}: passa pelo menos um volume (master, music, sfx)",
            )),
            Value::Table(t) => Ok(Self {
                master: t.get("master")?,
                music: t.get("music")?,
                sfx: t.get("sfx")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "audio opts {master=?, music=?, sfx=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.physics_set{gravity=?, paused=?}`.
#[derive(Default)]
pub struct PhysicsSetOpts {
    pub gravity: Option<Vec3Arg>,
    pub paused: Option<bool>,
}

impl FromLua for PhysicsSetOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Err(mlua::Error::runtime(
                "physics_set{}: passa pelo menos um campo (gravity={x,y,z}?, paused=?)",
            )),
            Value::Table(t) => Ok(Self {
                gravity: t.get("gravity")?,
                paused: t.get("paused")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "physics opts {gravity=?, paused=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.spawn(url, x, y, z, {...})`.
#[derive(Default)]
pub struct SpawnOpts {
    pub yaw: Option<f32>,
    pub scale: Option<f32>,
    /// Cor `#rrggbb` (primitivas; GLB ignora).
    pub color: Option<String>,
    /// Collider + corpo fixo nas primitivas (default TRUE — o valor do debug
    /// é poder pôr algo COM colisão no mundo; markers visuais são os outros).
    pub collider: Option<bool>,
    /// Assenta o Y no terreno (default TRUE).
    pub snap: Option<bool>,
}

impl FromLua for SpawnOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Ok(Self::default()),
            Value::Table(t) => Ok(Self {
                yaw: t.get("yaw")?,
                scale: t.get("scale")?,
                color: t.get("color")?,
                collider: t.get("collider")?,
                snap: t.get("snap")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "spawn opts {yaw=?, scale=?, color=?, collider=?, snap=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.spawn_light(x, y, z, {...})`.
#[derive(Default)]
pub struct SpawnLightOpts {
    pub intensity: Option<f32>,
    pub color: Option<String>,
    pub shadows: Option<bool>,
    pub range: Option<f32>,
}

impl FromLua for SpawnLightOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Ok(Self::default()),
            Value::Table(t) => Ok(Self {
                intensity: t.get("intensity")?,
                color: t.get("color")?,
                shadows: t.get("shadows")?,
                range: t.get("range")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "light opts {intensity=?, color=?, shadows=?, range=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.set_material(id, {...})` — campos ausentes ficam.
#[derive(Default)]
pub struct MaterialOpts {
    pub base_color: Option<String>,
    pub metallic: Option<f32>,
    pub roughness: Option<f32>,
    pub unlit: Option<bool>,
    pub emissive: Option<String>,
}

impl FromLua for MaterialOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Err(mlua::Error::runtime(
                "set_material{}: passa pelo menos um campo (base_color, metallic,                  roughness, unlit, emissive)",
            )),
            Value::Table(t) => Ok(Self {
                base_color: t.get("base_color")?,
                metallic: t.get("metallic")?,
                roughness: t.get("roughness")?,
                unlit: t.get("unlit")?,
                emissive: t.get("emissive")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "material opts {base_color=?, metallic=?, roughness=?, unlit=?,                      emissive=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.set_light(id, {...})` — campos ausentes ficam.
#[derive(Default)]
pub struct LightOpts {
    pub intensity: Option<f32>,
    pub color: Option<String>,
    pub shadows: Option<bool>,
    pub range: Option<f32>,
}

impl FromLua for LightOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Err(mlua::Error::runtime(
                "set_light{}: passa pelo menos um campo (intensity, color, shadows, range)",
            )),
            Value::Table(t) => Ok(Self {
                intensity: t.get("intensity")?,
                color: t.get("color")?,
                shadows: t.get("shadows")?,
                range: t.get("range")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "light opts {intensity=?, color=?, shadows=?, range=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.set_weather{rain=…, clouds=…, wind=…}`.
#[derive(Default)]
pub struct WeatherOpts {
    pub rain: Option<f32>,
    pub clouds: Option<f32>,
    pub wind: Option<f32>,
}

impl FromLua for WeatherOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Ok(Self::default()),
            Value::Table(t) => Ok(Self {
                rain: t.get("rain")?,
                clouds: t.get("clouds")?,
                wind: t.get("wind")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "weather opts {rain=?, clouds=?, wind=?}".into(),
                message: None,
            }),
        }
    }
}

/// Opções de `viber.debug.rain_look{near_fade=…, alpha=…, width=…, rate=…}`.
#[derive(Default)]
pub struct RainLookOpts {
    pub near_fade: Option<f32>,
    pub alpha: Option<f32>,
    pub width: Option<f32>,
    pub rate: Option<f32>,
}

impl FromLua for RainLookOpts {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Nil => Ok(Self::default()),
            Value::Table(t) => Ok(Self {
                near_fade: t.get("near_fade")?,
                alpha: t.get("alpha")?,
                width: t.get("width")?,
                rate: t.get("rate")?,
            }),
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "rain look {near_fade=?, alpha=?, width=?, rate=?}".into(),
                message: None,
            }),
        }
    }
}

/// `{x,y,z}` (tabela nomeada) ou `{x, y, z}` (array) → [`Vec3`] — tabelas
/// Lua não convertem sozinhas para o tipo da Bevy.
pub struct Vec3Arg(pub Vec3);

impl FromLua for Vec3Arg {
    fn from_lua(value: Value, _lua: &Lua) -> mlua::Result<Self> {
        match value {
            Value::Table(t) => {
                let x: f32 = t.get("x").or_else(|_| t.get(1))?;
                let y: f32 = t.get("y").or_else(|_| t.get(2))?;
                let z: f32 = t.get("z").or_else(|_| t.get(3))?;
                Ok(Self(Vec3::new(x, y, z)))
            }
            other => Err(mlua::Error::FromLuaConversionError {
                from: other.type_name(),
                to: "vec3 {x, y, z}".into(),
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
        let has_terrain = world
            .get_resource::<crate::terrain::runtime::TerrainRuntime>()
            .is_some();
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
    let mut world_hash: u64 = 0xcbf2_9ce4_8422_2325; // offset FNV-1a
    // Contadores da ladder de LOD: recurso, não varrimento de entidades.
    if let Some(lod) = world.get_resource::<crate::render_lod::MeshLodStats>() {
        stats.lod_swaps = lod.swaps_last_frame;
        stats.lod_pending = lod.pending;
    }
    if let Some(interior) = world.get_resource::<crate::worldsys::InteriorLighting>() {
        stats.interior_active = interior.active;
    }
    if let Some(ambient) = world.get_resource::<bevy::light::GlobalAmbientLight>() {
        stats.ambient_brightness = ambient.brightness;
    }
    if let Some(terrain) = world.get_resource::<crate::physics::TerrainCollisionStatus>() {
        stats.terrain_collider_ready = terrain.ready;
    }
    {
        let mut q = world.query::<&crate::player::Player>();
        if let Some(player) = q.iter(world).next() {
            stats.player_grounded = player.grounded;
            stats.player_vel_y = player.vel_y;
        }
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
        let script = e
            .get::<crate::luau::LuaScriptRef>()
            .map(|s| s.path.clone());
        let health = e.get::<Health>().map(|h| (h.current, h.max));
        let ai = ai_info(&e);

        // Agregados sobre o mundo INTEIRO (antes do cap do snapshot).
        stats.entities += 1;
        stats.disabled += usize::from(disabled);
        stats.scripted += usize::from(scripted);
        // Hash do conteúdo por entidade (FNV-1a) acumulado por SOMA —
        // independente de ordem e de reusos de índice de entidade: mundos
        // com o mesmo conteúdo têm o mesmo `world_hash`.
        world_hash = world_hash.wrapping_add(entity_hash(
            name.as_deref(),
            position,
            health,
            disabled,
        ));
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
            script,
            health,
            ai,
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

    // Áudio: snapshot do profiler (buses/layers/sinks) — barato (5 Hz lá).
    let audio = crate::profiler::audio_tab::snapshot(world);
    let audio = serde_json::to_value(&audio).ok();

    // Terreno partilhado (Arc clones) + registos de estrada/água para as
    // queries posicionais ao vivo de `viber.debug.terrain(x, z)`.
    let (terrain, surfaces) = world
        .get_resource::<crate::terrain::runtime::TerrainRuntime>()
        .map(|rt| {
            (
                Some(rt.reader()),
                Some(crate::luau::SurfaceRegistries {
                    roads: rt.roads.clone(),
                    water: rt.water.clone(),
                }),
            )
        })
        .unwrap_or((None, None));

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
        nav: build_nav(world),
        quests_deep: build_quests_deep(world),
        regions: build_regions(world),
        atmosphere: build_atmosphere(world),
        weather: build_weather(world),
        border: build_border(world),
        interior: build_interior(world),
        ui: build_ui(world),
        audio,
        seeds: build_seeds(world),
        skills: build_skills(world),
        waypoints: build_waypoints(world),
        save: build_save(world),
        terrain,
        surfaces,
        world_hash,
        events: world
            .get_resource::<super::events::BridgeEventLog>()
            .map(|log| log.tail_json())
            .unwrap_or(Json::Null),
    }
}

/// IA de uma entidade no snapshot — FSM da engine + locomoção + perfil nav.
fn ai_info(e: &bevy::ecs::world::EntityRef) -> Option<AiInfo> {
    let fsm = e.get::<crate::ai::EnemyCreature>();
    let loco = e.get::<crate::ai::AiLocomotion>();
    let profile = e
        .get::<crate::nav::NavProfile>()
        .map(|p| match p {
            crate::nav::NavProfile::Civil => "civil",
            crate::nav::NavProfile::Wild => "wild",
        });
    (fsm.is_some() || loco.is_some() || profile.is_some()).then(|| AiInfo {
        state: fsm.map(|f| match f.state {
            crate::ai::EnemyState::Wander => "wander",
            crate::ai::EnemyState::Chase => "chase",
        }).map(str::to_string),
        speed: fsm.map(|f| f.speed).unwrap_or(0.0),
        aggro_radius: fsm.map(|f| f.aggro_radius).unwrap_or(0.0),
        attack_radius: fsm.map(|f| f.attack_radius).unwrap_or(0.0),
        home: fsm.and_then(|f| f.home).map(|h| [h.x, h.y]),
        desired: loco.map(|l| l.desired()).unwrap_or_default().to_array(),
        velocity: loco.map(|l| [l.velocity.x, l.velocity.y]).unwrap_or_default(),
        goal: loco.and_then(|l| l.goal()).map(|g| [g.x, g.y]),
        nav_profile: profile.map(str::to_string),
    })
}

/// FNV-1a de uma entidade (nome + posição + hp + disabled), para o
/// `world_hash` — soma wrapping de todos os hashes é independente de ordem.
fn entity_hash(
    name: Option<&str>,
    position: Option<Vec3>,
    health: Option<(f32, f32)>,
    disabled: bool,
) -> u64 {
    let mut hash = 0xcbf2_9ce4_8422_2325_u64;
    let mut feed = |bytes: &[u8]| {
        for byte in bytes {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x0000_0100_0000_01B3);
        }
    };
    feed(name.unwrap_or("").as_bytes());
    feed(&[disabled as u8]);
    if let Some(p) = position {
        feed(&p.x.to_bits().to_le_bytes());
        feed(&p.y.to_bits().to_le_bytes());
        feed(&p.z.to_bits().to_le_bytes());
    }
    if let Some((current, max)) = health {
        feed(&current.to_bits().to_le_bytes());
        feed(&max.to_bits().to_le_bytes());
    }
    hash
}

/// Pilha de navegação no snapshot — config + tile + census de estados.
fn build_nav(world: &mut World) -> Option<NavInfo> {
    world.get_resource::<crate::nav::NavConfig>()?;
    let mut census: HashMap<&'static str, usize> = HashMap::new();
    {
        let mut q = world.query::<&AgentState>();
        for state in q.iter(world) {
            *census.entry(crate::nav::agent::state_name(state)).or_insert(0) += 1;
        }
    }
    let mut census: Vec<(&'static str, usize)> = census.into_iter().collect();
    census.sort();
    let config = world.resource::<crate::nav::NavConfig>();
    let tile = world.get_resource::<crate::nav::NavTile>();
    Some(NavInfo {
        enabled: config.enabled,
        agent_radius: config.agent_radius,
        agent_height: config.agent_height,
        tile_size: config.tile_size,
        offroad_cost: config.offroad_cost,
        tile_center: tile.and_then(|t| t.center).map(|c| [c.x, c.y]),
        tile_generating: tile.is_some_and(|t| t.generating),
        tile_generations: tile.map(|t| t.generations).unwrap_or(0),
        tile_obstacles: tile.and_then(|t| t.baked_obstacles).map(|n| n as u64),
        census,
    })
}

/// Quests com definição + estado vivo (o `quests()` simples é só o mapa de
/// estados — este traz título/objetivo/progresso para o agente decidir).
fn build_quests_deep(world: &World) -> Vec<QuestInfo> {
    let Some(log) = world.get_resource::<crate::quests::QuestLog>() else {
        return Vec::new();
    };
    let vault = world.get_resource::<crate::economy::Vault>();
    let mut out: Vec<QuestInfo> = log
        .defs
        .iter()
        .map(|def| QuestInfo {
            id: def.id.clone(),
            title: def.title.clone(),
            npc: def.npc.clone(),
            biome: def.biome.clone(),
            status: crate::quests::status_name(log.status(&def.id, vault)).to_string(),
            objective_kind: def.objective.kind.clone(),
            objective_target: def.objective.target.clone(),
            objective_count: def.objective.count,
            progress_text: log.progress_text(&def.id, vault),
            visited: log
                .states
                .get(&def.id)
                .map(|a| a.visited.clone())
                .unwrap_or_default(),
            rewards_gold: def.rewards.gold,
            rewards_xp: def.rewards.xp,
            rewards_items: def.rewards.items.clone(),
        })
        .collect();
    out.sort_by(|a, b| a.id.cmp(&b.id));
    out
}

/// `<BiomeRegion>` do mundo — polígonos são pequenos (dezenas de pontos).
fn build_regions(world: &World) -> Vec<RegionInfo> {
    world
        .get_resource::<crate::worldsys::BiomeRegions>()
        .map(|regions| {
            regions
                .list
                .iter()
                .map(|b| RegionInfo {
                    id: b.id.clone(),
                    display_name: b.display_name.clone(),
                    polygon: b.polygon.clone(),
                    fog_density: b.fog_density,
                    tint: b.tint,
                    pp_exposure: b.pp_exposure,
                    pp_bloom_strength: b.pp_bloom_strength,
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Atmosfera + névoa vivos (o `AtmosphereState` é o estado do grading; a
/// densidade da névoa vive no `DistanceFog` da câmara).
fn build_atmosphere(world: &World) -> Option<AtmosphereInfo> {
    let at = world.get_resource::<crate::worldsys::AtmosphereState>()?;
    let fog_density = world.iter_entities().find_map(|e| {
        let fog = e.get::<bevy::pbr::DistanceFog>()?;
        match fog.falloff {
            bevy::pbr::FogFalloff::ExponentialSquared { density } => Some(density),
            _ => None,
        }
    });
    Some(AtmosphereInfo {
        day: at.day,
        night: at.night,
        golden: at.golden,
        fog_density,
        fog_color: [at.fog[0], at.fog[1], at.fog[2]],
        exposure_scale: at.exposure_scale,
        bloom_boost: at.bloom_boost,
    })
}

/// Tempo completo — `WeatherState` + scheduler do ciclo (o que VAI acontecer).
fn build_weather(world: &World) -> Option<WeatherInfo> {
    let weather = world.get_resource::<crate::worldsys::WeatherState>()?;
    let scheduler = world
        .get_resource::<crate::worldsys::WeatherScheduler>()
        .map(|s| SchedulerInfo {
            seed: s.seed,
            index: s.index,
            period: s.period,
            timer: s.timer,
            target: s.target,
        });
    Some(WeatherInfo {
        wind: weather.wind,
        wind_strength: weather.wind_strength,
        clouds: weather.clouds,
        rain: weather.rain,
        cycle: weather.cycle,
        scheduler,
    })
}

fn build_border(world: &World) -> Option<BorderInfo> {
    world
        .get_resource::<crate::worldsys::WorldBorderConfig>()
        .map(|b| BorderInfo {
            radius: b.radius,
            warn_seconds: b.warn_seconds,
            margin: b.margin,
        })
}

fn build_interior(world: &World) -> Option<InteriorInfo> {
    let active = world
        .get_resource::<crate::worldsys::InteriorLighting>()
        .is_some_and(|i| i.active);
    let config = world.get_resource::<crate::worldsys::InteriorSceneConfig>();
    (config.is_some() || active).then(|| InteriorInfo {
        active,
        min: config.map(|c| c.min),
        max: config.map(|c| c.max),
        room_size: config.map(|c| c.room_size).unwrap_or([0.0, 0.0]),
        room_origin: config.map(|c| c.room_origin).unwrap_or([0.0, 0.0]),
        camera_distance: config.map(|c| c.camera_distance).unwrap_or(0.0),
        camera_pitch_deg: config.map(|c| c.camera_pitch_deg).unwrap_or(0.0),
        camera_yaw_deg: config.map(|c| c.camera_yaw_deg).unwrap_or(0.0),
    })
}

/// Rect + estado de um nó de UI (rect = GlobalTransform XY + ComputedNode).
fn ui_node_info(world: &World, entity: Entity, id: String) -> Option<UiNodeInfo> {
    let e = world.get_entity(entity).ok()?;
    let node = e.get::<bevy::ui::ComputedNode>()?;
    let g = e.get::<GlobalTransform>()?;
    let pos = g.translation().xy();
    // Texto próprio + descendentes (o Bevy 0.19 põe spans nos filhos).
    let mut text = String::new();
    if let Some(span) = e.get::<bevy::text::TextSpan>() {
        text.push_str(&span.0);
    }
    if let Some(children) = e.get::<Children>() {
        for child in children.iter() {
            if let Ok(child) = world.get_entity(child)
                && let Some(span) = child.get::<bevy::text::TextSpan>()
            {
                text.push_str(&span.0);
            }
        }
    }
    Some(UiNodeInfo {
        id,
        x: pos.x,
        y: pos.y,
        w: node.size.x,
        h: node.size.y,
        visible: e.get::<Visibility>() != Some(&Visibility::Hidden),
        disabled: e.get::<crate::ui::runtime::UiDisabled>().is_some(),
        text: (!text.is_empty()).then_some(text),
        classes: e
            .get::<crate::ui::runtime::UiClasses>()
            .map(|c| c.0.clone())
            .unwrap_or_default(),
    })
}

/// UI endereçável: ids declarativos do registry + nós nomeados `hud:*`/`chip:*`.
fn build_ui(world: &World) -> Vec<UiNodeInfo> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    if let Some(registry) = world.get_resource::<crate::ui::runtime::UiRegistry>() {
        let mut ids: Vec<(&String, Entity)> =
            registry.by_id.iter().map(|(id, e)| (id, *e)).collect();
        ids.sort_by(|a, b| a.0.cmp(b.0));
        for (id, entity) in ids {
            seen.insert(entity);
            if let Some(info) = ui_node_info(world, entity, id.clone()) {
                out.push(info);
            }
        }
    }
    // HUD da engine não passa pelo registry declarativo — nomeia-se com
    // `Name` (`hud:health`, `chip:gold`…).
    for e in world.iter_entities() {
        if seen.contains(&e.id()) {
            continue;
        }
        let Some(name) = e.get::<Name>() else {
            continue;
        };
        let name = name.as_str();
        if !name.starts_with("hud:") && !name.starts_with("chip:") {
            continue;
        }
        if let Some(info) = ui_node_info(world, e.id(), name.to_string()) {
            out.push(info);
        }
    }
    out
}

fn build_skills(world: &World) -> Option<SkillsInfo> {
    let tree = world.get_resource::<crate::skills::SkillTree>()?;
    let cooldowns = world
        .get_resource::<crate::skills::AbilityCooldowns>()
        .map(|c| [c.dash, c.heal, c.strike]);
    let stats = world.get_resource::<crate::skills::PlayerStatsResource>();
    let level = world
        .iter_entities()
        .find_map(|e| e.get::<crate::skills::LevelState>().map(|l| (l.level, l.points)));
    Some(SkillsInfo {
        learned: tree.learned.clone(),
        points: tree.points,
        level: level.map(|l| l.0),
        level_points: level.map(|l| l.1),
        cooldowns,
        bonus_damage: stats.map(|s| s.0.bonus_damage),
        speed_mult: stats.map(|s| s.0.speed_mult),
        max_hp_bonus: stats.map(|s| s.0.max_hp_bonus),
        crit_bonus: stats.map(|s| s.0.crit_bonus),
    })
}

fn build_seeds(world: &World) -> SeedInfo {
    let terrain = world.get_resource::<crate::terrain::runtime::TerrainRuntime>();
    let weather_seed = world
        .get_resource::<crate::worldsys::WeatherScheduler>()
        .map(|s| s.seed);
    SeedInfo {
        terrain_seed: terrain.map(|t| t.spec.seed),
        world_size: terrain.map(|t| t.spec.world_size),
        weather_seed,
    }
}

fn build_waypoints(world: &World) -> WaypointInfo {
    let marked = world
        .get_resource::<crate::travel::NotaLog>()
        .map(|log| {
            let mut marked: Vec<String> = log.marked.iter().cloned().collect();
            marked.sort();
            marked
        })
        .unwrap_or_default();
    let waypoint = world.get_resource::<crate::travel::Waypoint>();
    WaypointInfo {
        marked,
        // Fase B2: o label é String (catálogo declarável) — clone, não borrow.
        label: waypoint.and_then(|w| w.label.clone()),
        position: waypoint.and_then(|w| w.position).map(|p| p.to_array()),
        landmarks: crate::travel::LANDMARKS
            .iter()
            .map(|l| (l.name, l.biome.label()))
            .collect(),
    }
}

fn build_save(world: &World) -> Option<SaveInfo> {
    let world_key = world
        .get_resource::<crate::save::WorldSaveKey>()
        .and_then(|w| w.0.as_deref());
    let save_dir = world
        .get_resource::<crate::save::SaveDir>()
        .and_then(|d| d.0.as_deref());
    let path = crate::save::save_path_for(world_key, save_dir);
    let meta = std::fs::metadata(&path).ok();
    Some(SaveInfo {
        path: path.display().to_string(),
        exists: meta.is_some(),
        bytes: meta.as_ref().map(|m| m.len()),
        mtime: meta
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64()),
    })
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

/// Documentação de `viber.debug.*`: `(nome, assinatura, descrição)`.
///
/// A fonte da VERDADE é a tabela construída em [`ensure_debug_api`]; esta
/// lista é a documentação e o guard test
/// (`test_apidoc_covers_registered_functions`) garante a paridade exata nos
/// dois sentidos — função nova sem doc falha o teste, doc órfã também.
pub const DEBUG_API_DOCS: &[(&str, &str, &str)] = &[
    ("entities", "entities(radius?)", "entidades no snapshot {id,name?,x,y,z,disabled} (cap 4096, mais perto 1.º)"),
    ("find", "find(name)", "id (bits) por nome exato → substring case-insensitive"),
    ("find_all", "find_all(name)", "ids (bits) de todas as entidades que casam (substring)"),
    ("pos", "pos(id)", "(x, y, z) da entidade"),
    ("info", "info(id)", "tudo: transform, mesh, material, collider, luz, hp, script, ai"),
    ("components", "components(id)", "nomes dos componentes (por arquétipo)"),
    ("transform", "transform(id)", "{x,y,z,pitch,yaw,roll,sx,sy,sz,gx?,gy?,gz?}"),
    ("mesh", "mesh(id)", "{topology,vertices,indices,has_normals,has_uvs,uv_bounds}"),
    ("material", "material(id)", "{base_color,metallic,roughness,unlit,texturas{w,h}}"),
    ("collider", "collider(id)", "resumo do shape Rapier"),
    ("health", "health(id)", "{current,max,dead} de QUALQUER entidade"),
    ("ai", "ai(id)", "FSM+locomoção da criatura {state,speed,aggro,goal,nav_profile}"),
    ("nav", "nav()", "estado da pilha de navegação {enabled,tile,census}"),
    ("player", "player()", "{id,x,y,z,hp,max_hp,xp,xp_next,speed}"),
    ("camera", "camera()", "pose da OrbitCamera"),
    ("clock", "clock()", "{minute,dawn,dusk,minutes_per_real_second}"),
    ("vault", "vault()", "{gold,wood,stone,items{}}"),
    ("quests", "quests()", "{id = estado}"),
    ("quest", "quest(id)", "quest FUNDA: título, objetivo com progresso, rewards"),
    ("quest_defs", "quest_defs()", "[{id,title,status,kind,npc}] das quests embutidas"),
    ("regions", "regions()", "<BiomeRegion> com fog/tint/exposure"),
    ("biome_at", "biome_at(x, z)", "região do ponto (polígono)"),
    ("terrain", "terrain(x, z)", "queries AO VIVO: altura, estrada, água, distância a estrada"),
    ("weather_full", "weather_full()", "tempo + scheduler do ciclo (o que VAI acontecer)"),
    ("atmosphere", "atmosphere()", "grading/névoa vivos {day,night,fog_density,exposure}"),
    ("border", "border()", "<WorldBorder> {radius,warn_seconds,margin}"),
    ("interior", "interior()", "bolsa de interior {active,min,max,room_*}"),
    ("ui_tree", "ui_tree()", "UI endereçável com RECTS (clique exato via input.click)"),
    ("audio", "audio()", "buses/layers/sinks do mixer"),
    ("seeds", "seeds()", "{terrain_seed,world_size,weather_seed} (determinismo)"),
    ("world_hash", "world_hash()", "hash hex do conteúdo do mundo (A/B de determinismo)"),
    ("skills", "skills()", "árvore, pontos, nível, cooldowns, bónus"),
    ("waypoints", "waypoints()", "Nota marcada + waypoint + 12 marcos"),
    ("save_info", "save_info()", "{path,exists,bytes,mtime} do save"),
    ("stats", "stats()", "agregados do mundo inteiro (cap-free)"),
    ("physics", "physics()", "tempos do último step do Rapier"),
    ("colliders", "colliders(radius?)", "dump de colliders (cap 256)"),
    ("lights", "lights(radius?)", "dump de luzes (com shadows destacado)"),
    ("around", "around(radius, limit?)", "resumo compacto do que está perto do player"),
    ("prof", "prof()", "snapshot do profiler"),
    ("fps", "fps()", "fps instantâneo"),
    ("time_scale", "time_scale()", "speed virtual atual"),
    ("distance", "distance(a, b)", "metros entre duas entidades"),
    ("ground_state", "ground_state()", "tuning de splat + pele das paredes correntes"),
    ("events", "events(since?)", "eventos de jogo estruturados desde o cursor seq"),
    ("apidoc", "apidoc()", "ESTA tabela: assinaturas + descrições de toda a API"),
    ("set_pos", "set_pos(id, x, y, z)", "posição absoluta, sem snap"),
    ("teleport", "teleport(x, y, z)", "player, Y explícito"),
    ("tp", "tp(x, z)", "player, Y sentado no terreno"),
    ("move_to", "move_to(id, x, z)", "qualquer entidade, Y no terreno"),
    ("move_player", "move_player(dx, dz)", "delta XZ do player, Y no terreno"),
    ("face", "face(x, z)", "player olha para o ponto"),
    ("teleport_to", "teleport_to(name)", "player → primeira entidade com esse nome"),
    ("rotate", "rotate(id, graus)", "soma yaw em graus em torno do Y"),
    ("set_scale", "set_scale(id, s)", "escala uniforme (mín. 0.001)"),
    ("hide", "hide(id)", "esconde"),
    ("show", "show(id)", "mostra"),
    ("toggle_vis", "toggle_vis(id)", "alterna visibilidade"),
    ("disable", "disable(id)", "insere Disabled (sai de TODAS as queries)"),
    ("enable", "enable(id)", "remove Disabled"),
    ("despawn", "despawn(id)", "remove a entidade"),
    ("heal", "heal(n)", "cura o player"),
    ("damage", "damage(n)", "dano no player"),
    ("xp", "xp(n)", "XP ao player"),
    ("give", "give(what, n)", "recurso/item → vault (aditivo)"),
    ("take", "take(what, n)", "tira do vault (recurso ou item)"),
    ("vault_set", "vault_set(what, n)", "valor ABSOLUTO de recurso/item"),
    ("set_speed", "set_speed(n)", "velocidade do player"),
    ("set_time_scale", "set_time_scale(n)", "slow-mo; 0 = pausa"),
    ("set_entity_hp", "set_entity_hp(id, hp)", "HP absoluto de QUALQUER entidade"),
    ("set_max_hp", "set_max_hp(id, max)", "HP máximo de qualquer entidade"),
    ("kill", "kill(id)", "HP a zero (sem i-frames)"),
    ("set_hp", "set_hp(hp)", "HP do player (absoluto, clamp)"),
    ("quest_force", "quest_force(id, state)", "força active|ready|done|not_taken"),
    ("quest_progress", "quest_progress(id, n)", "fixa o progresso (kill/visit)"),
    ("skill_learn", "skill_learn(id)", "aprende passiva (aplica o delta ao herói)"),
    ("skill_points", "skill_points(n)", "pontos disponíveis (absoluto)"),
    ("skill_reset", "skill_reset()", "esquece tudo, devolve pontos, reverte bónus"),
    ("ai_state", "ai_state(id, s)", "força wander|chase (a FSM reavalia por distância)"),
    ("ai_aggro", "ai_aggro(id, r)", "raio de aggro (o lever que persiste)"),
    ("ai_calm_all", "ai_calm_all()", "todas as criaturas → Wander"),
    ("nav_set", "nav_set{...}", "navmesh ao vivo: enabled/offroad_cost/tile_size"),
    ("postfx", "postfx{...}", "gates de efeito AO VIVO (bloom, ssao, taa, …)"),
    ("audio_set", "audio_set{...}", "volumes master/music/sfx ao vivo"),
    ("combat_music", "combat_music(s)", "battle|boss|off (A/B de BGM)"),
    ("physics_set", "physics_set{...}", "gravidade e/ou pausa do pipeline Rapier"),
    ("set_camera", "set_camera{...}", "distance/pitch/yaw/target da OrbitCamera"),
    ("set_clock", "set_clock(minute)", "0–1440 (1380 = noite)"),
    ("set_weather", "set_weather{...}", "rain/clouds/wind (congela o ciclo)"),
    ("rain_look", "rain_look{...}", "look da chuva ao vivo"),
    ("set_window", "set_window(w, h)", "resize p/ QA responsivo"),
    ("sun", "sun{...}", "sol da cena E do shader do terreno"),
    ("ground", "ground{...}", "splat + pele das paredes ao vivo"),
    ("save", "save()", "grava (mesmo caminho da UI)"),
    ("load", "load()", "carrega o save"),
    ("toast", "toast(msg)", "mensagem no HUD"),
    ("spawn_sphere", "spawn_sphere(x, y, z, r, hex?)", "marker debug:sphere:N (visual)"),
    ("spawn_box", "spawn_box(x, y, z, size, hex?)", "marker debug:box:N (visual)"),
    ("spawn_light", "spawn_light(x, y, z, {...})", "PointLight debug:light:N"),
    ("spawn", "spawn(url, x, y, z, {...})", "primitiva FÍSICA (box/sphere/cylinder) ou GLB do pool"),
    ("set_material", "set_material(id, {...})", "material PBR ao vivo (só standard)"),
    ("set_light", "set_light(id, {...})", "intensity/color/shadows/range ao vivo"),
    ("clear_markers", "clear_markers()", "remove TODO o namespace debug:*"),
    ("step", "step(n)", "pausa e avança EXATAMENTE n frames (QA determinístico)"),
    ("play", "play()", "restaura a speed anterior ao step"),
];

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
            EntityArg::Id(bits) => Entity::try_from_bits(bits)
                .ok_or_else(|| mlua::Error::runtime(format!("id de entidade inválido: {bits}"))),
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
            table.raw_set("assets", {
                let t = lua.create_table()?;
                t.raw_set("meshes", s.assets_meshes)?;
                t.raw_set("materials", s.assets_materials)?;
                t.raw_set("images", s.assets_images)?;
                t
            })?;
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
            table.raw_set("terrain_collider_ready", s.terrain_collider_ready)?;
            table.raw_set("player_grounded", s.player_grounded)?;
            table.raw_set("player_vel_y", s.player_vel_y)?;
            table.raw_set("interior_active", s.interior_active)?;
            table.raw_set("ambient_brightness", s.ambient_brightness)?;
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
                    yaw: opts.yaw,
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
        "rain_look",
        lua.create_function(|lua, opts: RainLookOpts| {
            push(
                lua,
                DebugOp::RainLook {
                    near_fade: opts.near_fade,
                    alpha: opts.alpha,
                    width: opts.width,
                    rate: opts.rate,
                },
            )
        })?,
    )?;
    api.set(
        "set_weather",
        lua.create_function(|lua, opts: WeatherOpts| {
            push(
                lua,
                DebugOp::SetWeather {
                    rain: opts.rain,
                    clouds: opts.clouds,
                    wind: opts.wind,
                },
            )
        })?,
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

    // ── Introspecção profunda (vitals de qualquer entidade, IA, nav, …) ──
    api.set(
        "health",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some((current, max)) = view
                .entities
                .iter()
                .find(|info| info.id == entity)
                .and_then(|info| info.health)
            else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({ "current": current, "max": max, "dead": current <= 0.0 }),
            )?)
        })?,
    )?;

    api.set(
        "ai",
        lua.create_function(|lua, arg: EntityArg| {
            let entity = resolve(lua, arg)?;
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(ai) = view
                .entities
                .iter()
                .find(|info| info.id == entity)
                .and_then(|info| info.ai.as_ref())
            else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "state": ai.state,
                    "speed": ai.speed,
                    "aggro_radius": ai.aggro_radius,
                    "attack_radius": ai.attack_radius,
                    "home": ai.home,
                    "desired": ai.desired,
                    "velocity": ai.velocity,
                    "goal": ai.goal,
                    "nav_profile": ai.nav_profile,
                }),
            )?)
        })?,
    )?;

    api.set(
        "nav",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(nav) = view.nav.as_ref() else {
                return Ok(Value::Nil);
            };
            let census = lua.create_table()?;
            for (name, count) in &nav.census {
                census.raw_set(*name, *count)?;
            }
            let table = json_to_lua(
                lua,
                &json!({
                    "enabled": nav.enabled,
                    "agent_radius": nav.agent_radius,
                    "agent_height": nav.agent_height,
                    "tile_size": nav.tile_size,
                    "offroad_cost": nav.offroad_cost,
                    "tile_center": nav.tile_center,
                    "tile_generating": nav.tile_generating,
                    "tile_generations": nav.tile_generations,
                    "tile_obstacles": nav.tile_obstacles,
                }),
            )?;
            if let Value::Table(table) = &table {
                table.raw_set("census", census)?;
            }
            Ok(table)
        })?,
    )?;

    api.set(
        "quest",
        lua.create_function(|lua, id: String| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(quest) = view.quests_deep.iter().find(|q| q.id == id) else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "id": quest.id,
                    "title": quest.title,
                    "npc": quest.npc,
                    "biome": quest.biome,
                    "status": quest.status,
                    "objective": {
                        "kind": quest.objective_kind,
                        "target": quest.objective_target,
                        "count": quest.objective_count,
                        "progress_text": quest.progress_text,
                    },
                    "visited": quest.visited,
                    "rewards": {
                        "gold": quest.rewards_gold,
                        "xp": quest.rewards_xp,
                        "items": quest.rewards_items,
                    },
                }),
            )?)
        })?,
    )?;

    api.set(
        "quest_defs",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let list: Vec<Json> = view
                .quests_deep
                .iter()
                .map(|q| {
                    json!({ "id": q.id, "title": q.title, "status": q.status,
                            "kind": q.objective_kind, "npc": q.npc })
                })
                .collect();
            Ok(json_to_lua(lua, &Json::Array(list))?)
        })?,
    )?;

    api.set(
        "regions",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let list: Vec<Json> = view
                .regions
                .iter()
                .map(|r| {
                    json!({
                        "id": r.id, "display_name": r.display_name,
                        "fog_density": r.fog_density, "tint": r.tint,
                        "pp_exposure": r.pp_exposure,
                        "pp_bloom_strength": r.pp_bloom_strength,
                    })
                })
                .collect();
            Ok(json_to_lua(lua, &Json::Array(list))?)
        })?,
    )?;

    api.set(
        "biome_at",
        lua.create_function(|lua, (x, z): (f32, f32)| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(region) = view
                .regions
                .iter()
                .find(|r| crate::ambient::point_in_polygon(x, z, &r.polygon))
            else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "id": region.id,
                    "display_name": region.display_name,
                    "fog_density": region.fog_density,
                    "tint": region.tint,
                    "pp_exposure": region.pp_exposure,
                    "pp_bloom_strength": region.pp_bloom_strength,
                }),
            )?)
        })?,
    )?;

    // `terrain(x, z)` — queries posicionais AO VIVO (o terreno vive atrás de
    // Arcs: o snapshot só guarda os handles).
    api.set(
        "terrain",
        lua.create_function(|lua, (x, z): (f32, f32)| {
            if !(x.is_finite() && z.is_finite()) {
                return Err(mlua::Error::runtime("terrain: x/z não finitos (NaN/inf)"));
            }
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(terrain) = view.terrain.as_ref() else {
                return Ok(Value::Nil);
            };
            let base = terrain.base();
            let height = if terrain.voxel.is_flat() {
                crate::terrain::mesh::HeightField::sample(&base, x, z)
            } else {
                terrain.voxel.surface_top(&base, x, z)
            };
            let water_surface = view.surfaces.as_ref().and_then(|s| {
                let p = Vec2::new(x, z);
                s.water
                    .iter()
                    .filter(|w| w.contains(p))
                    .filter_map(|w| w.surface_y_at(p))
                    .fold(None::<f32>, |acc, y| Some(acc.map_or(y, |max| max.max(y))))
            });
            let distance_to_road = view.surfaces.as_ref().map(|s| {
                let p = Vec2::new(x, z);
                s.roads
                    .iter()
                    .map(|r| r.distance_to_road(p))
                    .fold(f32::MAX, f32::min)
            });
            Ok(json_to_lua(
                lua,
                &json!({
                    "height": height,
                    "in_field": view.seeds.world_size.is_some_and(|half| {
                        let half = half * 0.5;
                        x >= -half && x <= half && z >= -half && z <= half
                    }),
                    "on_road": view.surfaces.as_ref().is_some_and(|s| s.on_road(x, z)),
                    "in_water": view.surfaces.as_ref().is_some_and(|s| s.in_water(x, z)),
                    "water_surface": water_surface,
                    "distance_to_road": distance_to_road,
                }),
            )?)
        })?,
    )?;

    api.set(
        "weather_full",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(w) = view.weather.as_ref() else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "wind": w.wind,
                    "wind_strength": w.wind_strength,
                    "clouds": w.clouds,
                    "rain": w.rain,
                    "cycle": w.cycle,
                    "scheduler": w.scheduler.as_ref().map(|s| json!({
                        "seed": s.seed, "index": s.index, "period": s.period,
                        "timer": s.timer, "target": s.target,
                    })),
                }),
            )?)
        })?,
    )?;

    api.set(
        "atmosphere",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(a) = view.atmosphere.as_ref() else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "day": a.day, "night": a.night, "golden": a.golden,
                    "fog_density": a.fog_density, "fog_color": a.fog_color,
                    "exposure_scale": a.exposure_scale, "bloom_boost": a.bloom_boost,
                }),
            )?)
        })?,
    )?;

    api.set(
        "border",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(b) = view.border.as_ref() else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({ "radius": b.radius, "warn_seconds": b.warn_seconds, "margin": b.margin }),
            )?)
        })?,
    )?;

    api.set(
        "interior",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(i) = view.interior.as_ref() else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "active": i.active, "min": i.min, "max": i.max,
                    "room_size": i.room_size, "room_origin": i.room_origin,
                    "camera_distance": i.camera_distance,
                    "camera_pitch_deg": i.camera_pitch_deg,
                    "camera_yaw_deg": i.camera_yaw_deg,
                }),
            )?)
        })?,
    )?;

    // `ui_tree()` — ids declarativos + hud:* com RECTS: dá o clique exato
    // (`viber.input.click`) sem adivinhar coordenadas no screenshot.
    api.set(
        "ui_tree",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let list: Vec<Json> = view
                .ui
                .iter()
                .map(|n| {
                    json!({
                        "id": n.id, "x": n.x, "y": n.y, "w": n.w, "h": n.h,
                        "visible": n.visible, "disabled": n.disabled,
                        "text": n.text, "classes": n.classes,
                    })
                })
                .collect();
            Ok(json_to_lua(lua, &Json::Array(list))?)
        })?,
    )?;

    api.set(
        "audio",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            match view.audio.as_ref() {
                Some(audio) => json_to_lua(lua, audio),
                None => Ok(Value::Nil),
            }
        })?,
    )?;

    api.set(
        "seeds",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            Ok(json_to_lua(
                lua,
                &json!({
                    "terrain_seed": view.seeds.terrain_seed,
                    "world_size": view.seeds.world_size,
                    "weather_seed": view.seeds.weather_seed,
                }),
            )?)
        })?,
    )?;

    // Hash do CONTEÚDO do mundo (soma FNV-1a por entidade, independente de
    // ordem/ids): dois boots da mesma seed do mesmo binário têm o MESMO hash.
    // STRING hex — u64 não sobrevive ao f64 do Lua.
    api.set(
        "world_hash",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            Ok(format!("{:016x}", view.world_hash))
        })?,
    )?;

    // Event log estruturado (cauda do ring no snapshot; `since` = cursor).
    api.set(
        "events",
        lua.create_function(|lua, since: Option<f64>| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let since = since.unwrap_or(0.0);
            let Some(list) = view.events.as_array() else {
                return Ok(Value::Nil);
            };
            let filtered: Vec<Json> = list
                .iter()
                .filter(|event| {
                    event.get("seq").and_then(Json::as_f64).unwrap_or(0.0) > since
                })
                .cloned()
                .collect();
            Ok(json_to_lua(lua, &Json::Array(filtered))?)
        })?,
    )?;

    api.set(
        "step",
        lua.create_function(|lua, frames: u32| push(lua, DebugOp::StepFrames(frames)))?,
    )?;
    api.set(
        "play",
        lua.create_function(|lua, ()| push(lua, DebugOp::ResumePlay))?,
    )?;

    // ── Auto-descoberta (M4): apidoc — assinatura + 1 linha por função ──
    // A tabela DEBUG_API_DOCS é a fonte; o guard test garante que cobre
    // EXATAMENTE as chaves registadas acima (nenhuma a menos, nenhuma
    // esquecida) — o `test_apidoc_covers_registered_functions`.
    api.set(
        "apidoc",
        lua.create_function(|lua, ()| {
            let view = lua.app_data_ref::<DebugView>();
            let _ = view; // (docs são estáticas; a view não é precisa)
            let mut out = lua.create_table()?;

            // 1) viber.debug.* — documentação estática.
            let debug_docs = lua.create_table()?;
            for (name, signature, desc) in DEBUG_API_DOCS {
                let entry = lua.create_table()?;
                entry.raw_set("signature", *signature)?;
                entry.raw_set("description", *desc)?;
                entry.raw_set("group", "debug")?;
                debug_docs.raw_set(*name, entry)?;
            }
            out.raw_set("debug", debug_docs)?;

            // 2) viber.* dos scripts de jogo + ui/profiler — ENUMERADOS da
            //    tabela viva (a verdade do binário, não de docs antigas).
            let enumerate = |lua: &Lua, table: Value| -> mlua::Result<Vec<String>> {
                let mut names = Vec::new();
                if let Value::Table(t) = table {
                    for pair in t.clone().pairs::<Value, Value>() {
                        if let (Value::String(k), Value::Function(_)) = pair? {
                            names.push(k.to_str()?.to_string());
                        }
                    }
                }
                names.sort();
                Ok(names)
            };
            let viber_table: Value = lua.globals().get("viber")?;
            let game_api = enumerate(lua, viber_table.clone())?;
            let game = lua.create_table()?;
            for name in game_api {
                game.raw_set(name.as_str(), true)?;
            }
            out.raw_set("game", game)?;
            if let Value::Table(t) = &viber_table {
                if let Ok(ui) = t.get::<Value>("ui")
                    && let Ok(names) = enumerate(lua, ui)
                {
                    let ui_table = lua.create_table()?;
                    for name in names {
                        ui_table.raw_set(name.as_str(), true)?;
                    }
                    out.raw_set("ui", ui_table)?;
                }
                if let Ok(profiler) = t.get::<Value>("profiler")
                    && let Ok(names) = enumerate(lua, profiler)
                {
                    let prof = lua.create_table()?;
                    for name in names {
                        prof.raw_set(name.as_str(), true)?;
                    }
                    out.raw_set("profiler", prof)?;
                }
            }
            Ok(Value::Table(out))
        })?,
    )?;

    api.set(
        "skills",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(s) = view.skills.as_ref() else {
                return Ok(Value::Nil);
            };
            let learned = lua.create_sequence_from(s.learned.iter().map(String::as_str))?;
            let table = json_to_lua(
                lua,
                &json!({
                    "points": s.points,
                    "level": s.level,
                    "level_points": s.level_points,
                    "cooldowns": s.cooldowns.map(|c| json!({ "dash": c[0], "heal": c[1], "strike": c[2] })),
                    "bonus_damage": s.bonus_damage,
                    "speed_mult": s.speed_mult,
                    "max_hp_bonus": s.max_hp_bonus,
                    "crit_bonus": s.crit_bonus,
                }),
            )?;
            if let Value::Table(table) = &table {
                table.raw_set("learned", learned)?;
            }
            Ok(table)
        })?,
    )?;

    api.set(
        "waypoints",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let marked = lua.create_sequence_from(view.waypoints.marked.iter().map(String::as_str))?;
            let landmarks = lua.create_sequence_from(
                view.waypoints
                    .landmarks
                    .iter()
                    .map(|(name, label)| {
                        let t = lua.create_table()?;
                        t.raw_set("name", *name)?;
                        t.raw_set("biome", *label)?;
                        Ok(Value::Table(t))
                    })
                    .collect::<mlua::Result<Vec<Value>>>()?,
            )?;
            let table = lua.create_table()?;
            table.raw_set("marked", marked)?;
            if let Some(label) = &view.waypoints.label {
                table.raw_set("label", label.as_str())?;
            }
            if let Some(p) = view.waypoints.position {
                table.raw_set("x", p[0])?;
                table.raw_set("y", p[1])?;
                table.raw_set("z", p[2])?;
            }
            table.raw_set("landmarks", landmarks)?;
            Ok(Value::Table(table))
        })?,
    )?;

    api.set(
        "save_info",
        lua.create_function(|lua, ()| {
            let view = lua
                .app_data_ref::<DebugView>()
                .ok_or_else(|| mlua::Error::runtime("sem snapshot — só dentro de viber.lua"))?;
            let Some(s) = view.save.as_ref() else {
                return Ok(Value::Nil);
            };
            Ok(json_to_lua(
                lua,
                &json!({
                    "path": s.path, "exists": s.exists,
                    "bytes": s.bytes, "mtime": s.mtime,
                }),
            )?)
        })?,
    )?;

    // ── Escrita M2: controlo total (vitals, quests, vault, skills, IA…) ──
    api.set(
        "set_entity_hp",
        lua.create_function(|lua, (arg, hp): (EntityArg, f32)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::SetEntityHp(entity, hp))
        })?,
    )?;
    api.set(
        "set_max_hp",
        lua.create_function(|lua, (arg, max): (EntityArg, f32)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::SetMaxHp(entity, max))
        })?,
    )?;

    api.set(
        "quest_force",
        lua.create_function(|lua, (id, state): (String, String)| {
            push(lua, DebugOp::QuestForce(id, state))
        })?,
    )?;
    api.set(
        "quest_progress",
        lua.create_function(|lua, (id, n): (String, u32)| {
            push(lua, DebugOp::QuestProgress(id, n))
        })?,
    )?;

    api.set(
        "vault_set",
        lua.create_function(|lua, (what, n): (String, u32)| {
            push(lua, DebugOp::VaultSet(what, n))
        })?,
    )?;
    api.set(
        "take",
        lua.create_function(|lua, (what, n): (String, u32)| {
            push(lua, DebugOp::Take(what, n))
        })?,
    )?;

    api.set(
        "skill_learn",
        lua.create_function(|lua, id: String| push(lua, DebugOp::SkillLearn(id)))?,
    )?;
    api.set(
        "skill_points",
        lua.create_function(|lua, n: u32| push(lua, DebugOp::SkillPoints(n)))?,
    )?;
    api.set(
        "skill_reset",
        lua.create_function(|lua, ()| push(lua, DebugOp::SkillReset))?,
    )?;

    api.set(
        "ai_state",
        lua.create_function(|lua, (arg, state): (EntityArg, String)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::AiState(entity, state))
        })?,
    )?;
    api.set(
        "ai_aggro",
        lua.create_function(|lua, (arg, radius): (EntityArg, f32)| {
            let entity = resolve(lua, arg)?;
            push(lua, DebugOp::AiAggro(entity, radius))
        })?,
    )?;
    api.set(
        "ai_calm_all",
        lua.create_function(|lua, ()| push(lua, DebugOp::AiCalmAll))?,
    )?;

    api.set(
        "nav_set",
        lua.create_function(|lua, opts: NavSetOpts| {
            push(
                lua,
                DebugOp::NavSet {
                    enabled: opts.enabled,
                    offroad_cost: opts.offroad_cost,
                    tile_size: opts.tile_size,
                },
            )
        })?,
    )?;

    api.set(
        "postfx",
        lua.create_function(|lua, opts: PostFxOpts| {
            // Cada campo presente força o SEU gate; um op por efeito.
            const GATES: &[(&str, &str)] = &[
                ("autoexposure", "AUTOEXPOSURE"),
                ("bloom", "BLOOM"),
                ("dof", "DOF"),
                ("ssao", "SSAO"),
                ("contact_shadows", "CONTACT_SHADOWS"),
                ("aerial", "AERIAL"),
                ("splittone", "SPLITTONE"),
                ("vignette", "VIGNETTE"),
                ("chromatic", "CHROMATIC"),
                ("cas", "CAS"),
                ("motion_blur", "MOTION_BLUR"),
                ("taa", "TAA"),
                ("volumetrics", "VOLUMETRICS"),
            ];
            let values = [
                opts.autoexposure,
                opts.bloom,
                opts.dof,
                opts.ssao,
                opts.contact_shadows,
                opts.aerial,
                opts.splittone,
                opts.vignette,
                opts.chromatic,
                opts.cas,
                opts.motion_blur,
                opts.taa,
                opts.volumetrics,
            ];
            for ((_, key), on) in GATES.iter().zip(values) {
                if let Some(on) = on {
                    // &'static str das consts — safe para o op.
                    let key: &'static str = key;
                    push(lua, DebugOp::PostFx { key, on })?;
                }
            }
            Ok(())
        })?,
    )?;

    api.set(
        "audio_set",
        lua.create_function(|lua, opts: AudioSetOpts| {
            push(
                lua,
                DebugOp::AudioSet {
                    master: opts.master,
                    music: opts.music,
                    sfx: opts.sfx,
                },
            )
        })?,
    )?;

    api.set(
        "combat_music",
        lua.create_function(|lua, state: String| push(lua, DebugOp::CombatMusic(state)))?,
    )?;

    api.set(
        "physics_set",
        lua.create_function(|lua, opts: PhysicsSetOpts| {
            push(
                lua,
                DebugOp::PhysicsSet {
                    gravity: opts.gravity,
                    paused: opts.paused,
                },
            )
        })?,
    )?;

    api.set("save", lua.create_function(|lua, ()| push(lua, DebugOp::Save))?)?;
    api.set("load", lua.create_function(|lua, ()| push(lua, DebugOp::Load))?)?;

    api.set(
        "teleport_to",
        lua.create_function(|lua, name: String| push(lua, DebugOp::TeleportTo(name)))?,
    )?;

    api.set(
        "spawn",
        lua.create_function(
            |lua, (url, x, y, z, opts): (String, f32, f32, f32, SpawnOpts)| {
                let color = match opts.color {
                    Some(hex) => Some(
                        crate::xml::values::parse_color(&hex, "viber.debug.spawn")
                            .map_err(mlua::Error::runtime)?,
                    ),
                    None => None,
                };
                push(
                    lua,
                    DebugOp::Spawn {
                        url,
                        pos: Vec3::new(x, y, z),
                        yaw: opts.yaw,
                        scale: opts.scale,
                        color,
                        collider: opts.collider.unwrap_or(true),
                        snap: opts.snap.unwrap_or(true),
                    },
                )
            },
        )?,
    )?;

    api.set(
        "spawn_light",
        lua.create_function(|lua, (x, y, z, opts): (f32, f32, f32, SpawnLightOpts)| {
            let color = match opts.color {
                Some(hex) => Some(
                    crate::xml::values::parse_color(&hex, "viber.debug.spawn_light")
                        .map_err(mlua::Error::runtime)?,
                ),
                None => None,
            };
            push(
                lua,
                DebugOp::SpawnLight {
                    pos: Vec3::new(x, y, z),
                    intensity: opts.intensity,
                    color,
                    shadows: opts.shadows.unwrap_or(false),
                    range: opts.range,
                },
            )
        })?,
    )?;

    api.set(
        "set_material",
        lua.create_function(|lua, (arg, opts): (EntityArg, MaterialOpts)| {
            let entity = resolve(lua, arg)?;
            let base_color = match opts.base_color {
                Some(hex) => {
                    let c = crate::xml::values::parse_color(&hex, "viber.debug.set_material")
                        .map_err(mlua::Error::runtime)?;
                    Some([c[0], c[1], c[2], 1.0])
                }
                None => None,
            };
            let emissive = match opts.emissive {
                Some(hex) => Some(
                    crate::xml::values::parse_color(&hex, "viber.debug.set_material")
                        .map_err(mlua::Error::runtime)?,
                ),
                None => None,
            };
            push(
                lua,
                DebugOp::SetMaterial {
                    entity,
                    base_color,
                    metallic: opts.metallic,
                    roughness: opts.roughness,
                    unlit: opts.unlit,
                    emissive,
                },
            )
        })?,
    )?;

    api.set(
        "set_light",
        lua.create_function(|lua, (arg, opts): (EntityArg, LightOpts)| {
            let entity = resolve(lua, arg)?;
            let color = match opts.color {
                Some(hex) => Some(
                    crate::xml::values::parse_color(&hex, "viber.debug.set_light")
                        .map_err(mlua::Error::runtime)?,
                ),
                None => None,
            };
            push(
                lua,
                DebugOp::SetLight {
                    entity,
                    intensity: opts.intensity,
                    color,
                    shadows: opts.shadows,
                    range: opts.range,
                },
            )
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
            distance,
            pitch,
            yaw,
            ..
        } if distance.is_some_and(|d| !d.is_finite())
            || pitch.is_some_and(|p| !p.is_finite())
            || yaw.is_some_and(|y| !y.is_finite()) =>
        {
            Some("set_camera")
        }
        DebugOp::SetClock(minute) if !minute.is_finite() => Some("set_clock"),
        DebugOp::RainLook {
            near_fade,
            alpha,
            width,
            rate,
        } if [near_fade, alpha, width, rate]
            .into_iter()
            .any(|v| v.is_some_and(|value: f32| !value.is_finite())) =>
        {
            Some("rain_look")
        }
        DebugOp::SetWeather { rain, clouds, wind }
            if [rain, clouds, wind]
                .into_iter()
                .any(|v| v.is_some_and(|value: f32| !value.is_finite())) =>
        {
            Some("set_weather")
        }
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
        } if [
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
        ]
        .into_iter()
        .any(|v| v.is_some_and(|x| !x.is_finite())) =>
        {
            Some("ground")
        }
        DebugOp::SpawnMarker { pos, size, .. } if !finite3(*pos) || !finite3(*size) => {
            Some("spawn_marker")
        }
        DebugOp::SetEntityHp(_, hp) | DebugOp::SetMaxHp(_, hp) if !hp.is_finite() => {
            Some("set_entity_hp/set_max_hp")
        }
        DebugOp::QuestProgress(_, n) => {
            (*n > 1_000_000).then_some("quest_progress")
        }
        DebugOp::AiAggro(_, r) if !r.is_finite() => Some("ai_aggro"),
        DebugOp::NavSet {
            offroad_cost,
            tile_size,
            ..
        } if offroad_cost.is_some_and(|v| !v.is_finite())
            || tile_size.is_some_and(|v| !v.is_finite()) =>
        {
            Some("nav_set")
        }
        DebugOp::AudioSet {
            master,
            music,
            sfx,
        } if [master, music, sfx]
            .into_iter()
            .any(|v| v.is_some_and(|x| !x.is_finite())) =>
        {
            Some("audio_set")
        }
        DebugOp::PhysicsSet {
            gravity: Some(g), ..
        } if !finite3(g.0) => Some("physics_set"),
        DebugOp::Spawn {
            pos, yaw, scale, ..
        } if !finite3(*pos)
            || yaw.is_some_and(|v| !v.is_finite())
            || scale.is_some_and(|v| !v.is_finite()) =>
        {
            Some("spawn")
        }
        DebugOp::SpawnLight {
            pos,
            intensity,
            range,
            ..
        } if !finite3(*pos)
            || intensity.is_some_and(|v| !v.is_finite())
            || range.is_some_and(|v| !v.is_finite()) =>
        {
            Some("spawn_light")
        }
        DebugOp::SetMaterial {
            metallic,
            roughness,
            ..
        } if metallic.is_some_and(|v| !v.is_finite())
            || roughness.is_some_and(|v| !v.is_finite()) =>
        {
            Some("set_material")
        }
        DebugOp::SetLight {
            intensity, range, ..
        } if intensity.is_some_and(|v| !v.is_finite())
            || range.is_some_and(|v| !v.is_finite()) =>
        {
            Some("set_light")
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
                    e.get::<Name>()
                        .is_some_and(|n| n.as_str().starts_with("debug:"))
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
            yaw,
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
                if let Some(yaw) = yaw {
                    cam.yaw_deg = yaw;
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
        DebugOp::RainLook {
            near_fade,
            alpha,
            width,
            rate,
        } => match world.get_resource_mut::<crate::ambient::RainLook>() {
            Some(mut look) => {
                if let Some(near_fade) = near_fade {
                    look.near_fade_m = near_fade.max(0.0);
                }
                if let Some(alpha) = alpha {
                    look.max_alpha = alpha.clamp(0.0, 1.0);
                }
                if let Some(width) = width {
                    look.width_scale = width.max(0.01);
                }
                if let Some(rate) = rate {
                    look.rate_scale = rate.max(0.0);
                }
                true
            }
            None => {
                warnings.push("sem RainLook — rain_look ignorado".into());
                false
            }
        },
        DebugOp::SetWeather { rain, clouds, wind } => {
            match world.get_resource_mut::<crate::worldsys::WeatherState>() {
                Some(mut weather) => {
                    if let Some(rain) = rain {
                        weather.rain = rain.clamp(0.0, 1.0);
                        // Congela o ciclo: o `weather_drive` reescreve
                        // `rain` todos os frames a caminho do alvo do
                        // scheduler, e o braço de QA evaporava em 10 s.
                        weather.cycle = false;
                    }
                    if let Some(clouds) = clouds {
                        weather.clouds = clouds.clamp(0.0, 1.0);
                    }
                    if let Some(wind) = wind {
                        weather.wind_strength = wind.max(0.0);
                    }
                    true
                }
                None => {
                    warnings.push("sem WeatherState — set_weather ignorado".into());
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
            let (yaw_r, pitch_r) = (
                sun_yaw.to_radians(),
                sun_pitch.clamp(-89.0, 89.0).to_radians(),
            );
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
        DebugOp::SetEntityHp(entity, hp) => set_entity_health(world, entity, Some(hp), None, warnings),
        DebugOp::SetMaxHp(entity, max) => set_entity_health(world, entity, None, Some(max), warnings),
        DebugOp::QuestForce(id, state) => {
            let Some(mut log) = world.get_resource_mut::<crate::quests::QuestLog>() else {
                warnings.push("sem QuestLog — quest_force ignorado".into());
                return false;
            };
            let Some(def) = log.def(&id).cloned() else {
                warnings.push(format!("quest '{id}' não existe (quest_defs() lista)"));
                return false;
            };
            match state.as_str() {
                "active" => {
                    if !log.accept(&id) {
                        warnings.push(format!("quest '{id}': só se aceita de not_taken"));
                        return false;
                    }
                }
                "ready" => match def.objective.kind.as_str() {
                    "collect" => {
                        // O vault é a autoridade do collect — aceita e avisa
                        // que o READY vem do inventário.
                        log.accept(&id);
                        warnings.push(
                            "collect é vault-driven: ready = encher o vault                              (viber.debug.vault_set)"
                                .into(),
                        );
                    }
                    kind => {
                        let count = def.objective.count;
                        let mut active = crate::quests::ActiveQuest::default();
                        if kind == "visit" {
                            active.visited = def
                                .objective
                                .target
                                .split_whitespace()
                                .take(count as usize)
                                .map(crate::quests::normalize_target)
                                .collect();
                        } else {
                            active.progress = count;
                        }
                        log.accept(&id);
                        if let Some(entry) = log.states.get_mut(&id) {
                            *entry = active;
                        }
                    }
                },
                "done" => {
                    log.states.remove(&id);
                    if !log.done.iter().any(|d| d == &id) {
                        log.done.push(id.clone());
                    }
                }
                "not_taken" | "reset" => {
                    log.states.remove(&id);
                    log.done.retain(|d| d != &id);
                }
                other => {
                    warnings.push(format!(
                        "quest_force: estado '{other}' inválido (active|ready|done|not_taken)"
                    ));
                    return false;
                }
            }
            true
        }
        DebugOp::QuestProgress(id, n) => {
            let Some(mut log) = world.get_resource_mut::<crate::quests::QuestLog>() else {
                warnings.push("sem QuestLog — quest_progress ignorado".into());
                return false;
            };
            let Some(def) = log.def(&id).cloned() else {
                warnings.push(format!("quest '{id}' não existe"));
                return false;
            };
            let Some(active) = log.states.get_mut(&id) else {
                warnings.push(format!(
                    "quest '{id}' não está ativa (quest_force '{id}', 'active')"
                ));
                return false;
            };
            match def.objective.kind.as_str() {
                "kill" => active.progress = n,
                "visit" => {
                    active.visited = def
                        .objective
                        .target
                        .split_whitespace()
                        .take(n as usize)
                        .map(crate::quests::normalize_target)
                        .collect();
                }
                _ => {
                    warnings.push(
                        "collect é vault-driven: usa viber.debug.vault_set".into(),
                    );
                    return false;
                }
            }
            true
        }
        DebugOp::VaultSet(what, n) => match world.get_resource_mut::<crate::economy::Vault>() {
            Some(mut vault) => {
                match what.as_str() {
                    "gold" => vault.gold = n,
                    "wood" => vault.wood = n,
                    "stone" => vault.stone = n,
                    _ => {
                        vault.items.insert(what, n.min(99));
                    }
                }
                true
            }
            None => {
                warnings.push("vault indisponível — vault_set ignorado".into());
                false
            }
        },
        DebugOp::Take(what, n) => match world.get_resource_mut::<crate::economy::Vault>() {
            Some(mut vault) => {
                if vault.take(&what, n) {
                    true
                } else {
                    warnings.push(format!("take: '{what}'×{n} — stock insuficiente"));
                    false
                }
            }
            None => {
                warnings.push("vault indisponível — take ignorado".into());
                false
            }
        },
        DebugOp::SkillLearn(id) => {
            // Delta derivado da ÁRVORE (fonte da verdade) — o resource
            // PlayerStatsResource pode nem existir numa app mínima.
            let (old_stats, learned) = {
                let Some(mut tree) = world.get_resource_mut::<crate::skills::SkillTree>() else {
                    warnings.push("sem SkillTree — skill_learn ignorado".into());
                    return false;
                };
                let old_stats = crate::skills::stats_from_learned(&tree.learned);
                (old_stats, tree.learn(&id))
            };
            match learned {
                Some(new_stats) => {
                    apply_stats_delta(world, &old_stats, &new_stats, warnings);
                    if let Some(mut res) =
                        world.get_resource_mut::<crate::skills::PlayerStatsResource>()
                    {
                        res.0 = new_stats;
                    }
                    true
                }
                None => {
                    warnings.push(format!(
                        "skill '{id}': id desconhecido, sem pontos ou sem pré-requisitos"
                    ));
                    false
                }
            }
        }
        DebugOp::SkillPoints(n) => match world.get_resource_mut::<crate::skills::SkillTree>() {
            Some(mut tree) => {
                tree.points = n;
                true
            }
            None => {
                warnings.push("sem SkillTree — skill_points ignorado".into());
                false
            }
        },
        DebugOp::SkillReset => {
            let (old_stats, new_stats) = {
                let Some(mut tree) = world.get_resource_mut::<crate::skills::SkillTree>() else {
                    warnings.push("sem SkillTree — skill_reset ignorado".into());
                    return false;
                };
                let old_stats = crate::skills::stats_from_learned(&tree.learned);
                let forgotten = tree.learned.len() as u32;
                tree.learned.clear();
                tree.points += forgotten;
                (old_stats, crate::skills::PlayerStats::default())
            };
            apply_stats_delta(world, &old_stats, &new_stats, warnings);
            if let Some(mut res) = world.get_resource_mut::<crate::skills::PlayerStatsResource>() {
                res.0 = new_stats;
            }
            true
        }
        DebugOp::AiState(entity, state) => {
            let state = match state.as_str() {
                "wander" => crate::ai::EnemyState::Wander,
                "chase" => crate::ai::EnemyState::Chase,
                other => {
                    warnings.push(format!("ai_state: '{other}' inválido (wander|chase)"));
                    return false;
                }
            };
            let applied = with_entity(world, entity, warnings, |e| {
                e.get_mut::<crate::ai::EnemyCreature>()
                    .map(|mut fsm| fsm.state = state)
                    .is_some()
            })
            .unwrap_or(false);
            if !applied {
                warnings.push("ai_state: entidade sem EnemyCreature (FSM da engine)".into());
            }
            applied
        }
        DebugOp::AiAggro(entity, radius) => {
            with_entity(world, entity, warnings, |e| {
                e.get_mut::<crate::ai::EnemyCreature>()
                    .map(|mut fsm| fsm.aggro_radius = radius.max(0.0))
                    .is_some()
            })
            .unwrap_or_else(|| {
                warnings.push("ai_aggro: entidade sem EnemyCreature".into());
                false
            })
        }
        DebugOp::AiCalmAll => {
            let mut q = world.query::<&mut crate::ai::EnemyCreature>();
            let mut n = 0;
            for mut fsm in q.iter_mut(world) {
                fsm.state = crate::ai::EnemyState::Wander;
                n += 1;
            }
            warnings.push(format!("ai_calm_all: {n} criaturas em Wander"));
            true
        }
        DebugOp::NavSet {
            enabled,
            offroad_cost,
            tile_size,
        } => match world.get_resource_mut::<crate::nav::NavConfig>() {
            Some(mut config) => {
                if let Some(v) = enabled {
                    config.enabled = v;
                }
                if let Some(v) = offroad_cost {
                    config.offroad_cost = v.max(1.0);
                }
                if let Some(v) = tile_size {
                    config.tile_size = v.max(16.0);
                }
                true
            }
            None => {
                warnings.push("sem NavConfig (NavPlugin não está no mundo?)".into());
                false
            }
        },
        DebugOp::PostFx { key, on } => {
            crate::postfx::fx_runtime_toggle(key, on);
            true
        }
        DebugOp::AudioSet {
            master,
            music,
            sfx,
        } => match world.get_resource_mut::<crate::music::AudioMixerSettings>() {
            Some(mut mixer) => {
                if let Some(v) = master {
                    mixer.master = v.clamp(0.0, 1.0);
                }
                if let Some(v) = music {
                    mixer.music = v.clamp(0.0, 1.0);
                }
                if let Some(v) = sfx {
                    mixer.sfx = v.clamp(0.0, 1.0);
                }
                true
            }
            None => {
                warnings.push("sem AudioMixerSettings (MusicPlugin?)".into());
                false
            }
        },
        DebugOp::CombatMusic(state) => {
            let now = world.resource::<Time>().elapsed_secs_f64();
            match world.get_resource_mut::<crate::music::CombatMusicState>() {
                Some(mut music) => match state.as_str() {
                    "battle" => {
                        music.clear();
                        music.engage(now, false);
                        true
                    }
                    "boss" => {
                        music.clear();
                        music.engage(now, true);
                        true
                    }
                    "off" => {
                        music.clear();
                        true
                    }
                    other => {
                        warnings.push(format!(
                            "combat_music: '{other}' inválido (battle|boss|off)"
                        ));
                        false
                    }
                },
                None => {
                    warnings.push("sem CombatMusicState (MusicPlugin?)".into());
                    false
                }
            }
        }
        DebugOp::PhysicsSet { gravity, paused } => {
            let mut q = world.query::<&mut bevy_rapier3d::prelude::RapierConfiguration>();
            if let Some(mut conf) = q.iter_mut(world).next() {
                if let Some(g) = gravity {
                    conf.gravity = g.0;
                }
                if let Some(p) = paused {
                    conf.physics_pipeline_active = !p;
                }
                true
            } else {
                warnings.push("sem RapierConfiguration (PhysicsPlugin?)".into());
                false
            }
        }
        DebugOp::Save => queue_ui_action(world, "save", warnings),
        DebugOp::Load => queue_ui_action(world, "load", warnings),
        DebugOp::StepFrames(frames) => {
            // Semântica (afinada no smoke ao vivo): PÁRA e FICA parado — cada
            // `step(n)` avança n frames à speed 1 e volta a congelar; `play`
            // retoma a speed que estava ANTES da primeira chamada da cadeia.
            // (A 1.ª versão retomava a speed antiga no fim do orçamento: o
            // mundo voltava a correr e as medições/hash deixavam de ser
            // determinísticos a seguir ao step.)
            let current = world
                .get_resource::<crate::combat::BaseTimeScale>()
                .map(|b| b.0)
                .unwrap_or_else(|| world.resource::<Time<Virtual>>().relative_speed());
            // A speed a restaurar é a da PRIMEIRA chamada da cadeia — um
            // `step` a seguir a outro não a clobber (ficaria 0 e o `play`
            // não despausava).
            let restore = world
                .get_resource::<super::FrameStepper>()
                .map(|stepper| stepper.restore)
                .unwrap_or(current);
            world.insert_resource(super::FrameStepper {
                remaining: frames,
                restore,
                skip: true,
                active: true,
            });
            set_time_scale_value(world, 0.0);
            true
        }
        DebugOp::ResumePlay => {
            if let Some(stepper) = world.remove_resource::<super::FrameStepper>() {
                set_time_scale_value(world, stepper.restore);
            }
            true
        }
        DebugOp::TeleportTo(name) => {
            // Resolve por nome contra o MUNDO (não o snapshot — o alvo pode
            // ter nascido de um spawn recente).
            let needle = name.to_ascii_lowercase();
            let target = world
                .iter_entities()
                .filter_map(|e| {
                    let n = e.get::<Name>()?.to_string().to_ascii_lowercase();
                    (n == needle || n.contains(&needle)).then_some(e.id())
                })
                .next();
            let Some(target) = target else {
                warnings.push(format!("teleport_to: '{name}' não encontrado"));
                return false;
            };
            let pos = world
                .get::<Transform>(target)
                .map(|t| t.translation)
                .or_else(|| world.get::<GlobalTransform>(target).map(|t| t.translation()));
            let Some(mut pos) = pos else {
                warnings.push(format!("teleport_to: '{name}' sem posição"));
                return false;
            };
            if let Some(terrain) = world.get_resource::<crate::terrain::runtime::TerrainRuntime>()
            {
                pos.y = terrain.sample(pos.x, pos.z);
            }
            let Some(player) = find_player(world).map(|p| p.entity) else {
                warnings.push("sem player — teleport_to ignorado".into());
                return false;
            };
            set_translation(world, player, pos, warnings)
        }
        DebugOp::Spawn {
            url,
            mut pos,
            yaw,
            scale,
            color,
            collider,
            snap,
        } => {
            if snap
                && let Some(terrain) =
                    world.get_resource::<crate::terrain::runtime::TerrainRuntime>()
            {
                pos.y = terrain.sample(pos.x, pos.z);
            }
            let transform = Transform {
                translation: pos,
                rotation: Quat::from_rotation_y(yaw.unwrap_or(0.0).to_radians()),
                scale: Vec3::splat(scale.unwrap_or(1.0).max(0.001)),
            };
            static NEXT_SPAWN: std::sync::atomic::AtomicU64 =
                std::sync::atomic::AtomicU64::new(1);
            let n = NEXT_SPAWN.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let name = format!("debug:spawn:{n}");
            if let Some(shape) = parse_primitive(&url) {
                spawn_primitive(world, shape, name, transform, color, collider, warnings)
            } else {
                // GLB do pool: handle assíncrono — o `gltf_scene_spawner` da
                // engine troca pelo SceneRoot quando aterrar (igual ao XML).
                let Some(server) = world.get_resource::<bevy::asset::AssetServer>() else {
                    warnings.push("sem AssetServer — spawn GLB ignorado".into());
                    return false;
                };
                let handle =
                    crate::meshopt::load_gltf(&server, url.trim_start_matches('/').to_owned());
                world.spawn((
                    Name::new(name),
                    transform,
                    Visibility::default(),
                    crate::recipes::spawn::GltfScenePending { handle },
                ));
                true
            }
        }
        DebugOp::SpawnLight {
            pos,
            intensity,
            color,
            shadows,
            range,
        } => {
            static NEXT_LIGHT: std::sync::atomic::AtomicU64 =
                std::sync::atomic::AtomicU64::new(1);
            let n = NEXT_LIGHT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let light = PointLight {
                intensity: intensity.unwrap_or(1200.0).max(0.0),
                color: color
                    .map(|c| Color::srgb(c[0], c[1], c[2]))
                    .unwrap_or_default(),
                shadow_maps_enabled: shadows,
                range: range.unwrap_or(20.0).max(0.1),
                ..Default::default()
            };
            world.spawn((
                Name::new(format!("debug:light:{n}")),
                light,
                Transform::from_translation(pos),
                Visibility::default(),
            ));
            true
        }
        DebugOp::SetMaterial {
            entity,
            base_color,
            metallic,
            roughness,
            unlit,
            emissive,
        } => {
            let handle = with_entity(world, entity, warnings, |e| {
                e.get::<MeshMaterial3d<StandardMaterial>>()
                    .map(|m| m.0.clone())
            })
            .flatten();
            let Some(handle) = handle else {
                warnings.push(
                    "set_material: entidade sem StandardMaterial (os materiais BINDLESS do                      terreno não são mutáveis — só primitivas/GLB)"
                        .into(),
                );
                return false;
            };
            let Some(mut assets) = world.get_resource_mut::<bevy::asset::Assets<StandardMaterial>>()
            else {
                warnings.push("Assets<StandardMaterial> indisponível".into());
                return false;
            };
            let Some(mut mat) = assets.get_mut(&handle) else {
                warnings.push("material já não existe nos assets".into());
                return false;
            };
            if let Some(c) = base_color {
                mat.base_color = Color::srgba(c[0], c[1], c[2], c[3]);
            }
            if let Some(v) = metallic {
                mat.metallic = v.clamp(0.0, 1.0);
            }
            if let Some(v) = roughness {
                mat.perceptual_roughness = v.clamp(0.0, 1.0);
            }
            if let Some(v) = unlit {
                mat.unlit = v;
            }
            if let Some(e) = emissive {
                mat.emissive = bevy::color::LinearRgba::rgb(e[0], e[1], e[2]);
            }
            true
        }
        DebugOp::SetLight {
            entity,
            intensity,
            color,
            shadows,
            range,
        } => {
            let applied = with_entity(world, entity, warnings, |e| -> Result<(), ()> {
                if let Some(mut point) = e.get_mut::<PointLight>() {
                    if let Some(v) = intensity {
                        point.intensity = v.max(0.0);
                    }
                    if let Some(c) = color {
                        point.color = Color::srgb(c[0], c[1], c[2]);
                    }
                    if let Some(v) = shadows {
                        point.shadow_maps_enabled = v;
                    }
                    if let Some(v) = range {
                        point.range = v.max(0.1);
                    }
                    return Ok(());
                }
                if let Some(mut spot) = e.get_mut::<SpotLight>() {
                    if let Some(v) = intensity {
                        spot.intensity = v.max(0.0);
                    }
                    if let Some(c) = color {
                        spot.color = Color::srgb(c[0], c[1], c[2]);
                    }
                    if let Some(v) = shadows {
                        spot.shadow_maps_enabled = v;
                    }
                    if let Some(v) = range {
                        spot.range = v.max(0.1);
                    }
                    return Ok(());
                }
                if let Some(mut dir) = e.get_mut::<DirectionalLight>() {
                    // DirectionalLight não tem range; intensity = illuminance.
                    if let Some(v) = intensity {
                        dir.illuminance = v.max(0.0);
                    }
                    if let Some(c) = color {
                        dir.color = Color::srgb(c[0], c[1], c[2]);
                    }
                    if let Some(v) = shadows {
                        dir.shadow_maps_enabled = v;
                    }
                    return Ok(());
                }
                Err(())
            })
            .map(|result| result.is_ok())
            .unwrap_or(false);
            if !applied {
                warnings.push("set_light: entidade sem luz".into());
            }
            applied
        }
    }
}

/// Speed base do jogo (o mesmo par Time/BaseTimeScale do `set_time_scale`).
fn set_time_scale_value(world: &mut World, scale: f32) {
    if let Some(mut base) = world.get_resource_mut::<crate::combat::BaseTimeScale>() {
        base.0 = scale;
    }
    world.resource_mut::<Time<Virtual>>().set_relative_speed(scale);
}

/// `UiAction` para a engine (save/load passam pelo mesmo caminho da UI).
fn queue_ui_action(
    world: &mut World,
    name: &str,
    warnings: &mut Vec<String>,
) -> bool {
    match world.get_resource_mut::<Messages<crate::ui::actions::UiAction>>() {
        Some(mut msgs) => {
            msgs.write(crate::ui::actions::UiAction {
                name: name.to_string(),
                arg: String::new(),
            });
            true
        }
        None => {
            warnings.push("sem Messages<UiAction> (UIPlugin?)".into());
            false
        }
    }
}

/// HP/máximo de QUALQUER entidade com `Health` — um braço para os dois ops.
fn set_entity_health(
    world: &mut World,
    entity: Entity,
    hp: Option<f32>,
    max: Option<f32>,
    warnings: &mut Vec<String>,
) -> bool {
    let applied = with_entity(world, entity, warnings, |e| {
        e.get_mut::<Health>()
            .map(|mut health| {
                if let Some(max) = max {
                    health.max = max.max(1.0);
                }
                if let Some(hp) = hp {
                    health.current = hp.clamp(0.0, health.max);
                } else {
                    health.current = health.current.clamp(0.0, health.max);
                }
            })
            .is_some()
    })
    .unwrap_or(false);
    if !applied {
        warnings.push(format!("{entity}: sem Health"));
    }
    applied
}

/// Delta de passivas ao herói (aprender/esquecer) — o mesmo caminho da
/// compra na UI, para o speed/max_hp não ficarem órfãos do reset.
fn apply_stats_delta(
    world: &mut World,
    old: &crate::skills::PlayerStats,
    new: &crate::skills::PlayerStats,
    warnings: &mut Vec<String>,
) -> bool {
    let Some(player) = find_player(world).map(|p| p.entity) else {
        warnings.push("sem player — delta de skills ignorado".into());
        return false;
    };
    // `apply_passive_delta` pede &mut Health E &mut Player — um EntityWorldMut
    // não empresta os dois ao mesmo tempo; o delta espelha-se aqui por campo.
    let applied = with_entity(world, player, warnings, |e| {
        let hp_delta = new.max_hp_bonus - old.max_hp_bonus;
        if hp_delta != 0.0
            && let Some(mut health) = e.get_mut::<Health>()
        {
            health.max += hp_delta;
            health.current = (health.current + hp_delta).clamp(0.0, health.max);
        }
        let ratio = new.speed_mult / old.speed_mult.max(f32::EPSILON);
        if ratio != 1.0
            && ratio.is_finite()
            && let Some(mut player) = e.get_mut::<crate::player::Player>()
        {
            player.speed *= ratio;
        }
        true
    })
    .unwrap_or(false);
    if !applied {
        warnings.push("sem player — delta de skills ignorado".into());
    }
    applied
}

/// Primitivas do `viber.debug.spawn`: `"box:w,h,d"`, `"sphere:r"`,
/// `"cylinder:r,h"` — dimensões COMPLETAS (como no XML).
#[derive(Debug, Clone, Copy)]
enum PrimitiveShape {
    Box(Vec3),
    Sphere(f32),
    Cylinder(f32, f32),
}

fn parse_primitive(url: &str) -> Option<PrimitiveShape> {
    let (kind, dims) = url.split_once(':')?;
    let nums: Vec<f32> = dims
        .split(',')
        .map(|v| v.trim().parse::<f32>().ok())
        .collect::<Option<_>>()?;
    match kind.trim().to_ascii_lowercase().as_str() {
        "box" if nums.len() == 3 && nums.iter().all(|v| *v > 0.0) => {
            Some(PrimitiveShape::Box(Vec3::new(nums[0], nums[1], nums[2])))
        }
        "sphere" if nums.len() == 1 && nums[0] > 0.0 => Some(PrimitiveShape::Sphere(nums[0])),
        "cylinder" if nums.len() == 2 && nums.iter().all(|v| *v > 0.0) => {
            Some(PrimitiveShape::Cylinder(nums[0], nums[1]))
        }
        _ => None,
    }
}

#[allow(clippy::too_many_arguments)]
fn spawn_primitive(
    world: &mut World,
    shape: PrimitiveShape,
    name: String,
    transform: Transform,
    color: Option<[f32; 3]>,
    collider: bool,
    warnings: &mut Vec<String>,
) -> bool {
    use bevy::asset::Assets;
    use bevy::math::primitives::Cylinder;
    let Some(mut meshes) = world.get_resource_mut::<Assets<Mesh>>() else {
        warnings.push("Assets<Mesh> indisponível — spawn ignorado".into());
        return false;
    };
    let (mesh, rapier) = match shape {
        PrimitiveShape::Box(dims) => (
            Mesh::from(Cuboid::new(dims.x, dims.y, dims.z)),
            Collider::cuboid(dims.x * 0.5, dims.y * 0.5, dims.z * 0.5),
        ),
        PrimitiveShape::Sphere(radius) => {
            (Mesh::from(Sphere::new(radius)), Collider::ball(radius))
        }
        PrimitiveShape::Cylinder(radius, height) => (
            Mesh::from(Cylinder::new(radius, height)),
            Collider::cylinder(height * 0.5, radius),
        ),
    };
    let mesh = meshes.add(mesh);
    drop(meshes);
    let Some(mut materials) = world.get_resource_mut::<Assets<StandardMaterial>>() else {
        warnings.push("Assets<StandardMaterial> indisponível".into());
        return false;
    };
    let material = materials.add(StandardMaterial {
        base_color: color
            .map(|c| Color::srgb(c[0], c[1], c[2]))
            .unwrap_or(Color::srgb(0.65, 0.65, 0.7)),
        ..Default::default()
    });
    drop(materials);
    let mut entity = world.spawn((
        Name::new(name),
        Mesh3d(mesh),
        MeshMaterial3d(material),
        transform,
        Visibility::default(),
    ));
    if collider {
        entity.insert((rapier, RigidBody::Fixed));
    }
    true
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
    if let Some(light) = &info.light {
        table.raw_set("light", light.kind.as_str())?;
        if light.shadows {
            // Sombras de luz são o custo que mais interessa ver de relance.
            table.raw_set("light_shadows", true)?;
        }
    }
    if let Some(script) = &info.script {
        table.raw_set("script", script.as_str())?;
    }
    if let Some((current, max)) = info.health {
        table.raw_set("hp", current)?;
        table.raw_set("max_hp", max)?;
    }
    if let Some(ai) = &info.ai {
        table.raw_set(
            "ai",
            json_to_lua(
                lua,
                &json!({
                    "state": ai.state,
                    "speed": ai.speed,
                    "aggro_radius": ai.aggro_radius,
                    "attack_radius": ai.attack_radius,
                    "home": ai.home,
                    "goal": ai.goal,
                    "nav_profile": ai.nav_profile,
                }),
            )?,
        )?;
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
