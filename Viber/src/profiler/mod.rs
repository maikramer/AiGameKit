//! Profiler da engine — o análogo nativo do `?profiler=1` do VibeGame, com
//! os mesmos tabs: **Sistemas** (timings por sistema + grupos + contadores),
//! **Mundo** (player/câmara/entidades próximas), **Física** (Rapier),
//! **Áudio** (buses/sinks) e **Extras** (toggles de debug da engine).
//!
//! Superfície:
//! * janela declarativa (XML+CSS+Luau) com o modal **P** — o driver é o
//!   `ui/profiler.lua` do mundo (ex.: `examples/simple-rpg`), alimentado por
//!   `viber.profiler()` / `viber.profiler_cmd()` (`script.rs`);
//! * overlay mínimo **F3** (fps/frame/entidades);
//! * debug bridge: `viber.profiler` (compat), `viber.profiler.tab`
//!   (`{"tab": "systems|world|physics|audio|extras|all"}`) e
//!   `viber.profiler.export` (JSON para ficheiro) — cliente `viber debug
//!   prof --tab/--export`;
//! * teclas com a janela aberta: **F5** muda de aba, **F12/Pause** congela
//!   (aneis estáveis — o *Pause* do VibeGame), **Backquote** exporta,
//!   **PageUp/PageDown** muda o raio de "próximas".
//!
//! Timings por sistema: o Bevy 0.19 não expõe tempos por sistema, por isso
//! os plugins embrulham os sistemas pesados com [`timed::timed`] (ver
//! `timed.rs`). `VIBER_PROF_LOG=1` liga o `LogDiagnosticsPlugin` nativo.

pub mod audio_tab;
pub mod physics_tab;
pub mod script;
pub mod timed;
pub mod world_tab;

use std::collections::VecDeque;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use bevy::diagnostic::{
    Diagnostic, DiagnosticsStore, EntityCountDiagnosticsPlugin, FrameTimeDiagnosticsPlugin,
    LogDiagnosticsPlugin,
};
use bevy::prelude::*;
use serde_json::json;

use crate::luau::{LuaScriptRef, ScriptActivation};
use crate::particles::ParticleEmitter;
use crate::player::Player;
use crate::terrain::plugin::TerrainChunk;

pub use timed::{Group, TimingStats, timed};

/// Frames guardados na janela deslizante (~3 s a 60 fps).
const FRAME_WINDOW: usize = 180;
/// Refrescamento do texto do overlay (s) — evitar reescrever texto a 60 fps.
const OVERLAY_REFRESH: f32 = 0.25;

/// Abas do profiler, por ordem — ids canónicos EN (o tab-group do XML, o
/// `--tab` do CLI e `viber.ui.tab("prof")` falam nestes ids; os rótulos
/// visíveis são PT no XML).
pub const TABS: [&str; 5] = ["systems", "world", "physics", "audio", "extras"];
pub const TAB_SYSTEMS: usize = 0;
pub const TAB_WORLD: usize = 1;
pub const TAB_PHYSICS: usize = 2;
pub const TAB_AUDIO: usize = 3;
pub const TAB_EXTRAS: usize = 4;

/// Marker da raiz do overlay (toggle de visibilidade).
#[derive(Component)]
struct ProfilerOverlay;

/// Marker do nó de texto (corpo reescrito a cada refrescamento).
#[derive(Component)]
struct ProfilerText;

/// Estado do overlay mínimo: refresh throttle + visibilidade (**F3**).
#[derive(Resource)]
struct HudState {
    timer: f32,
    visible: bool,
}

impl Default for HudState {
    fn default() -> Self {
        Self {
            timer: 0.0,
            visible: false,
        }
    }
}

/// Estado partilhado do profiler: aba activa da janela, congelação, raio de
/// "entidades próximas" e última mensagem de estado (caminho de export, etc.).
#[derive(Resource, Clone)]
pub struct ProfilerState {
    pub tab: usize,
    pub frozen: bool,
    pub nearby_radius: f32,
    pub status: String,
}

impl Default for ProfilerState {
    fn default() -> Self {
        Self {
            tab: TAB_SYSTEMS,
            frozen: false,
            nearby_radius: world_tab::DEFAULT_NEARBY_RADIUS,
            status: String::new(),
        }
    }
}

/// Contador global de frames (o `frameCount` do snapshot do VibeGame).
#[derive(Resource, Default)]
pub struct FrameCounter(pub u64);

/// Janela deslizante de frame-times (ms) — média/pico/min-fps recentes,
/// independentes do histórico exponencial do `DiagnosticsStore`.
#[derive(Resource, Default)]
pub struct FrameStats {
    samples: VecDeque<f32>,
}

impl FrameStats {
    /// Registra um frame-time em ms, evictando fora da janela.
    pub fn push(&mut self, frame_ms: f32) {
        if self.samples.len() == FRAME_WINDOW {
            self.samples.pop_front();
        }
        self.samples.push_back(frame_ms);
    }

    fn avg_ms(&self) -> Option<f32> {
        if self.samples.is_empty() {
            return None;
        }
        let n = self.samples.len();
        Some(self.samples.iter().sum::<f32>() / n as f32)
    }

    fn max_ms(&self) -> Option<f32> {
        self.samples.iter().fold(None, |acc: Option<f32>, &v| {
            Some(acc.map_or(v, |m| m.max(v)))
        })
    }

    fn min_ms(&self) -> Option<f32> {
        self.samples.iter().fold(None, |acc: Option<f32>, &v| {
            Some(acc.map_or(v, |m| m.min(v)))
        })
    }

    /// P95 discreto sobre a janela (o `frameP95Ms` do VibeGame).
    fn p95_ms(&self) -> Option<f32> {
        if self.samples.is_empty() {
            return None;
        }
        let mut sorted: Vec<f32> = self.samples.iter().copied().collect();
        sorted.sort_by(f32::total_cmp);
        // p95 discreto do VibeGame: ceil(n·0.95)-1.
        let idx = (sorted.len() * 95).div_ceil(100) - 1;
        Some(sorted[idx])
    }

    /// Pior fps da janela (`1000/max_ms`); `None` sem amostras.
    pub fn min_fps(&self) -> Option<f32> {
        self.max_ms()
            .map(|ms| if ms > 0.0 { 1000.0 / ms } else { 0.0 })
    }

    /// (avg, min, max, p95) da janela.
    pub fn window(&self) -> (Option<f32>, Option<f32>, Option<f32>, Option<f32>) {
        (self.avg_ms(), self.min_ms(), self.max_ms(), self.p95_ms())
    }

    pub fn len(&self) -> usize {
        self.samples.len()
    }

    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }
}

/// Contadores de jogo para overlay/snapshot — recolhidos das queries.
#[derive(Debug, Clone, Copy, Default)]
pub struct GameCounters {
    pub entities: usize,
    pub scripts_total: usize,
    /// Scripts cujo `ScriptActivation` inclui o player (ou sem raio: sempre
    /// activos). É o "LOD de IA" — o resto está congelado.
    pub scripts_active: usize,
    pub particle_emitters: usize,
    pub terrain_chunks: usize,
    pub colliders: usize,
    /// PointLights com cube shadow map ACTIVO — cada uma são 6 vistas de
    /// render. É o contador que explica um frame que dobra ao entrar na vila.
    pub shadow_lights: usize,
    pub audio_sinks: usize,
}

/// Uma linha por script para [`count_active_scripts`]: posição global e raio
/// de activação (`None` = sem componente).
pub type ScriptRow = (Option<Vec3>, Option<f32>);

/// Conta scripts activos: entidade com `ScriptActivation` conta quando o
/// player está dentro do raio; sem componente, conta sempre (POIs/interações
/// baratas). Sem player no mundo, nada com raio está activo.
pub fn count_active_scripts(player_pos: Option<Vec3>, scripts: &[ScriptRow]) -> usize {
    scripts
        .iter()
        .filter(|&&(pos, radius)| match (radius, player_pos) {
            (None, _) => true,
            (Some(_), None) => false,
            (Some(r), Some(p)) => pos.is_some_and(|s| s.distance_squared(p) <= r * r),
        })
        .count()
}

/// Recolhe os contadores de um mundo real (queries por archetype, barato).
pub fn collect_counters(world: &mut World) -> GameCounters {
    let entities = world.entities().count_spawned() as usize;

    let player_pos: Option<Vec3> = {
        let mut q = world.query_filtered::<&GlobalTransform, With<Player>>();
        q.iter(world).next().map(GlobalTransform::translation)
    };

    let mut scripts_total = 0usize;
    let mut script_rows: Vec<ScriptRow> = Vec::new();
    let mut q_scripts = world.query_filtered::<
        (Option<&GlobalTransform>, Option<&ScriptActivation>),
        With<LuaScriptRef>,
    >();
    for (transform, activation) in q_scripts.iter(world) {
        scripts_total += 1;
        script_rows.push((
            transform.map(GlobalTransform::translation),
            activation.map(|a| a.radius),
        ));
    }

    let mut q_emitters = world.query_filtered::<(), With<ParticleEmitter>>();
    let particle_emitters = q_emitters.iter(world).count();

    let mut q_chunks = world.query_filtered::<(), With<TerrainChunk>>();
    let terrain_chunks = q_chunks.iter(world).count();

    let mut q_colliders = world.query_filtered::<(), With<bevy_rapier3d::prelude::Collider>>();
    let colliders = q_colliders.iter(world).count();

    let mut q_sinks =
        world.query_filtered::<(), Or<(With<AudioSink>, With<bevy::audio::SpatialAudioSink>)>>();
    let audio_sinks = q_sinks.iter(world).count();

    let mut q_shadow_lights = world.query::<&PointLight>();
    let shadow_lights = q_shadow_lights
        .iter(world)
        .filter(|light| light.shadow_maps_enabled)
        .count();

    GameCounters {
        entities,
        scripts_total,
        scripts_active: count_active_scripts(player_pos, &script_rows),
        particle_emitters,
        terrain_chunks,
        colliders,
        shadow_lights,
        audio_sinks,
    }
}

/// FPS/frame-time suavizados do `DiagnosticsStore` (`None` sem o plugin).
fn diagnostics_snapshot(store: &DiagnosticsStore) -> (Option<f64>, Option<f64>) {
    (
        store
            .get(&FrameTimeDiagnosticsPlugin::FPS)
            .and_then(Diagnostic::smoothed),
        store
            .get(&FrameTimeDiagnosticsPlugin::FRAME_TIME)
            .and_then(Diagnostic::average),
    )
}

/// Linhas das listas `systems`/`scripts_timed` do snapshot.
///
/// O corte de 30 esconde precisamente os sistemas baratos que uma
/// investigação quer CONFIRMAR baratos (fila aberta do `docs/PERFORMANCE.md`).
/// `VIBER_PROF_SYSTEMS=<n>` sobe o corte, `=all` tira-o.
const DEFAULT_SYSTEMS_LIMIT: usize = 30;

fn systems_limit() -> usize {
    match std::env::var("VIBER_PROF_SYSTEMS").as_deref() {
        Ok("all") | Ok("todos") | Ok("0") => usize::MAX,
        Ok(raw) => raw.parse().unwrap_or(DEFAULT_SYSTEMS_LIMIT),
        Err(_) => DEFAULT_SYSTEMS_LIMIT,
    }
}

/// Spans do render graph medidos pelo `RenderDiagnosticsPlugin`
/// (`VIBER_PROF_GPU=1`, ver `gpu_profiling_enabled` no `main.rs`).
///
/// Os paths chegam como `render/<span>/…/elapsed_cpu|elapsed_gpu`: o prefixo
/// sai, o sufixo vira campo e o meio é o nome do span (`main_opaque_pass_3d`,
/// `shadows`, `bloom`, …). Sem o gate a lista vem VAZIA — é o sinal de que a
/// medição do render não está ligada, não de que os passes são de graça.
/// Ordenada pelo maior de (gpu, cpu): o passe que domina fica na 1.ª linha.
/// `render/<span>/…/elapsed_gpu` → `("<span>/…", "elapsed_gpu")`; qualquer
/// outro path devolve `None` (o store tem também fps, frame_time, contagens).
fn split_render_path(path: &str) -> Option<(&str, &str)> {
    path.strip_prefix("render/")?.rsplit_once('/')
}

fn gpu_spans(store: &DiagnosticsStore) -> Vec<serde_json::Value> {
    use std::collections::HashMap;

    let mut rows: HashMap<String, (Option<f64>, Option<f64>)> = HashMap::new();
    for diagnostic in store.iter() {
        let Some((name, field)) = split_render_path(diagnostic.path().as_str()) else {
            continue;
        };
        // `smoothed` (EMA) e não `value`: o timestamp de UM frame salta com o
        // boost da GPU — o mesmo motivo por que se cita `frame_ms_avg`.
        let value = diagnostic.smoothed();
        let entry = rows.entry(name.to_string()).or_default();
        match field {
            "elapsed_cpu" => entry.0 = value,
            "elapsed_gpu" => entry.1 = value,
            _ => {}
        }
    }
    let mut spans: Vec<(String, Option<f64>, Option<f64>)> = rows
        .into_iter()
        .filter(|(_, (cpu, gpu))| cpu.is_some() || gpu.is_some())
        .map(|(name, (cpu, gpu))| (name, cpu, gpu))
        .collect();
    let weight = |cpu: Option<f64>, gpu: Option<f64>| cpu.unwrap_or(0.0).max(gpu.unwrap_or(0.0));
    spans.sort_by(|a, b| weight(b.1, b.2).total_cmp(&weight(a.1, a.2)));
    spans
        .into_iter()
        .map(|(name, cpu, gpu)| json!({ "name": name, "cpu_ms": cpu, "gpu_ms": gpu }))
        .collect()
}

/// Snapshot JSON do profiler — o corpo do método `viber.profiler` (compatível
/// com o formato anterior; campos novos só se acrescentam).
pub fn snapshot(world: &mut World) -> serde_json::Value {
    let counters = collect_counters(world);
    let (fps, frame_ms_avg, gpu) = match world.get_resource::<DiagnosticsStore>() {
        Some(store) => {
            let (fps, frame_ms_avg) = diagnostics_snapshot(store);
            (fps, frame_ms_avg, gpu_spans(store))
        }
        None => (None, None, Vec::new()),
    };
    let (avg, min, max, p95) = world
        .get_resource::<FrameStats>()
        .map(FrameStats::window)
        .unwrap_or((None, None, None, None));
    let min_fps = world
        .get_resource::<FrameStats>()
        .and_then(FrameStats::min_fps);
    let window_frames = world
        .get_resource::<FrameStats>()
        .map(|s| s.len())
        .unwrap_or(0);
    let uptime = world
        .get_resource::<Time>()
        .map(Time::elapsed_secs)
        .unwrap_or(0.0);
    let lod = world
        .get_resource::<crate::render_lod::MeshLodStats>()
        .copied()
        .unwrap_or_default();
    let frames = world
        .get_resource::<FrameCounter>()
        .map(|f| f.0)
        .unwrap_or(0);
    let frozen = world
        .get_resource::<ProfilerState>()
        .map(|s| s.frozen)
        .unwrap_or(false);

    let frame_avg_f32 = avg.unwrap_or(0.0);
    let limit = systems_limit();
    let systems: Vec<serde_json::Value> = timed::systems_snapshot(frame_avg_f32)
        .into_iter()
        .take(limit)
        .map(|s| serde_json::to_value(s).unwrap_or(json!({})))
        .collect();
    let scripts_timed: Vec<serde_json::Value> = timed::scripts_snapshot(frame_avg_f32)
        .into_iter()
        .take(limit)
        .map(|s| serde_json::to_value(s).unwrap_or(json!({})))
        .collect();
    let groups: Vec<serde_json::Value> = timed::groups_snapshot(frame_avg_f32)
        .into_iter()
        .map(|(group, stats)| {
            let mut v = serde_json::to_value(stats).unwrap_or(json!({}));
            v["group"] = json!(group.as_str());
            v
        })
        .collect();

    json!({
        "fps": fps,
        "frame_ms_avg": frame_ms_avg,
        "frame_ms": { "avg": avg, "max": max, "min": min, "p95": p95 },
        "min_fps_window": min_fps,
        "window_frames": window_frames,
        "frame_count": frames,
        "frozen": frozen,
        "groups": groups,
        // Passes do render graph (CPU+GPU). Vazio sem `VIBER_PROF_GPU=1`.
        "gpu": gpu,
        "systems": systems,
        "scripts_timed": scripts_timed,
        "entities": counters.entities,
        "scripts": {
            "total": counters.scripts_total,
            "active": counters.scripts_active,
        },
        "particle_emitters": counters.particle_emitters,
        "terrain_chunks": counters.terrain_chunks,
        "colliders": counters.colliders,
        "shadow_lights": counters.shadow_lights,
        "audio_sinks": counters.audio_sinks,
        // LOD de render: barato (um recurso), ao contrário do `stats()` do
        // bridge — este é o caminho a amostrar em ciclo.
        "render_lod": {
            "swaps_last_frame": lod.swaps_last_frame,
            "pending": lod.pending,
        },
        "uptime_s": uptime,
    })
}

/// Snapshot rico de um tab (`viber.profiler.tab`).
pub fn tab_snapshot(world: &mut World, tab: &str) -> serde_json::Value {
    let frame_avg_ms = world
        .get_resource::<FrameStats>()
        .and_then(|s| s.avg_ms())
        .unwrap_or(0.0);
    let frames = world
        .get_resource::<FrameCounter>()
        .map(|f| f.0)
        .unwrap_or(0);
    let radius = world
        .get_resource::<ProfilerState>()
        .map(|s| s.nearby_radius)
        .unwrap_or(world_tab::DEFAULT_NEARBY_RADIUS);
    match tab {
        "world" => {
            let snap = world_tab::snapshot(world, radius, frames);
            world_tab::json(&snap)
        }
        "physics" => physics_tab::json(&physics_tab::snapshot(world, frame_avg_ms)),
        "audio" => audio_tab::json(&audio_tab::snapshot(world)),
        _ => snapshot(world),
    }
}

/// JSON completo para exportação: todos os tabs + metadados.
pub fn export_snapshot(world: &mut World) -> serde_json::Value {
    let mut all = serde_json::Map::new();
    for tab in ["systems", "world", "physics", "audio"] {
        all.insert(tab.to_string(), tab_snapshot(world, tab));
    }
    let extras: Vec<serde_json::Value> = extras_snapshot(world);
    json!({
        "tool": "viber",
        "kind": "profiler-export",
        "exported_at": SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0),
        "uptime_s": world.get_resource::<Time>().map(Time::elapsed_secs).unwrap_or(0.0),
        "tabs": all,
        "extras": extras,
    })
}

/// O JSON COMPLETO do profiler — o mesmo payload do botão COPIAR, do
/// ficheiro de export, do publish para o driver Luau e do `viber.profiler.tab
/// {"tab":"all"}` da bridge. Um só construtor para os quatro caminhos nunca
/// divergirem.
pub fn full_snapshot(world: &mut World) -> serde_json::Value {
    let mut value = export_snapshot(world);
    let state = world.resource::<ProfilerState>().clone();
    value["state"] = json!({
        "tab": state.tab,
        "tab_name": TABS.get(state.tab).copied().unwrap_or("systems"),
        "frozen": state.frozen,
        "open": world
            .get_resource::<crate::ui::modal::UiModalsOpen>()
            .map(|m| m.open.iter().any(|id| id == "profiler-win"))
            .unwrap_or(false),
        "nearby_radius": state.nearby_radius,
        "status": state.status,
    });
    value
}

/// Copia o [`full_snapshot`] para o clipboard (o "Copy JSON" do VibeGame).
/// Devolve o número de bytes colocados ou o motivo da falha (sem display,
/// Wayland/X11 indisponível, …).
pub fn copy_snapshot_to_clipboard(world: &mut World) -> Result<usize, String> {
    let text = serde_json::to_string_pretty(&full_snapshot(world))
        .map_err(|error| format!("serialize: {error}"))?;
    // A instância fica em cache: criar um Clipboard por cópia reabre a
    // ligação X11/Wayland de cada vez.
    static CLIPBOARD: std::sync::Mutex<Option<arboard::Clipboard>> = std::sync::Mutex::new(None);
    let mut guard = CLIPBOARD
        .lock()
        .map_err(|_| "clipboard poisoned".to_string())?;
    let clipboard = match guard.as_mut() {
        Some(clipboard) => clipboard,
        None => {
            let fresh = arboard::Clipboard::new()
                .map_err(|error| format!("clipboard indisponível: {error}"))?;
            guard.insert(fresh)
        }
    };
    clipboard
        .set_text(text.as_str())
        .map_err(|error| format!("clipboard set: {error}"))?;
    Ok(text.len())
}

/// Exporta o snapshot para ficheiro e devolve o caminho. `None` →
/// directório por omissão (`$TMPDIR/viber-profiles/`, a mesma convenção das
/// capturas da bridge).
pub fn export_to_file(world: &mut World, path: Option<PathBuf>) -> std::io::Result<PathBuf> {
    let path = path.unwrap_or_else(|| {
        let dir = std::env::temp_dir().join("viber-profiles");
        let secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        std::fs::create_dir_all(&dir).ok();
        dir.join(format!("viber-profile-{secs}.json"))
    });
    let value = full_snapshot(world);
    let text = serde_json::to_string_pretty(&value).unwrap_or_else(|_| "{}".into());
    std::fs::write(&path, text)?;
    Ok(path)
}

// ---------------------------------------------------------------- extras

/// Um toggle de debug da engine listado no tab **Extras** — o mecanismo dos
/// `registerProfilerExtra` do VibeGame (aqui são função ponteiro `&World`).
#[derive(Clone)]
pub struct ProfilerExtra {
    pub id: &'static str,
    pub label: &'static str,
    pub description: &'static str,
    pub is_on: fn(&mut World) -> bool,
    pub toggle: fn(&mut World),
}

impl std::fmt::Debug for ProfilerExtra {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProfilerExtra")
            .field("id", &self.id)
            .finish()
    }
}

/// Registos de extras (populado no build do plugin; mundos podem acrescentar).
#[derive(Resource, Default, Clone)]
pub struct ProfilerExtras {
    pub items: Vec<ProfilerExtra>,
}

/// Alterna um extra por id; devolve o novo estado (`None` = id desconhecido).
pub fn toggle_extra(world: &mut World, id: &str) -> Option<bool> {
    let items = world.get_resource::<ProfilerExtras>()?;
    let extra = items.items.iter().find(|e| e.id == id)?.clone();
    (extra.toggle)(world);
    let items = world.get_resource::<ProfilerExtras>()?;
    let extra = items.items.iter().find(|e| e.id == id)?.clone();
    Some((extra.is_on)(world))
}

/// Estado actual dos extras para janela/bridge.
pub fn extras_snapshot(world: &mut World) -> Vec<serde_json::Value> {
    let items = world
        .get_resource::<ProfilerExtras>()
        .map(|extras| extras.items.clone())
        .unwrap_or_default();
    items
        .iter()
        .map(|extra| {
            json!({
                "id": extra.id,
                "label": extra.label,
                "description": extra.description,
                "on": (extra.is_on)(world),
            })
        })
        .collect()
}

// ---------------------------------------------------------------- toggles

fn colliders_on(world: &mut World) -> bool {
    world
        .get_resource::<bevy_rapier3d::render::DebugRenderContext>()
        .map(|ctx| ctx.enabled)
        .unwrap_or(false)
}

fn toggle_colliders(world: &mut World) {
    if let Some(mut ctx) = world.get_resource_mut::<bevy_rapier3d::render::DebugRenderContext>() {
        ctx.enabled = !ctx.enabled;
    }
}

fn grass_on(world: &mut World) -> bool {
    world
        .get_resource::<crate::grass::GrassSettings>()
        .map(|g| g.enabled)
        .unwrap_or(false)
}

fn toggle_grass(world: &mut World) {
    if let Some(mut g) = world.get_resource_mut::<crate::grass::GrassSettings>() {
        g.enabled = !g.enabled;
    }
}

/// Sombras do sol: o estado do primeiro `DirectionalLight` do mundo é o
/// estado de todos (o toggle escreve-os todos).
fn dir_shadows_on(world: &mut World) -> bool {
    let mut query = world.query::<&DirectionalLight>();
    query
        .iter(world)
        .next()
        .map(|light| light.shadow_maps_enabled)
        .unwrap_or(false)
}

fn toggle_dir_shadows(world: &mut World) {
    let want = !dir_shadows_on(world);
    let mut query = world.query::<&mut DirectionalLight>();
    for mut light in query.iter_mut(world) {
        light.shadow_maps_enabled = want;
    }
}

/// Sombras das lanternas: o flag vive num recurso porque o
/// `light_budget_system` reescreve `shadow_maps_enabled` a cada segundo —
/// mexer nas luzes directamente durava um refresh.
fn point_shadows_on(world: &mut World) -> bool {
    world
        .get_resource::<crate::ambient::PointShadowsEnabled>()
        .map(|state| state.0)
        .unwrap_or(false)
}

fn toggle_point_shadows(world: &mut World) {
    if let Some(mut state) = world.get_resource_mut::<crate::ambient::PointShadowsEnabled>() {
        state.0 = !state.0;
    }
}

fn physics_paused_on(world: &mut World) -> bool {
    // "on" = simulação PAUSADA (o toggle pára o `PhysicsSet::StepSimulation`).
    let mut q = world.query::<&bevy_rapier3d::prelude::RapierConfiguration>();
    q.iter(world)
        .next()
        .map(|conf| !conf.physics_pipeline_active)
        .unwrap_or(false)
}

fn toggle_physics_paused(world: &mut World) {
    let mut q = world.query::<&mut bevy_rapier3d::prelude::RapierConfiguration>();
    if let Some(mut conf) = q.iter_mut(world).next() {
        conf.physics_pipeline_active = !conf.physics_pipeline_active;
    }
}

// ---------------------------------------------------------------- plugin

/// Plugin do profiler: diagnósticos canónicos + janela deslizante + overlay +
/// âncoras do step de física + extras.
pub struct ProfilerPlugin;

impl Plugin for ProfilerPlugin {
    /// As âncoras do render app instalam-se no `finish`: o sub-app já existe
    /// (o `RenderPlugin` criou-o) e a ordem dos plugins do mundo deixa de
    /// importar.
    fn finish(&self, app: &mut App) {
        timed::render_phase_anchors(app);
    }

    fn build(&self, app: &mut App) {
        app.add_plugins((
            FrameTimeDiagnosticsPlugin::default(),
            EntityCountDiagnosticsPlugin {
                max_history_length: 120,
            },
        ));
        if std::env::var_os("VIBER_PROF_LOG").is_some() {
            app.add_plugins(LogDiagnosticsPlugin::default());
        }
        // Âncoras antes/depois do `PhysicsSet::StepSimulation` — o schedule
        // por omissão do RapierPhysicsPlugin no bevy_rapier 0.36.
        timed::physics_anchors(app, bevy::app::PostUpdate);
        let extras = ProfilerExtras {
            items: vec![
                ProfilerExtra {
                    id: "colliders",
                    label: "colisores",
                    description: "wireframe dos colliders (bevy_rapier debug render)",
                    is_on: colliders_on,
                    toggle: toggle_colliders,
                },
                ProfilerExtra {
                    id: "grass",
                    label: "relva",
                    description: "campo de relva instanciado (GrassSettings.enabled)",
                    is_on: grass_on,
                    toggle: toggle_grass,
                },
                ProfilerExtra {
                    id: "dir-shadows",
                    label: "sombras do sol",
                    description: "shadow maps da DirectionalLight (4 cascatas) — A/B do custo por vista",
                    is_on: dir_shadows_on,
                    toggle: toggle_dir_shadows,
                },
                ProfilerExtra {
                    id: "point-shadows",
                    label: "sombras das lanternas",
                    description: "cube shadow maps das PointLight (6 vistas por luz)",
                    is_on: point_shadows_on,
                    toggle: toggle_point_shadows,
                },
                ProfilerExtra {
                    id: "physics-pause",
                    label: "pausar física",
                    description: "congela o solver Rapier (physics_pipeline_active=false)",
                    is_on: physics_paused_on,
                    toggle: toggle_physics_paused,
                },
            ],
        };
        // Fases do próprio Bevy (transform/visibilidade/luzes) — sem elas o
        // profiler mostra 4 ms de um frame de 48 e o resto é um buraco.
        timed::engine_phase_anchors(app);
        // Linha do tempo por schedule + o buraco entre frames (render/present).
        timed::frame_timeline(app);
        app.init_resource::<FrameStats>()
            .init_resource::<FrameCounter>()
            .init_resource::<ProfilerState>()
            .init_resource::<script::ProfilerScriptState>()
            .init_resource::<script::PublishThrottle>()
            .insert_resource(extras)
            .init_resource::<HudState>()
            .add_systems(Startup, spawn_overlay)
            .add_systems(First, (count_frames, sync_frozen_state).chain())
            // Superfície Luau (padrão viber.ui): instalar antes dos scripts
            // activarem, publicar a view antes de `on_update`, aplicar
            // comandos depois. As teclas F5/F12/`/PageUp são engine-side.
            .add_systems(
                Update,
                script::install_profiler_script_api
                    .before(crate::luau::luau_on_add)
                    .before(crate::luau::luau_update),
            )
            .add_systems(
                Update,
                script::publish_profiler_view.before(crate::luau::luau_update),
            )
            .add_systems(
                Update,
                script::apply_profiler_commands.after(crate::luau::luau_update),
            )
            .add_systems(Update, script::profiler_keys_system)
            .add_systems(Update, (record_frame_time, profiler_overlay_update).chain());
    }
}

fn count_frames(mut counter: ResMut<FrameCounter>) {
    counter.0 += 1;
}

/// Espelha `ProfilerState.frozen` no átomo do `timed` (corre no `First`,
/// antes de qualquer sistema instrumentado registar amostras).
fn sync_frozen_state(state: Res<ProfilerState>) {
    timed::set_frozen(state.frozen);
}

fn record_frame_time(time: Res<Time>, mut stats: ResMut<FrameStats>) {
    stats.push(time.delta_secs() * 1000.0);
}

fn spawn_overlay(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                // baixo-direita: o minimapa/barras ocupam o topo
                bottom: Val::Px(8.0),
                right: Val::Px(8.0),
                padding: UiRect::all(Val::Px(8.0)),
                flex_direction: FlexDirection::Column,
                border_radius: BorderRadius::all(Val::Px(6.0)),
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.06, 0.06, 0.06, 0.72)),
            Name::new("profiler"),
            ProfilerOverlay,
        ))
        .with_children(|panel| {
            panel.spawn((
                Text::new(""),
                TextColor(Color::srgba(0.86, 0.90, 0.82, 1.0)),
                TextFont::from_font_size(13.0),
                ProfilerText,
            ));
        });
}

/// Toggle **F3** do overlay mínimo (a janela completa é **P**, no HUD) +
/// reescrita do corpo (throttle `OVERLAY_REFRESH`). Sem menus abertos.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn profiler_overlay_update(
    mut state: ResMut<HudState>,
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
    menus: Res<crate::menus::MenusOpen>,
    mut q_text: Query<&mut Text, With<ProfilerText>>,
    mut q_root: Query<&mut Visibility, With<ProfilerOverlay>>,
    stats: Res<FrameStats>,
    diagnostics: Option<Res<DiagnosticsStore>>,
    players: Query<&GlobalTransform, With<Player>>,
    scripts: Query<(Option<&GlobalTransform>, Option<&ScriptActivation>), With<LuaScriptRef>>,
    entities: Query<Entity>,
    emitters: Query<(), With<ParticleEmitter>>,
    chunks: Query<(), With<TerrainChunk>>,
) {
    if keys.just_pressed(KeyCode::F3) && !menus.any() {
        state.visible = !state.visible;
    }
    let wanted = if state.visible {
        Visibility::Inherited
    } else {
        Visibility::Hidden
    };
    for mut vis in &mut q_root {
        if *vis != wanted {
            *vis = wanted;
        }
    }
    if !state.visible {
        return;
    }

    state.timer += time.delta_secs();
    if state.timer < OVERLAY_REFRESH {
        return;
    }
    state.timer = 0.0;

    let fps = diagnostics
        .as_deref()
        .map(diagnostics_snapshot)
        .and_then(|(fps, _)| fps);
    let frame_stats = (stats.avg_ms(), stats.max_ms(), stats.min_fps());

    let player_pos = players.iter().next().map(GlobalTransform::translation);
    let rows: Vec<ScriptRow> = scripts
        .iter()
        .map(|(t, a)| (t.map(GlobalTransform::translation), a.map(|act| act.radius)))
        .collect();
    let counters = GameCounters {
        entities: entities.iter().count(),
        scripts_total: rows.len(),
        scripts_active: count_active_scripts(player_pos, &rows),
        particle_emitters: emitters.iter().count(),
        terrain_chunks: chunks.iter().count(),
        ..Default::default()
    };
    let body = overlay_body(fps, frame_stats, &counters, time.elapsed_secs());
    if let Ok(mut text) = q_text.single_mut() {
        *text = Text::new(body);
    }
}

/// Corpo do overlay — pura para os testes (uma linha por métrica).
pub fn overlay_body(
    fps: Option<f64>,
    stats: (Option<f32>, Option<f32>, Option<f32>),
    counters: &GameCounters,
    uptime_s: f32,
) -> String {
    let fps_text = fps
        .map(|v| format!("FPS {v:.0}"))
        .unwrap_or_else(|| "FPS —".into());
    let min_text = stats
        .2
        .map(|v| format!("  pior {v:.0}"))
        .unwrap_or_default();
    let frame_text = stats
        .0
        .map(|avg| format!("frame {avg:.1} ms"))
        .unwrap_or_else(|| "frame —".into());
    let max_text = stats
        .1
        .map(|v| format!("  pico {v:.1}"))
        .unwrap_or_default();
    format!(
        "{fps_text}{min_text}\n{frame_text}{max_text}\nentidades {}\nscripts {} (ativos {})\npartículas {} emissores\nterreno {} chunks\nuptime {uptime_s:.0} s\nF3 esconde",
        counters.entities,
        counters.scripts_total,
        counters.scripts_active,
        counters.particle_emitters,
        counters.terrain_chunks,
    )
}

#[cfg(test)]
mod tests {

    /// Os paths do `RenderDiagnosticsPlugin` chegam como
    /// `render/<span>/elapsed_cpu|elapsed_gpu`; tudo o resto no store (fps,
    /// frame_time, contagens de entidades) tem de ficar de fora do `gpu[]`.
    #[test]
    fn render_paths_split_into_span_and_field() {
        assert_eq!(
            split_render_path("render/main_opaque_pass_3d/elapsed_gpu"),
            Some(("main_opaque_pass_3d", "elapsed_gpu"))
        );
        assert_eq!(
            split_render_path("render/bloom/downsample/elapsed_cpu"),
            Some(("bloom/downsample", "elapsed_cpu"))
        );
        assert_eq!(split_render_path("fps"), None);
        assert_eq!(split_render_path("render/sem_campo"), None);
    }

    /// `VIBER_PROF_SYSTEMS` só muda o CORTE da lista — o default fica nos 30
    /// (o teste não toca no env: correm testes em paralelo no mesmo processo).
    #[test]
    fn systems_limit_defaults_to_thirty() {
        if std::env::var_os("VIBER_PROF_SYSTEMS").is_none() {
            assert_eq!(systems_limit(), DEFAULT_SYSTEMS_LIMIT);
        }
    }
    use super::*;

    #[test]
    fn test_frame_stats_window() {
        let mut stats = FrameStats::default();
        assert_eq!(stats.avg_ms(), None);
        for ms in [16.0f32, 18.0, 20.0] {
            stats.push(ms);
        }
        assert!((stats.avg_ms().unwrap() - 18.0).abs() < 1e-4);
        assert!((stats.max_ms().unwrap() - 20.0).abs() < 1e-4);
        assert!((stats.min_ms().unwrap() - 16.0).abs() < 1e-4);
        assert!((stats.p95_ms().unwrap() - 20.0).abs() < 1e-4);
        assert!((stats.min_fps().unwrap() - 50.0).abs() < 1e-3);
        // janela evicta: 180+ amostras de 1 ms apagam os valores antigos
        for _ in 0..(FRAME_WINDOW + 40) {
            stats.push(1.0);
        }
        assert!((stats.avg_ms().unwrap() - 1.0).abs() < 1e-4);
    }

    #[test]
    fn test_frame_stats_min_fps_guards_zero() {
        let mut stats = FrameStats::default();
        stats.push(0.0);
        assert_eq!(stats.min_fps(), Some(0.0), "ms=0 não pode virar infinito");
    }

    fn row(x: f32, radius: Option<f32>) -> ScriptRow {
        (Some(Vec3::new(x, 0.0, 0.0)), radius)
    }

    #[test]
    fn test_active_scripts_radius() {
        let scripts = vec![
            row(10.0, Some(45.0)),  // dentro
            row(100.0, Some(45.0)), // fora
            row(500.0, None),       // sem raio: sempre activo
        ];
        let player = Some(Vec3::ZERO);
        assert_eq!(count_active_scripts(player, &scripts), 2);
        // sem player: só os sem-raio contam (congelamento total)
        assert_eq!(count_active_scripts(None, &scripts), 1);
    }

    #[test]
    fn test_overlay_body_lines() {
        let counters = GameCounters {
            entities: 15432,
            scripts_total: 96,
            scripts_active: 12,
            particle_emitters: 35,
            terrain_chunks: 481,
            ..Default::default()
        };
        let body = overlay_body(
            Some(60.4),
            (Some(16.6), Some(32.1), Some(41.3)),
            &counters,
            123.4,
        );
        assert!(body.contains("FPS 60"), "{body}");
        assert!(body.contains("pior 41"), "{body}");
        assert!(body.contains("frame 16.6 ms"), "{body}");
        assert!(body.contains("entidades 15432"), "{body}");
        assert!(body.contains("scripts 96 (ativos 12)"), "{body}");
        assert!(body.contains("F3"), "{body}");
    }

    #[test]
    fn test_snapshot_from_world() {
        let mut world = World::new();
        world.init_resource::<FrameStats>();
        world.init_resource::<DiagnosticsStore>();
        world.init_resource::<Time>();
        world.init_resource::<FrameCounter>();
        // dois scripts sem player: o de raio fica inactivo, o sem raio activo
        world.spawn((
            GlobalTransform::default(),
            LuaScriptRef {
                path: "slime.lua".into(),
            },
            ScriptActivation::default(),
        ));
        world.spawn((
            GlobalTransform::default(),
            LuaScriptRef {
                path: "well.lua".into(),
            },
        ));

        let snap = snapshot(&mut world);
        assert_eq!(snap["scripts"]["total"], 2, "{snap}");
        assert_eq!(snap["scripts"]["active"], 1, "{snap}");
        assert!(snap["uptime_s"].as_f64().is_some(), "{snap}");
        assert!(snap["frame_ms"]["avg"].is_null(), "sem amostras ainda");
        assert_eq!(snap["frame_count"], 0);
        assert_eq!(snap["frozen"], false);
        assert!(snap["groups"].is_array());
        assert!(snap["systems"].is_array());

        // entidades: recursos vivem como entidades internas no Bevy 0.16+, por
        // isso o teste compara o DELTA (agnóstico aos internals)
        let before = snap["entities"].as_u64().expect("entities numérico");
        world.spawn((
            GlobalTransform::default(),
            LuaScriptRef { path: "x".into() },
        ));
        let after = snapshot(&mut world)["entities"].as_u64().expect("numérico");
        assert_eq!(after - before, 1, "+1 entidade por spawn");
    }

    #[test]
    fn test_tab_snapshot_world_and_fallback() {
        let mut world = World::new();
        world.init_resource::<Time>();
        world.init_resource::<FrameStats>();
        world.init_resource::<FrameCounter>();
        world.insert_resource(ProfilerState::default());
        let world_snap = tab_snapshot(&mut world, "world");
        assert!(world_snap["entity_count"].is_u64(), "{world_snap}");
        // tab desconhecido → snapshot de sistemas (compat).
        let fallback = tab_snapshot(&mut world, "wat");
        assert!(fallback["entities"].is_u64(), "{fallback}");
    }

    #[test]
    fn test_export_to_file() {
        let mut world = World::new();
        world.init_resource::<Time>();
        world.init_resource::<FrameStats>();
        world.init_resource::<FrameCounter>();
        // full_snapshot lê o estado (aba/congelação) — o teste cobre o caminho.
        world.init_resource::<ProfilerState>();
        let dir = tempfile::tempdir().expect("tmpdir");
        let path = dir.path().join("prof.json");
        let written = export_to_file(&mut world, Some(path.clone())).expect("export");
        assert_eq!(written, path);
        let text = std::fs::read_to_string(&path).expect("ler export");
        let value: serde_json::Value = serde_json::from_str(&text).expect("json válido");
        assert_eq!(value["tool"], "viber");
        assert!(value["tabs"]["systems"].is_object(), "{value}");
        assert!(value["tabs"]["world"].is_object());
        assert!(value["tabs"]["physics"].is_object());
        assert!(value["tabs"]["audio"].is_object());
        assert!(value["extras"].is_array());
    }
}
